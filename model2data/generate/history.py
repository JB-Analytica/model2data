"""A table's history: every version of every row, as a source keeping its own (SCD type 2).

A table with `incremental.history: true` gets a second table, `<table>_history`,
built from the days `generate_days` returns: one row per version of each row,
with the table's columns as the version had them, then

- `valid_from`: when the version began -- its `incremental.updated_at` when the
  table has one and it is set, else the start of the day the version was
  delivered (day 0 for the rows the first day holds);
- `valid_to`: when the next version began, null for the current one;
- `is_current`: true for the last version of each key.

A version never begins before the one it follows: one that would (an
`updated_at` the first day placed after a later update) begins a second after
it instead, so a clean history has no overlapping ranges and one current row
per key. Rows are in the order of the table's own rows, each row's versions
oldest first.

The history is the record of the clean days: defects applied to the table
(`model2data.defects`) do not reach it. Only `overlapping_history` breaks it.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Optional, Union

import pandas as pd

from model2data.generate.days import DayResult
from model2data.model.engine import EngineInputs, to_engine
from model2data.model.types import Model
from model2data.model.validate import HISTORY_COLUMNS
from model2data.parse.dbml import ColumnDef, TableDef

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"
VALID_FROM, VALID_TO, IS_CURRENT = HISTORY_COLUMNS


def history_key(key: str) -> str:
    """The key of a table's history table: `orders` -> `orders_history`."""
    return f"{key}_history"


def primary_key(table: TableDef) -> list[str]:
    """The primary key's columns: the `pk` column, or the members of a `pk` key."""
    single = [c.name for c in table.columns if "pk" in c.settings]
    composite = (
        list(k.get("columns") or []) for k in table.composite_keys if k.get("type") == "pk"
    )
    return single or next(composite, [])


def history_tables(model: Union[Model, EngineInputs]) -> dict[str, TableDef]:
    """The history tables a model's run writes, by key, in the order of their tables.

    Each is the table's columns, with no key, test or hint of their own, and
    the three history columns; its `note` holds `{"history": {...}}`: the key
    and the history columns, which the dbt export writes the history's tests
    from.
    """
    inputs = to_engine(model) if isinstance(model, Model) else model
    out: dict[str, TableDef] = {}
    for key, table in inputs.tables.items():
        inc = inputs.incremental.get(key)
        if inc is None or not inc.history:
            continue
        name = history_key(key)
        columns = [
            ColumnDef(name=c.name, data_type=c.data_type, description=c.description)
            for c in table.columns
        ]
        columns += [
            ColumnDef(name=VALID_FROM, data_type="timestamp"),
            ColumnDef(name=VALID_TO, data_type="timestamp"),
            ColumnDef(name=IS_CURRENT, data_type="boolean"),
        ]
        out[name] = TableDef(
            name=name,
            columns=columns,
            description=f"Every version of every row of {key}, with its validity.",
            note={
                "history": {
                    "key": primary_key(table),
                    "valid_from": VALID_FROM,
                    "valid_to": VALID_TO,
                    "current": IS_CURRENT,
                }
            },
        )
    return out


def history_frames(
    model: Union[Model, EngineInputs], results: list[DayResult]
) -> dict[str, pd.DataFrame]:
    """The history tables of `results` (what `generate_days` returned), by key.

    Empty when no table keeps its history. A run of one day (`generate_days(model,
    0)`) gives each row one version, current.
    """
    inputs = to_engine(model) if isinstance(model, Model) else model
    out: dict[str, pd.DataFrame] = {}
    for name in history_tables(inputs):
        key = name[: -len("_history")]
        out[name] = _history(results, key, inputs.incremental[key].updated_at)
    return out


def _history(results: list[DayResult], key: str, updated_at: Optional[str]) -> pd.DataFrame:
    # Each version's row position and day are kept beside the rows, never in them, so
    # no column of the table can collide with them.
    parts: list[pd.DataFrame] = []
    tags: list[tuple[int, int]] = []
    before = 0
    for result in results:
        table_day = result.tables[key]
        inserted = table_day.state if result.day == 0 else table_day.inserted
        start = 0 if result.day == 0 else before
        for frame, positions in (
            (inserted, range(start, start + len(inserted))),
            (table_day.updated, table_day.updated_positions),
        ):
            if len(frame):
                parts.append(frame)
                tags += [(position, result.day) for position in positions]
        before = len(table_day.state)
    order = sorted(range(len(tags)), key=lambda row: tags[row])
    versions = pd.concat(parts, ignore_index=True).iloc[order].reset_index(drop=True)
    tags = [tags[row] for row in order]
    starts = {result.day: datetime.combine(result.date, time()) for result in results}

    begins: list[datetime] = []
    for row, (position, day) in enumerate(tags):
        stamp = versions[updated_at].iloc[row] if updated_at else None
        begin = pd.Timestamp(str(stamp)).to_pydatetime() if _set(stamp) else starts[day]
        if row and tags[row - 1][0] == position:
            begin = max(begin, begins[-1] + timedelta(seconds=1))
        begins.append(begin)
    last = [row == len(tags) - 1 or tags[row + 1][0] != tags[row][0] for row in range(len(tags))]
    ends: list[Optional[str]] = [
        None if last[row] else begins[row + 1].strftime(_TS_FORMAT) for row in range(len(begins))
    ]
    history = versions.copy()
    history[VALID_FROM] = [begin.strftime(_TS_FORMAT) for begin in begins]
    history[VALID_TO] = pd.Series(ends, dtype=object)
    history[IS_CURRENT] = last
    return history


def _set(value: Any) -> bool:
    return value is not None and bool(pd.notna(value))
