"""`rules`: comparisons every generated row of a table satisfies (spec 0.7.0).

A table's `rules` are comparisons between columns of the same row (see
`generate.comparison`). They hold after the table's columns are drawn: a row
that breaks one gets one column of the rule drawn again, until the rule holds,
within a retry budget.

- **The column redrawn** is the one that comes later in the table's column
  order, of the columns the comparison reads (its own column and the one its
  `{column: ...}` operand names). Of a rule comparing `home_team_id` with
  `away_team_id`, the `away_team_id` is redrawn. A column that cannot be
  drawn again without breaking something else -- a key (`pk`, `unique` or one of a
  composite key), a column copied from a parent, a rollup, one with `at_least` or one a
  `when` names -- is passed over for the next one back; with none left the
  rule is reported, not forced. A row still breaking its rule after half the retries has
  the comparison's other columns drawn again in turn, so a rule that the later column's
  range cannot meet for some value of the earlier one (`gte` of a column at its
  maximum) is met by moving the earlier one.
- **A redrawn value** is drawn as the column is drawn without the rule: its
  type's generator, its `min`, `max`, `distribution`, `weights`, and, for a
  foreign key, a value from the parent's rows, so it still names a parent that
  exists. It is never null, and only the rows that broke the rule are touched,
  so a column's `when` (null on the rows it does not match) keeps its nulls.
- **A null operand** exempts the row, as an SQL check constraint does: a
  comparison with a null is unknown, not false, so a rule on a nullable column does not
  turn nulls into values.
- **Retries** are bounded (`MAX_ATTEMPTS` rounds, each drawing the still-offending
  rows of every rule once), like a composite key's. A rule a table's value space cannot satisfy
  (`ne` between two columns that can each only hold one value) leaves rows that
  break it, which `get_unsatisfied_rules()` reports, and the CLI prints.

Determinism. Every draw is from a stream of its own, derived from the run's seed, the
table and the word `rules`, and the shared streams are put back as they were, so
a table without `rules` draws exactly what it drew before the rules existed and a
table with them changes only in the rows they redraw.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

from model2data.generate import comparison as cmp
from model2data.generate import kinds
from model2data.generate.faker import AsOf, generate_column_values
from model2data.generate.options import TimeProfile
from model2data.generate.when import own_stream
from model2data.parse.dbml import ColumnDef, TableDef

MAX_ATTEMPTS = 20

# Rules the most recent run left unmet: "table: rule 2 (home_score ne {column: away_score}): 3 row(s)".
_unsatisfied: list[str] = []


def reset_rules_state() -> None:
    """Clear the record of rules the last run could not meet."""
    _unsatisfied.clear()


def get_unsatisfied_rules() -> list[str]:
    """Return "table: rule N (comparison): R row(s)" labels for rules left unmet."""
    return list(_unsatisfied)


def column_kind(column: ColumnDef) -> cmp.ColumnKind:
    if column.enum_values:
        return "enum"
    if kinds.is_boolean_type(column.data_type):
        return "boolean"
    if kinds.is_integer_type(column.data_type):
        return "integer"
    if kinds.is_decimal_type(column.data_type):
        return "decimal"
    return kinds.temporal_kind(column.data_type) or "text"


def comparisons(table_def: TableDef) -> list[tuple[int, cmp.Comparison]]:
    """The table's rules as comparisons, each with the position of its rule in the list."""
    return [
        (index, comparison)
        for index, rule in enumerate(table_def.rules)
        for comparison in cmp.parse(rule)
    ]


def apply_rules(
    df: pd.DataFrame,
    table_def: TableDef,
    *,
    table_name: str,
    fk_pools: Mapping[str, Optional[pd.Series]],
    locked: set[str],
    seed: Optional[int],
    as_of: AsOf,
    run_anchor: datetime,
    time_profile: Optional[TimeProfile],
    skew: float,
    refresh: Optional[Callable[[pd.DataFrame], None]] = None,
) -> pd.DataFrame:
    """`df` with the rows that break one of the table's rules drawn again until none does.

    `fk_pools` maps each foreign-key column to the parent values it draws from (None
    for one with no parent rows: it is not drawn again), `locked` names the columns that must not be
    drawn again, `seed` seeds the rules' own stream (None leaves it to chance) and
    `run_anchor` is the value of `{run: as_of}`. `refresh(df)`, given when the table has
    derived columns, brings them (and the `when`s that read them) up to date in place after a
    round that drew values again, so the next round reads what the columns hold.
    """
    found = comparisons(table_def)
    if not found:
        return df
    columns = {column.name: column for column in table_def.columns}
    order = {name: position for position, name in enumerate(columns)}
    kind_of = {name: column_kind(column) for name, column in columns.items()}
    redraw = {
        index: _candidates(comparison, order, columns, fk_pools, locked)
        for index, (_, comparison) in enumerate(found)
    }

    def offending(comparison: cmp.Comparison) -> np.ndarray:
        held = cmp.holds(df, comparison, kind_of, as_of=run_anchor)
        if comparison.operator != "is_null":
            held = held | cmp.null_operand(df, comparison, kind_of)
        return np.flatnonzero(~held.to_numpy())

    with own_stream(seed):
        for attempt in range(MAX_ATTEMPTS):
            drawn = False
            for index, (_, comparison) in enumerate(found):
                rows = offending(comparison)
                if not len(rows) or not redraw[index]:
                    continue
                # The later column for the first half of the budget; a row still breaking
                # the rule after that has the other candidates drawn again in turn.
                options = redraw[index]
                name = (
                    options[0] if attempt < MAX_ATTEMPTS // 2 else options[attempt % len(options)]
                )
                df = _redraw(
                    df,
                    columns[name],
                    rows,
                    comparison,
                    table_name=table_name,
                    pool=fk_pools.get(name),
                    as_of=as_of,
                    time_profile=time_profile,
                    skew=skew,
                )
                drawn = True
            if not drawn:
                break
            if refresh is not None:
                refresh(df)
    for rule, comparison in found:
        left = len(offending(comparison))
        if left:
            _unsatisfied.append(
                f"{table_name}: rule {rule + 1} ({cmp.label(comparison)}): {left} row(s)"
            )
    return df


def _candidates(
    comparison: cmp.Comparison,
    order: Mapping[str, int],
    columns: Mapping[str, ColumnDef],
    fk_pools: Mapping[str, Optional[pd.Series]],
    locked: set[str],
) -> list[str]:
    """The columns of the comparison that may be drawn again, the one to try first first."""
    names = [name for name in dict.fromkeys(comparison.columns()) if name in columns]
    names.sort(key=lambda name: order[name], reverse=True)
    if comparison.operator == "is_null":
        names = names[:1]
    out = []
    for name in names:
        if name in locked:
            continue
        if name in fk_pools and fk_pools[name] is None:
            continue  # a foreign key with no parent rows to draw from
        out.append(name)
    return out


def _redraw(
    df: pd.DataFrame,
    column: ColumnDef,
    rows: np.ndarray,
    comparison: cmp.Comparison,
    *,
    table_name: str,
    pool: Optional[pd.Series],
    as_of: AsOf,
    time_profile: Optional[TimeProfile],
    skew: float,
) -> pd.DataFrame:
    """The rows' value of `column` drawn again, as the column is drawn."""
    if comparison.operator == "is_null" and comparison.operand is True:
        values: list = [None] * len(rows)
    else:
        values = generate_column_values(
            column,
            row_count=len(rows),
            fk_series=pool,
            force_not_null=True,
            table_name=table_name,
            as_of=as_of,
            time_profile=time_profile,
            skew=skew,
        )
    series = df[column.name].astype(object).copy()
    series.iloc[rows] = pd.Series(values, dtype=object).to_numpy()
    df[column.name] = series
    return df
