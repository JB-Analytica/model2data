"""`derive`: a column computed from other columns of its row (spec 0.7.0).

A derived column draws nothing: no random draw is made for it, so it never moves a
stream, and its value is what its derivation says over the values of the columns it
reads, whatever those hold once the table is done.

    winner_team_id:
      generate:
        when: {status: [final]}
        derive:
          cases:
            - if: {home_score: {gt: {column: away_score}}}
              then: {column: home_team_id}
          else: {column: away_team_id}
    game_date: {generate: {derive: {date_of: tip_off_at}}}
    status:
      generate:
        derive:
          cases: [{if: {tip_off_at: {lte: {run: as_of}}}, then: final}]
          else: scheduled

- `cases`: each `{if: <comparison>, then: <value>}` in order, the first whose `if` holds
  on the row giving the value, and `else` the value when none does (null when left out).
  An `if` is a comparison mapping (`generate.comparison`), all of whose entries must
  hold. A value is a literal, `{column: x}` (the row's value of x) or `{run: as_of}`.
- `copy: x`: the row's value of x. `date_of: x`: the date part of the timestamp x.
- `when` on a derived column says where it is null; the derivation decides the value
  where it is not.

The order. A table is generated in this order (see `generate.core`): every column that is
not derived is drawn, then the ordering of a row's dates, the repairs of its keys and the
copies of a parent's attributes; then `settle` computes the derived columns and applies
the `when`s that read them or that they read, in dependency order (a derived column may
read another, and a `when` may name one); then the cross-table `after` is held and the
table's `rules` are met -- a rule redraws the columns it reads that are not derived, and
the derived columns and `when`s are settled again after every round so a rule sees what its
columns hold -- and last, `settle` once more, so that what the table holds is final:

1. every derived value is its derivation over the final values of the columns it reads;
2. every `when` is applied on the final values of the columns it names, derived ones included;
3. every rule holds where it can.

A rollup is computed after every table, from the final rows, so a rollup over a derived
column reads its final values; a derived column may not read a rollup.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from model2data.generate import comparison as cmp
from model2data.generate.rules import column_kind
from model2data.generate.when import apply_when, conditions, null_unmatched
from model2data.parse.dbml import ColumnDef, TableDef

# Hints that would decide a value the derivation decides.
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
    "transitions",
    "rollup",
    "at_least",
    "at_most",
)
FORMS = ("cases", "copy", "date_of")
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


class _Nothing:
    """The `else` a derivation leaves out: the value is null."""

    def __repr__(self) -> str:
        return "<null>"


NOTHING = _Nothing()


@dataclass(frozen=True)
class Case:
    """One `{if, then}`: the comparisons that must all hold, and the value then."""

    when: tuple[cmp.Comparison, ...]
    then: Any


@dataclass(frozen=True)
class Derivation:
    """What a derived column is: `cases` (with `otherwise`), a `copy` or a `date_of`."""

    cases: tuple[Case, ...] = ()
    otherwise: Any = NOTHING
    copy: Optional[str] = None
    date_of: Optional[str] = None

    def reads(self) -> list[str]:
        """Every column the derivation reads, once, in order of appearance."""
        names: list[str] = []
        if self.copy is not None:
            names.append(self.copy)
        if self.date_of is not None:
            names.append(self.date_of)
        for case in self.cases:
            for comparison in case.when:
                names.extend(comparison.columns())
            names.extend(_value_columns(case.then))
        names.extend(_value_columns(self.otherwise))
        return list(dict.fromkeys(names))


def _value_columns(value: Any) -> list[str]:
    name = cmp.column_operand(value)
    return [name] if name is not None else []


def spec_of(column: ColumnDef) -> Optional[Mapping]:
    """The column's `derive` hint, or None when it has none."""
    spec = (column.note or {}).get("derive")
    return spec if isinstance(spec, Mapping) else None


def derivation_reads(spec: Mapping) -> list[str]:
    """The columns a `derive` hint reads, in a hint that may be malformed (nothing is raised)."""
    try:
        return parse(spec).reads()
    except (AttributeError, TypeError, KeyError):
        return []


def is_derived(column: ColumnDef) -> bool:
    return "derive" in (column.note or {})


def derived_columns(table_def: TableDef) -> list[ColumnDef]:
    """The table's derived columns, in table order."""
    return [column for column in table_def.columns if is_derived(column)]


def parse(spec: Mapping) -> Derivation:
    """A `derive` hint as a `Derivation`. The shape is checked elsewhere: read a valid one."""
    if "copy" in spec:
        return Derivation(copy=spec["copy"])
    if "date_of" in spec:
        return Derivation(date_of=spec["date_of"])
    cases = tuple(
        Case(tuple(cmp.parse(case.get("if") or {})), case.get("then"))
        for case in spec.get("cases") or []
    )
    return Derivation(cases, spec["else"] if "else" in spec else NOTHING)


# ---------------------------------------------------------------------------
# The order
# ---------------------------------------------------------------------------
def dependencies(table_def: TableDef) -> dict[str, list[str]]:
    """For each derived column and each non-derived column with `when`, the columns of the
    table it reads: a derived column its derivation and its own `when`'s columns, a `when`
    column the columns it names."""
    graph: dict[str, list[str]] = {}
    for column in table_def.columns:
        reads: list[str] = []
        spec = spec_of(column)
        if spec is not None:
            reads += derivation_reads(spec)
        reads += list(conditions(column) or {})
        if spec is not None or conditions(column) is not None:
            graph[column.name] = list(dict.fromkeys(reads))
    return graph


def plan(table_def: TableDef) -> list[str]:
    """The names of the derived columns and the `when` columns, in the order to settle them.

    A column goes after the derived or `when` columns it reads, ties in table order. A
    cycle (which `validate` reports for a derived column) is left in table order.
    """
    graph = dependencies(table_def)
    position = {column.name: index for index, column in enumerate(table_def.columns)}
    waiting = {
        name: {dep for dep in reads if dep in graph and dep != name}
        for name, reads in graph.items()
    }
    order: list[str] = []
    ready = deque(sorted((n for n, deps in waiting.items() if not deps), key=position.__getitem__))
    placed: set[str] = set()
    while ready:
        name = ready.popleft()
        order.append(name)
        placed.add(name)
        freed = [
            other
            for other, deps in waiting.items()
            if other not in placed and other not in ready and deps <= placed
        ]
        for other in sorted(freed, key=position.__getitem__):
            ready.append(other)
    order += sorted((n for n in graph if n not in placed), key=position.__getitem__)
    return order


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def _is_null(value: Any) -> bool:
    return (
        value is None
        or value is pd.NaT
        or (not isinstance(value, (list, dict)) and bool(pd.isna(value)))
    )


def _as_target(value: Any, target: str) -> Any:
    """`value` as the column of kind `target` stores it, None for a null."""
    if _is_null(value):
        return None
    if target == "timestamp":
        if isinstance(value, datetime):
            return value.strftime(_TIMESTAMP_FORMAT)
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day).strftime(_TIMESTAMP_FORMAT)
        if isinstance(value, str) and len(value) == 10:
            return f"{value} 00:00:00"
        return value
    if target == "date":
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value).date()
        return value
    if target == "enum":
        return str(value)
    return value


def _run_value(name: str, anchor: datetime) -> datetime:
    if name != "as_of":
        raise ValueError(f"`{{run: {name}}}` is not a value of the run: only `as_of` is")
    return anchor


def _values(
    frame: pd.DataFrame, value: Any, rows: np.ndarray, target: str, anchor: datetime
) -> list:
    """The value of a `then`/`else` on the rows where `rows` is true: one per such row."""
    count = int(rows.sum())
    if (name := cmp.column_operand(value)) is not None:
        column = frame[name].to_numpy(dtype=object)[rows]
        return [_as_target(item, target) for item in column]
    if (run := cmp.run_operand(value)) is not None:
        return [_as_target(_run_value(run, anchor), target)] * count
    return [_as_target(value, target)] * count


def evaluate(
    frame: pd.DataFrame,
    column: ColumnDef,
    derivation: Derivation,
    kind_of: Mapping[str, str],
    anchor: datetime,
) -> list:
    """The column's value on every row of `frame`: its derivation over the row's columns."""
    target = column_kind(column)
    size = len(frame)
    out = np.empty(size, dtype=object)
    out[:] = None
    if derivation.copy is not None or derivation.date_of is not None:
        name = derivation.copy if derivation.copy is not None else derivation.date_of
        assert name is not None
        everywhere = np.ones(size, dtype=bool)
        out[everywhere] = _values(frame, {"column": name}, everywhere, target, anchor)
        return out.tolist()
    done = np.zeros(size, dtype=bool)
    for case in derivation.cases:
        held = cmp.all_hold(frame, case.when, kind_of, as_of=anchor).to_numpy(dtype=bool)
        rows = held & ~done
        if rows.any():
            out[rows] = _values(frame, case.then, rows, target, anchor)
        done |= rows
    rest = ~done
    if derivation.otherwise is not NOTHING and rest.any():
        out[rest] = _values(frame, derivation.otherwise, rest, target, anchor)
    return out.tolist()


def settle(
    frame: pd.DataFrame,
    table_def: TableDef,
    *,
    run_anchor: datetime,
    cap: datetime,
    seed_for: Callable[[str], Optional[int]],
    as_of: Any = None,
    time_profile: Any = None,
) -> None:
    """Compute the table's derived columns and apply its `when`s, in dependency order. In place.

    A derived column takes its derivation's value on every row and is null where its own
    `when` does not match; a `when` column that is not derived is nulled and filled as
    `generate.when` does. Run again after a change to a column either reads, it gives the
    same result for what did not change.
    """
    names = plan(table_def)
    if not names:
        return
    by_name = {column.name: column for column in table_def.columns}
    kind_of = {name: column_kind(column) for name, column in by_name.items()}
    for name in names:
        column = by_name[name]
        spec = spec_of(column)
        if spec is not None:
            values = evaluate(frame, column, parse(spec), kind_of, run_anchor)
            frame[name] = pd.Series(values, index=frame.index, dtype=object)
            if conditions(column) is not None:
                null_unmatched(frame, table_def, column)
        else:
            apply_when(
                frame,
                table_def,
                cap=cap,
                seed_for=seed_for,
                as_of=as_of,
                time_profile=time_profile,
                only={name},
            )


def has_derived(table_def: TableDef) -> bool:
    return any(is_derived(column) for column in table_def.columns)


def problems(table_def: TableDef) -> list[tuple[ColumnDef, str]]:
    """What is wrong with the table's `derive` hints as the engine reads them, per column.

    The structure only: the form, the columns it names, the hints and keys a derived column
    cannot be, and a loop. The typed checks (the values a column can hold, the kinds that
    go into it) are the model validation's.
    """
    found: list[tuple[ColumnDef, str]] = []
    names = {column.name for column in table_def.columns}
    keyed = {name for key in table_def.composite_keys for name in key.get("columns") or []}
    for column in derived_columns(table_def):
        spec = (column.note or {}).get("derive")

        def fail(message: str, column: ColumnDef = column) -> None:
            found.append((column, message))

        if not isinstance(spec, Mapping):
            fail('"derive" must be an object: `cases`, `copy` or `date_of`.')
            continue
        forms = [form for form in FORMS if form in spec]
        if len(forms) != 1:
            fail('"derive" needs exactly one of `cases`, `copy` and `date_of`.')
            continue
        if "pk" in column.settings or "unique" in column.settings or column.name in keyed:
            fail('"derive" cannot sit on a primary key, a unique column or a column of a key.')
        for hint in CONFLICTS:
            if hint in (column.note or {}):
                fail(f'"derive" decides the value, so the column cannot also have "{hint}".')
        if forms[0] == "cases":
            cases = spec["cases"]
            if not isinstance(cases, list) or not cases:
                fail('"derive" `cases` must be a non-empty list of {if, then} entries.')
                continue
            for case in cases:
                if not isinstance(case, Mapping) or not isinstance(case.get("if"), Mapping):
                    fail('"derive" `cases` entries need an `if` (a comparison) and a `then`.')
                    break
                if "then" not in case:
                    fail('"derive" `cases` entries need an `if` (a comparison) and a `then`.')
                    break
                for comparison in cmp.parse(case["if"]):
                    for issue in cmp.operand_problems(
                        comparison.operator, comparison.operand, allow_run=True
                    ):
                        fail(f'"derive" `if`: {issue.message}.')
        elif not isinstance(spec[forms[0]], str):
            fail(f'"derive" `{forms[0]}` must name a column.')
            continue
        for other in derivation_reads(spec):
            if other not in names:
                fail(f'"derive" reads {other!r}, which is not a column of {table_def.name}.')
            elif other == column.name:
                fail('"derive" reads the column itself.')
    graph = dependencies(table_def)
    derived = {column.name for column in derived_columns(table_def)}
    by_name = {column.name: column for column in table_def.columns}
    reported: set[str] = set()
    for start in graph:
        if start not in derived or start in reported:
            continue
        stack = [(start, [start])]
        while stack:
            node, chain = stack.pop()
            for nxt in graph.get(node, []):
                if nxt == start:
                    reported.update(chain)
                    found.append(
                        (
                            by_name[start],
                            '"derive" reads itself through other derived columns: '
                            + " -> ".join([*chain, start])
                            + ".",
                        )
                    )
                    stack.clear()
                    break
                if nxt in graph and nxt not in chain:
                    stack.append((nxt, [*chain, nxt]))
    return found
