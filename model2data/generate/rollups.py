"""A parent's column computed from its child rows (`rollup`), and parents that all have children (`at_least`).

`rollup` sits on a parent's column and names a child table and one aggregate
over the child rows whose foreign key points at the row:

    orders.total_amount:   {"rollup": {"of": "order_items", "sum": ["quantity", "unit_price"]}}
    customers.order_count: {"rollup": {"of": "orders", "count": true}}

- `sum: <column>` adds the column up; `sum: [a, b, ...]` multiplies the columns
  on each child row and adds the products (Σ quantity × unit_price). A child row
  with a null in any of them adds nothing. The sum is rounded once per parent,
  to the most decimal places any summed column has (`numeric(10,2)` has 2, an
  integer 0, a decimal type without a scale the generator's 2).
- `count: true` counts the child rows; `count_distinct: <column>` the distinct
  non-null values of a column of them.
- `min: <column>` / `max: <column>`: the lowest or highest non-null value, of a
  numeric, date or timestamp column.
- A parent with no child rows holds 0 for a sum or a count, and null for min
  and max.
- The child table reaches the parent through exactly one foreign key onto the
  parent's primary key or a unique column, or `via` names the one to use.

Rollups are computed after every table is generated (and after the foreign
keys an FK cycle deferred), a rollup that reads another rollup's column after
that one. They draw nothing from any random stream, so no other column changes
and the column itself replaces the value the generator first drew for it.

`at_least: N` sits on a child's foreign key: every parent row gets at least N
child rows. How (`cover_parents`), from the table's own stream:

1. The distinct parents, in the order the parent holds them, are shuffled, and
   each is listed N times: the coverage, N × parents values. With fewer rows
   than that, the first rows' worth of the list is kept (so the parents first
   in the shuffle are covered fully), and the shortfall is reported, not
   raised.
2. The rest of the rows are drawn exactly as the column draws without the hint
   (its skew, its null rate).
3. The coverage values are shuffled, and placed at positions drawn at random
   without replacement; the rest fill the other positions in order. Nothing
   later in generation moves a coverage row to another parent or nulls it: a
   cross-table `after` moves such a row's date instead, and a composite key's
   repair redraws its other columns.
"""

from __future__ import annotations

import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
import pandas as pd

from model2data.generate import kinds
from model2data.generate.derive import derivation_reads
from model2data.generate.relationships import _key_kind, build_fk_lookup, classify_refs
from model2data.parse.dbml import ColumnDef, TableDef

AGGREGATES = ("sum", "count", "count_distinct", "min", "max")
_ROLLUP_KEYS = ("of", "via", *AGGREGATES)
# Hints that would decide the value a rollup decides.
CONFLICTS = (
    "min",
    "max",
    "distribution",
    "mean",
    "stddev",
    "median",
    "spread",
    "weights",
    "null_rate",
    "distinct",
    "true_rate",
    "skew",
    "after",
    "growth",
    "seasonality",
    "business_hours",
    "days",
    "times",
    "when",
    "derive",
)
_SCALE = re.compile(r"\(\s*\d+\s*,\s*(\d+)\s*\)")
# The places the generator rounds a decimal column without a declared scale to.
_DEFAULT_DECIMAL_SCALE = 2


@dataclass(frozen=True)
class Rollup:
    """One `rollup`: `table.column` is `aggregate` over `child`'s rows, grouped by `fk`.

    `fk` is the child's foreign-key column, `parent_key` the column of `table`
    it references. `columns` are the child columns aggregated (empty for
    `count`), `scale` the decimal places a sum is rounded to.
    """

    table: str
    column: str
    child: str
    fk: str
    parent_key: str
    aggregate: str
    columns: tuple[str, ...]
    scale: int


@dataclass(frozen=True)
class Problem:
    """What is wrong with a `rollup` or `at_least`: on `table.column`, at its `hint`.

    `hint` is the key of the column's generate block the problem is at
    (`rollup`, `at_least`, `when`, `after`), or `references` for a reference.
    """

    table: str
    column: str
    hint: str
    message: str
    warning: bool = False


def has_rollups(tables: Mapping[str, TableDef]) -> bool:
    return any(_rollup_note(column) is not None for t in tables.values() for column in t.columns)


def _rollup_note(column: ColumnDef) -> Any:
    note = column.note if isinstance(column.note, dict) else {}
    return note.get("rollup")


def at_least_of(column: ColumnDef) -> int:
    """The column's `at_least`, 0 when it has none."""
    note = column.note if isinstance(column.note, dict) else {}
    value = note.get("at_least")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def column_scale(column: ColumnDef) -> int:
    """The decimal places a column's values have: 0 for an integer, the declared scale, else 2."""
    if kinds.is_integer_type(column.data_type):
        return 0
    found = _SCALE.search(column.data_type)
    return int(found.group(1)) if found else _DEFAULT_DECIMAL_SCALE


def _aggregate_columns(spec: Mapping) -> tuple[Optional[str], list]:
    """The rollup's aggregate and the child columns it names (as written)."""
    named = [key for key in AGGREGATES if key in spec]
    if len(named) != 1:
        return None, []
    aggregate = named[0]
    value = spec[aggregate]
    if aggregate == "count":
        return aggregate, []
    if aggregate == "sum" and isinstance(value, list):
        return aggregate, list(value)
    return aggregate, [value]


# ---------------------------------------------------------
# Checking
# ---------------------------------------------------------
def problems(tables: Mapping[str, TableDef], refs: list[dict]) -> list[Problem]:
    """Everything wrong with the model's `rollup` and `at_least` hints, errors and warnings."""
    fk_refs, _ = classify_refs(dict(tables), refs)
    fk_lookup = build_fk_lookup(fk_refs)
    found: list[Problem] = []
    rollups: dict[tuple[str, str], Rollup] = {}
    for table_name, table_def in tables.items():
        for column in table_def.columns:
            note = column.note if isinstance(column.note, dict) else {}
            if "at_least" in note:
                found.extend(_at_least_problems(table_name, table_def, column, fk_lookup))
            if "rollup" in note:
                rollup, issues = _rollup_problems(tables, fk_refs, fk_lookup, table_name, column)
                found.extend(issues)
                if rollup is not None:
                    rollups[(table_name, column.name)] = rollup
    if rollups:
        found.extend(_reader_problems(tables, refs, rollups))
        found.extend(_cycle_problems(rollups))
    return found


def _at_least_problems(
    table_name: str,
    table_def: TableDef,
    column: ColumnDef,
    fk_lookup: Mapping[tuple[str, str], tuple[str, str]],
) -> list[Problem]:
    value = (column.note or {})["at_least"]

    def problem(message: str) -> list[Problem]:
        return [Problem(table_name, column.name, "at_least", message)]

    if (table_name, column.name) not in fk_lookup:
        return problem('"at_least" only applies to foreign-key columns.')
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return problem(f'"at_least" must be a whole number of 1 or more (got {value!r}).')
    if (
        "unique" in column.settings
        or "pk" in column.settings
        or _single_unique_key(table_def, column.name)
    ):
        return problem(
            '"at_least" cannot sit on a unique foreign key: a one-to-one takes each parent at '
            "most once, so no parent can be given several rows."
        )
    return []


def _single_unique_key(table_def: TableDef, name: str) -> bool:
    return any(
        key.get("columns") == [name] and key.get("type") in ("pk", "unique")
        for key in table_def.composite_keys
    )


def _in_key(table_def: TableDef, column: ColumnDef) -> bool:
    return (
        "pk" in column.settings
        or "unique" in column.settings
        or _single_unique_key(table_def, column.name)
    )


def _rollup_problems(
    tables: Mapping[str, TableDef],
    fk_refs: list[dict],
    fk_lookup: Mapping[tuple[str, str], tuple[str, str]],
    table_name: str,
    column: ColumnDef,
) -> tuple[Optional[Rollup], list[Problem]]:
    note = column.note or {}
    spec = note["rollup"]
    issues: list[Problem] = []

    def fail(message: str, warning: bool = False) -> None:
        issues.append(Problem(table_name, column.name, "rollup", message, warning))

    if not isinstance(spec, Mapping):
        fail(
            '"rollup" must be an object naming the child table (`of`) and one aggregate '
            f"({', '.join(AGGREGATES)})."
        )
        return None, issues
    unknown = [key for key in spec if key not in _ROLLUP_KEYS]
    if unknown:
        fail(
            f'"rollup" has no {unknown[0]!r}: it holds `of`, `via` and one aggregate '
            f"({', '.join(AGGREGATES)})."
        )
    conflicts = [hint for hint in CONFLICTS if hint in note]
    if conflicts:
        named = ", ".join(f'"{hint}"' for hint in conflicts)
        fail(f'"rollup" decides the value, so the column cannot also have {named}.')
    table_def = tables[table_name]
    if _in_key(table_def, column) or any(
        column.name in (key.get("columns") or []) for key in table_def.composite_keys
    ):
        fail('"rollup" cannot sit on a primary key, a unique column or a column of a key.')
    if (table_name, column.name) in fk_lookup:
        fail('"rollup" cannot sit on a foreign key: its values are the parent\'s keys.')
    if column.enum_values:
        fail('"rollup" cannot sit on an enum column.')

    aggregate, named = _aggregate_columns(spec)
    if aggregate is None:
        count = sum(key in spec for key in AGGREGATES)
        fail(
            f'"rollup" needs exactly one aggregate ({", ".join(AGGREGATES)}), and it has '
            f"{'none' if count == 0 else count}."
        )
    child = spec.get("of")
    if not isinstance(child, str) or child not in tables:
        fail(
            f'"rollup" names the table {child!r} in `of`, which is not in the model.'
            if isinstance(child, str)
            else '"rollup" needs `of`: the child table whose rows it aggregates.'
        )
        return None, issues
    child_def = tables[child]
    child_columns = {c.name: c for c in child_def.columns}

    link = _child_link(tables, fk_refs, table_name, child, spec.get("via"))
    if isinstance(link, str):
        fail(link)
    if aggregate is None or issues:
        return None, issues
    assert not isinstance(link, str)
    fk, parent_key = link

    value = spec[aggregate]
    if aggregate == "count":
        if value is not True:
            fail(f'"rollup" `count` must be true (got {value!r}).')
    elif aggregate == "sum":
        if not (isinstance(value, str) or (isinstance(value, list) and value)) or (
            isinstance(value, list) and not all(isinstance(v, str) for v in value)
        ):
            fail(f'"rollup" `sum` must be a column of {child}, or a list of them (got {value!r}).')
    elif not isinstance(value, str):
        fail(f'"rollup" `{aggregate}` must be a column of {child} (got {value!r}).')
    if issues:
        return None, issues
    for name in named:
        if name not in child_columns:
            fail(f'"rollup" `{aggregate}` names {name!r}, which is not a column of {child}.')
    if issues:
        return None, issues

    own = kinds.base_type(column.data_type)
    own_integer = kinds.is_integer_type(own)
    own_numeric = kinds.is_numeric_type(own)
    summed = [child_columns[name] for name in named]
    if aggregate in ("count", "count_distinct"):
        if not own_integer:
            fail(
                f'"rollup" `{aggregate}` counts rows, so {table_name}.{column.name} must be an '
                f"integer column ({column.data_type} is not one)."
            )
    elif aggregate == "sum":
        for other in summed:
            if other.enum_values or not kinds.is_numeric_type(other.data_type):
                fail(
                    f'"rollup" `sum` names {other.name}, and {child}.{other.name} '
                    f"({other.data_type}) is not numeric."
                )
        if not issues and not own_numeric:
            fail(
                f'"rollup" `sum` adds numbers, so {table_name}.{column.name} must be numeric '
                f"({column.data_type} is not)."
            )
        if (
            not issues
            and own_integer
            and any(kinds.is_decimal_type(other.data_type) for other in summed)
        ):
            decimal = next(o.name for o in summed if kinds.is_decimal_type(o.data_type))
            fail(
                f'"rollup" `sum` adds {child}.{decimal}, a decimal column, and '
                f"{table_name}.{column.name} is an integer column: make it numeric."
            )
    else:  # min, max
        (other,) = summed
        other_kind = _ordered_kind(other)
        if other_kind is None:
            fail(
                f'"rollup" `{aggregate}` names {other.name}, and {child}.{other.name} '
                f"({other.data_type}) is not a numeric, date or timestamp column."
            )
        elif other_kind == "numeric":
            if not own_numeric or column.enum_values:
                fail(
                    f'"rollup" `{aggregate}` of {child}.{other.name}, a number, needs '
                    f"{table_name}.{column.name} to be numeric ({column.data_type} is not)."
                )
            elif own_integer and kinds.is_decimal_type(other.data_type):
                fail(
                    f'"rollup" `{aggregate}` of {child}.{other.name}, a decimal column, needs '
                    f"{table_name}.{column.name} to be a decimal column, not an integer one."
                )
        elif kinds.temporal_kind(column.data_type) != other_kind or column.enum_values:
            fail(
                f'"rollup" `{aggregate}` of {child}.{other.name}, a {other_kind}, needs '
                f"{table_name}.{column.name} to be a {other_kind} column too "
                f"({column.data_type} is not)."
            )
        if not issues and "not null" in column.settings:
            fk_column = child_columns[fk]
            if at_least_of(fk_column) < 1:
                fail(
                    f'"rollup" `{aggregate}` is null on a {table_name} row with no {child} rows, '
                    f"and the column is not null: add `at_least: 1` to {child}.{fk} so every "
                    f"{table_name} row has one.",
                    warning=True,
                )
    if any(not problem.warning for problem in issues):
        return None, issues
    scale = max((column_scale(c) for c in summed), default=0) if aggregate == "sum" else 0
    rollup = Rollup(
        table=table_name,
        column=column.name,
        child=child,
        fk=fk,
        parent_key=parent_key,
        aggregate=aggregate,
        columns=tuple(named),
        scale=scale,
    )
    return rollup, issues


def _ordered_kind(column: ColumnDef) -> Optional[str]:
    if column.enum_values:
        return None
    if kinds.is_numeric_type(column.data_type):
        return "numeric"
    return kinds.temporal_kind(column.data_type)


def _child_link(
    tables: Mapping[str, TableDef],
    fk_refs: list[dict],
    parent: str,
    child: str,
    via: Any,
) -> "tuple[str, str] | str":
    """`(child fk column, parent key)` the rollup groups by, or what is wrong."""
    onto = [r for r in fk_refs if r["source_table"] == child and r["target_table"] == parent]
    if via is not None:
        if not isinstance(via, str):
            return f'"rollup" `via` must be a column of {child} (got {via!r}).'
        chosen = [r for r in onto if r["source_column"] == via]
        if not chosen:
            return (
                f'"rollup" `via` names {via!r}, which is not a foreign key of {child} onto '
                f"{parent}."
            )
        ref = chosen[0]
    elif not onto:
        return f'"rollup" reads {child}, which has no foreign key onto {parent}.'
    elif len(onto) > 1:
        names = ", ".join(r["source_column"] for r in onto)
        return (
            f'"rollup" reads {child}, which has {len(onto)} foreign keys onto {parent} '
            f"({names}): name the one to group by with `via`."
        )
    else:
        ref = onto[0]
    if _key_kind(dict(tables), ref) not in ("pk", "unique"):
        return (
            f'"rollup" groups by {child}.{ref["source_column"]}, which references '
            f"{parent}.{ref['target_column']}, not a key: a rollup needs the one {parent} row "
            "the foreign key points at."
        )
    return ref["source_column"], ref["target_column"]


def _reader_problems(
    tables: Mapping[str, TableDef], refs: list[dict], rollups: Mapping[tuple[str, str], Rollup]
) -> list[Problem]:
    """A column that reads a rolled-up column while tables are generated: refused."""
    found: list[Problem] = []

    def why(target: tuple[str, str]) -> str:
        return (
            f"{target[0]}.{target[1]}, a rollup: it is computed once every table is generated, "
            "so nothing generated reads it"
        )

    for table_name, table_def in tables.items():
        for column in table_def.columns:
            note = column.note if isinstance(column.note, dict) else {}
            when = note.get("when")
            if isinstance(when, Mapping):
                for other in when:
                    if (table_name, other) in rollups:
                        found.append(
                            Problem(
                                table_name,
                                column.name,
                                "when",
                                f'"when" names {why((table_name, other))}.',
                            )
                        )
            derive = note.get("derive")
            if isinstance(derive, Mapping):
                for other in derivation_reads(derive):
                    if (table_name, other) in rollups:
                        found.append(
                            Problem(
                                table_name,
                                column.name,
                                "derive",
                                f'"derive" reads {why((table_name, other))}.',
                            )
                        )
            after = note.get("after")
            for entry in after if isinstance(after, list) else [after]:
                if not isinstance(entry, str):
                    continue
                target = tuple(entry.rsplit(".", 1)) if "." in entry else (table_name, entry)
                if target in rollups:
                    found.append(
                        Problem(table_name, column.name, "after", f'"after" names {why(target)}.')
                    )
    for ref in refs:
        target = (ref["target_table"], ref["target_column"])
        if target in rollups:
            found.append(
                Problem(
                    ref["source_table"],
                    ref["source_column"],
                    "references",
                    f"references {why(target)}, and a reference is copied while it is.",
                )
            )
    return found


def _cycle_problems(rollups: Mapping[tuple[str, str], Rollup]) -> list[Problem]:
    """Rollups that read each other, directly or around a loop: none can go first."""
    found = []
    for key, rollup in rollups.items():
        seen: set = set()
        stack = list(_reads(rollup, rollups))
        while stack:
            current = stack.pop()
            if current == key:
                found.append(
                    Problem(
                        rollup.table,
                        rollup.column,
                        "rollup",
                        '"rollup" reads a rollup that reads it back: neither can be computed '
                        "first.",
                    )
                )
                break
            if current in seen:
                continue
            seen.add(current)
            stack.extend(_reads(rollups[current], rollups))
    return found


def _reads(rollup: Rollup, rollups: Mapping[tuple[str, str], Rollup]) -> list[tuple[str, str]]:
    """The rollups whose columns `rollup` aggregates (or groups by)."""
    return [
        (rollup.child, name)
        for name in (*rollup.columns, rollup.fk)
        if (rollup.child, name) in rollups
    ]


# ---------------------------------------------------------
# Computing
# ---------------------------------------------------------
def rollups_of(tables: Mapping[str, TableDef], refs: list[dict]) -> list[Rollup]:
    """The model's valid rollups, each after every rollup it reads."""
    found: dict[tuple[str, str], Rollup] = {}
    fk_refs, _ = classify_refs(dict(tables), refs)
    fk_lookup = build_fk_lookup(fk_refs)
    for table_name, table_def in tables.items():
        for column in table_def.columns:
            if _rollup_note(column) is None:
                continue
            rollup, _ = _rollup_problems(tables, fk_refs, fk_lookup, table_name, column)
            # None only for a hint `validate_hints` refuses, which a caller that
            # did not validate (a dbt export of bare tables) leaves out.
            if rollup is not None:
                found[(table_name, column.name)] = rollup
    ordered: list[Rollup] = []
    placed: set = set()

    def place(key: tuple[str, str]) -> None:
        if key in placed:
            return
        placed.add(key)
        for read in _reads(found[key], found):
            place(read)
        ordered.append(found[key])

    for key in found:
        place(key)
    return ordered


def apply_rollups(
    generated: dict[str, pd.DataFrame],
    tables: Mapping[str, TableDef],
    refs: list[dict],
) -> set[str]:
    """Write every rollup into `generated`, in place; returns the tables changed.

    Draws nothing from any random stream.
    """
    changed: set[str] = set()
    for rollup in rollups_of(tables, refs):
        parent = generated[rollup.table]
        child = generated[rollup.child]
        column = next(c for c in tables[rollup.table].columns if c.name == rollup.column)
        values = rolled_up(rollup, parent, child)
        if kinds.is_integer_type(column.data_type):
            parent[rollup.column] = pd.array(values, dtype="Int64")
        else:
            parent[rollup.column] = pd.Series(values, index=parent.index, dtype=object)
            if rollup.aggregate == "sum" or (
                rollup.aggregate in ("min", "max") and kinds.is_numeric_type(column.data_type)
            ):
                parent[rollup.column] = parent[rollup.column].astype("float64")
        changed.add(rollup.table)
    return changed


def rolled_up(rollup: Rollup, parent: pd.DataFrame, child: pd.DataFrame) -> list:
    """The rollup's value on each row of `parent`, from the rows of `child`."""
    keys = _plain(child[rollup.fk])
    present = [key is not None for key in keys]
    frame = pd.DataFrame({"key": keys}, dtype=object)[present]
    aggregate = rollup.aggregate
    if aggregate == "count":
        by_key: dict = frame.groupby("key", sort=False).size().to_dict()
        empty: Any = 0
    elif aggregate == "sum":
        product = np.ones(len(child), dtype="float64")
        known = np.ones(len(child), dtype=bool)
        for name in rollup.columns:
            numbers = pd.to_numeric(child[name], errors="coerce").astype("float64").to_numpy()
            known &= ~np.isnan(numbers)
            product = product * np.nan_to_num(numbers)
        frame["value"] = np.where(known, product, 0.0)[present]
        sums = frame.groupby("key", sort=False)["value"].sum().to_dict()
        by_key = {key: _rounded(total, rollup.scale) for key, total in sums.items()}
        empty = 0 if rollup.scale == 0 else 0.0
    else:
        (name,) = rollup.columns
        values = _plain(child[name])
        frame["value"] = pd.Series(values, dtype=object)[present]
        frame = frame[[value is not None for value in frame["value"]]]
        if aggregate == "count_distinct":
            by_key = {
                key: len(set(group)) for key, group in frame.groupby("key", sort=False)["value"]
            }
            empty = 0
        else:
            pick = min if aggregate == "min" else max
            by_key = {key: pick(group) for key, group in frame.groupby("key", sort=False)["value"]}
            empty = None
    return [by_key.get(key, empty) for key in _plain(parent[rollup.parent_key])]


def _plain(values: pd.Series) -> list:
    """Values as plain Python ones (`tolist` unboxes them), a missing one (NA, NaN) as None."""
    return [None if _missing(value) else value for value in values.tolist()]


def _missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _rounded(total: float, scale: int) -> Any:
    """A per-parent sum rounded once, to `scale` places: an int at 0."""
    if scale == 0:
        return int(round(total))
    return round(float(total), scale)


# ---------------------------------------------------------
# at_least
# ---------------------------------------------------------
@dataclass
class Coverage:
    """What `cover_parents` did: the positions it gave a parent, and the shortfall.

    `positions` are the row positions of the coverage rows, in ascending order.
    `parents` is how many distinct parents the column could take, `covered` how
    many got their `at_least` rows, `needed` the rows that would have covered
    every one.
    """

    positions: list[int]
    parents: int
    covered: int
    needed: int


def cover_parents(
    values_for: Any,
    fk_series: pd.Series,
    row_count: int,
    at_least: int,
) -> tuple[list, Coverage]:
    """`row_count` foreign-key values, every parent at least `at_least` times (as rows allow).

    `values_for(n)` draws `n` values the way the column draws without the hint.
    All draws come from the `random` stream the caller has seeded.
    """
    parents = list(dict.fromkeys(value for value in fk_series.tolist() if not _missing(value)))
    needed = at_least * len(parents)
    cover = min(needed, row_count)
    order = list(parents)
    random.shuffle(order)
    coverage = [parent for parent in order for _ in range(at_least)][:cover]
    rest = list(values_for(row_count - cover))
    random.shuffle(coverage)
    positions = sorted(random.sample(range(row_count), cover))
    values: list = [None] * row_count
    taken = set(positions)
    for position, value in zip(positions, coverage, strict=True):
        values[position] = value
    others = iter(rest)
    for position in range(row_count):
        if position not in taken:
            values[position] = next(others)
    covered = len(parents) if cover == needed else cover // at_least
    return values, Coverage(positions, len(parents), covered, needed)


def protected_rows(coverage: Mapping[str, Coverage], length: int) -> dict[str, np.ndarray]:
    """Per foreign-key column, a mask of the rows `cover_parents` gave a parent."""
    masks = {}
    for column, cover in coverage.items():
        mask = np.zeros(length, dtype=bool)
        mask[cover.positions] = True
        masks[column] = mask
    return masks
