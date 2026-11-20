"""`combinations`: a table with one row for every combination of its parents' rows (spec 0.7.0).

A table that says `combinations: [home_team_id, away_team_id]` holds every pair of the
rows its two foreign keys point at, less the pairs its `rules` exclude. Nothing is drawn:
the row count follows from the parents, so `rows_per_table` may not name the table.

- **The rows** are the cartesian product of the parent columns each listed foreign key
  references, as the parents finally hold them, in the parents' row order with the first
  listed column outermost (`a1 b1, a1 b2, a2 b1, ...`).
- **The rules** that read only combination columns are applied to the combinations
  here, before any other column is generated, and the rows that fail one are left out. A
  rule that reads another column too holds as any rule does (generate.rules), which never
  draws a combination column again: they are locked, as keys are.
- **A ceiling** (`MAX_ROWS`) refuses a product that would be too large to generate, naming
  the parents' sizes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from math import prod

import numpy as np
import pandas as pd

from model2data.generate import comparison as cmp
from model2data.generate.rules import column_kind, comparisons
from model2data.parse.dbml import TableDef

MAX_ROWS = 1_000_000


def combination_frame(
    table_def: TableDef,
    table_name: str,
    fk_lookup: Mapping[tuple[str, str], tuple[str, str]],
    generated: Mapping[str, pd.DataFrame],
    *,
    run_anchor: datetime,
) -> pd.DataFrame:
    """The table's combination columns: one row per combination its rules leave."""
    pools: list[pd.Series] = []
    sizes: list[str] = []
    for name in table_def.combinations:
        target = fk_lookup.get((table_name, name))
        if target is None:
            raise ValueError(
                f"{table_name}.{name}: `combinations` names a column that is not a foreign key"
            )
        parent_table, parent_column = target
        parent = generated.get(parent_table)
        if parent is None or parent_column not in parent.columns:
            raise ValueError(
                f"{table_name}.{name}: `combinations` needs the rows of {parent_table}, which "
                f"are not generated before {table_name} (a self-reference or a cycle)"
            )
        pools.append(parent[parent_column].reset_index(drop=True))
        sizes.append(f"{parent_table} {len(parent)}")
    total = prod(len(pool) for pool in pools)
    if total > MAX_ROWS:
        raise ValueError(
            f"{table_name}: `combinations` would make {total:,} rows ({' x '.join(sizes)}), "
            f"more than the {MAX_ROWS:,} a combination table may hold: use fewer parent rows"
        )

    # The first column is the outermost: it repeats each value for every combination of the
    # columns after it, and the last cycles fastest.
    frame = pd.DataFrame(index=pd.RangeIndex(total))
    counts = [len(pool) for pool in pools]
    for position, (name, pool) in enumerate(zip(table_def.combinations, pools, strict=True)):
        inner, outer = prod(counts[position + 1 :]), prod(counts[:position])
        order = np.tile(np.repeat(np.arange(len(pool)), inner), outer)
        frame[name] = pool.to_numpy()[order]

    columns = {column.name: column for column in table_def.columns}
    kind_of = {name: column_kind(columns[name]) for name in table_def.combinations}
    own = set(table_def.combinations)
    keep = pd.Series(True, index=frame.index)
    for _, comparison in comparisons(table_def):
        if set(comparison.columns()) <= own:
            keep &= cmp.holds(frame, comparison, kind_of, as_of=run_anchor)
    frame = frame[keep].reset_index(drop=True)
    if frame.empty:
        raise ValueError(
            f"{table_name}: no combination of {', '.join(table_def.combinations)} is left "
            "(the parents have no rows, or its rules exclude every one)"
        )
    return frame
