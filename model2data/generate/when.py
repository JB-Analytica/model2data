"""`when`: a column that holds a value only on the rows another column allows.

`completed_at` with `when: {status: [done]}` is set on the done tasks and null on
every other one. A model says so in the column's hints; this module makes the
generated rows agree, after the rest of the table is drawn:

- On a row whose named columns all hold one of their listed values (a row
  that *matches*), the column keeps the value it was drawn with. Without a
  `null_rate` no matching row is null: one the ordinary null pass left empty
  is filled. With a `null_rate`, exactly floor(m * null_rate) of the m
  matching rows are null, so the rate counts only the rows the column can be
  set on.
- On every other row (a row whose named column is null included) it is null.

A filled date or timestamp follows what it follows: a column with an `after`
hint or an earlier-stage name (see `generate.timeline`) lands after it, as the
ordering pass would have placed it, and before a column that follows it, so
that column keeps its value -- unless it was itself earlier than what the
filled one follows (it was ordered while the filled one was null), in which
case it moves after the filled value, as the ordering pass would have moved
it. Any other filled value is one of the values the column already holds, so
its `weights`, `distinct` pool or `distribution` keep their shape.

Determinism. The column is drawn first exactly as it would be without `when`,
from the same stream and with the same draws, so every other column of the
table -- and every table after it -- comes out byte for byte what it does
without the hint, but for such a follower. What `when` itself decides (which
rows to fill or null, the values filled in) is drawn from a stream of its own,
derived from the run's seed, the table and the column, and the shared streams
are put back as they were afterwards. A model without `when` never reaches this module's draws.
"""

from __future__ import annotations

import random
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import faker.generator
import pandas as pd

from model2data.generate import kinds
from model2data.generate.faker import AsOf, generate_column_values
from model2data.generate.options import TimeProfile
from model2data.generate.timeline import (
    _build_dependencies,
    _format_value,
    _parse_value,
    _topological_order,
    column_business_hours,
    place_after,
)
from model2data.parse.dbml import ColumnDef, TableDef

SeedFor = Callable[[str], Optional[int]]


def conditions(column: ColumnDef) -> Optional[dict[str, list]]:
    """The column's `when` hint, or None when it has none."""
    when = (column.note or {}).get("when")
    return when if isinstance(when, dict) and when else None


def when_columns(table_def: TableDef) -> list[ColumnDef]:
    """The table's columns that carry `when`, in table order."""
    return [column for column in table_def.columns if conditions(column) is not None]


def _is_null(value: Any) -> bool:
    return value is None or (not isinstance(value, (list, dict)) and bool(pd.isna(value)))


def matching(
    frame: pd.DataFrame, by_name: Mapping[str, ColumnDef], when: Mapping[str, list]
) -> list[bool]:
    """Per row of `frame`: does every column `when` names hold one of its values?

    An enum's members are text (an integer member is its decimal text), so a
    listed `1` matches the member `"1"`. A null never matches.
    """
    result = [True] * len(frame)
    for other, values in when.items():
        enum = bool(by_name[other].enum_values)
        allowed = {str(value) for value in values} if enum else set(values)
        for row, value in enumerate(frame[other].tolist()):
            if result[row] and (_is_null(value) or (str(value) if enum else value) not in allowed):
                result[row] = False
    return result


def _null_count(column: ColumnDef, rows: int) -> int:
    """How many of `rows` matching rows stay null: floor(rows * null_rate), else none."""
    rate = (column.note or {}).get("null_rate")
    return int(rows * rate + 1e-9) if rate is not None else 0


@contextmanager
def own_stream(seed: Optional[int]) -> Iterator[None]:
    """Draw from a stream of `seed` inside the block, and put the shared streams back after.

    Both streams generation draws from -- the `random` module's and the one
    every Faker instance shares -- are seeded on the way in and restored on the
    way out, so what is drawn here never shifts a draw made outside.
    """
    shared = random.getstate()
    faker_random = faker.generator.random
    faker_state = faker_random.getstate()
    try:
        random.seed(seed)
        faker_random.seed(seed)
        yield
    finally:
        random.setstate(shared)
        faker_random.setstate(faker_state)


def _temporal_kind(column: ColumnDef) -> Optional[str]:
    return None if column.enum_values else kinds.temporal_kind(column.data_type)


class _Order:
    """What a filled date or timestamp of one table must sit between, row by row."""

    def __init__(
        self,
        table_def: TableDef,
        frame: pd.DataFrame,
        time_profile: Optional[TimeProfile] = None,
    ):
        self.time_profile = time_profile
        self.by_name = {column.name: column for column in table_def.columns}
        temporal = [
            column
            for column in table_def.columns
            if column.name in frame.columns and _temporal_kind(column) is not None
        ]
        self.deps = _build_dependencies(table_def, self.by_name, temporal)
        self.order = _topological_order(table_def, self.deps)
        self.frame = frame

    def _moments(self, names: set, row: int) -> list[datetime]:
        found = []
        for name in names:
            kind = _temporal_kind(self.by_name[name])
            parsed = _parse_value(self.frame[name].iloc[row], kind or "")
            if parsed is not None:
                found.append(parsed)
        return found

    def after(self, name: str, row: int) -> Optional[datetime]:
        """The latest moment of the row the column follows, None when it follows none."""
        found = self._moments(self.deps.get(name, set()), row)
        return max(found) if found else None

    def before(self, name: str, row: int) -> Optional[datetime]:
        """The earliest moment of the row among the columns that follow it."""
        followers = {other for other, required in self.deps.items() if name in required}
        found = self._moments(followers, row)
        return min(found) if found else None

    def place(self, name: str, lower: datetime, kind: str, upper: datetime) -> datetime:
        """A moment for `name` a random gap after `lower`, in the column's shape."""
        business_hours = column_business_hours(self.by_name[name], self.time_profile)
        return place_after(lower, kind, upper, business_hours)

    def settle(self, name: str, row: int, cap: datetime) -> None:
        """Move what follows `name` in this row to no earlier than what it follows, in order.

        Only needed where a column that follows a filled one was out of order
        with what the filled one follows (it was ordered while the filled one
        was null): the filled value cannot sit between them, so the follower
        moves, as the ordering pass would have moved it.
        """
        moved = {name}
        for other in self.order:
            if not self.deps[other] & moved:
                continue
            kind = _temporal_kind(self.by_name[other]) or ""
            column = self.frame.columns.get_loc(other)
            value = _parse_value(self.frame.iat[row, column], kind)
            lower = self.after(other, row)
            if value is None or lower is None or value >= lower:
                continue
            self.frame.iat[row, column] = _format_value(self.place(other, lower, kind, cap), kind)
            moved.add(other)


def _draw_like(
    column: ColumnDef,
    held: list,
    count: int,
    as_of: AsOf,
    time_profile: Optional[TimeProfile],
) -> list:
    """`count` values of the column: some it already holds, or fresh ones when it holds none."""
    if held:
        return [random.choice(held) for _ in range(count)]
    return generate_column_values(
        column, count, force_not_null=True, as_of=as_of, time_profile=time_profile
    )


def apply_when(
    frame: pd.DataFrame,
    table_def: TableDef,
    *,
    cap: datetime,
    seed_for: SeedFor,
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
    floor: Optional[datetime] = None,
    held: Optional[Callable[[str], list]] = None,
) -> None:
    """Null each `when` column of `frame` where its rows do not match, and fill where they do.

    In place. `cap` is the latest moment a filled date or timestamp may take
    (midnight of `as_of` on the first day, the end of the day on a later one).
    `floor`, given on a later day, is the start of that day: a filled value
    then falls on the day, at a time drawn evenly between what it follows (or
    the day's start) and `cap`. `seed_for(column)` is the seed of the column's
    own stream (None without a run seed). `held(column)` lists more values the
    column holds (the table's rows before the day), drawn from like the
    frame's own when filling a column that is not temporal.
    """
    columns = when_columns(table_def)
    if not columns:
        return
    order = _Order(table_def, frame, time_profile)
    for column in columns:
        name = column.name
        match = matching(frame, order.by_name, conditions(column) or {})
        values = frame[name].tolist()
        nulls = [row for row, value in enumerate(values) if match[row] and _is_null(value)]
        present = [row for row, value in enumerate(values) if match[row] and not _is_null(value)]
        target = _null_count(column, sum(match))
        kind = _temporal_kind(column)
        with own_stream(seed_for(name)):
            if len(nulls) > target:
                rows = sorted(random.sample(nulls, len(nulls) - target))
                if kind is None:
                    pool = [value for value in values if not _is_null(value)]
                    pool += held(name) if held is not None else []
                    filled = _draw_like(column, pool, len(rows), as_of, time_profile)
                else:
                    filled = [_moment(order, name, kind, row, values, cap, floor) for row in rows]
                for row, value in zip(rows, filled, strict=True):
                    values[row] = value
                if kind is not None:
                    frame[name] = values
                    for row in rows:
                        order.settle(name, row, cap)
            elif len(nulls) < target:
                for row in random.sample(present, target - len(nulls)):
                    values[row] = None
        for row, matches in enumerate(match):
            if not matches:
                values[row] = None
        frame[name] = values


def _moment(
    order: _Order,
    name: str,
    kind: str,
    row: int,
    values: list,
    cap: datetime,
    floor: Optional[datetime],
) -> Any:
    """A date or timestamp for one row of a column, between what it follows and what follows it."""
    lower = order.after(name, row)
    upper = order.before(name, row)
    upper = cap if upper is None else min(upper, cap)
    if floor is not None:
        start = floor if lower is None else max(lower, floor)
        span = max(0, int((upper - start).total_seconds()))
        moment = start + timedelta(seconds=random.randint(0, span))
    elif lower is not None:
        moment = order.place(name, lower, kind, upper)
    else:
        held = [
            parsed
            for value in values
            for parsed in [_parse_value(value, kind)]
            if parsed is not None
        ]
        moment = random.choice(held) if held else upper
    if moment > upper:
        moment = upper
    if lower is not None and moment < lower:
        moment = lower
    return _format_value(moment, kind)


def update_when(
    table_def: TableDef,
    before: pd.DataFrame,
    changes: pd.DataFrame,
    *,
    day_start: datetime,
    updated_at: Optional[str],
    seed_for: SeedFor,
    held: Callable[[str], list],
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
) -> None:
    """Keep the `when` columns of a day's updated rows in step with their conditions.

    `before` holds the updated rows as they were, `changes` the columns the
    update changed (same index); a `when` column whose value changes is added
    to `changes`, in place. A row the update brings to match gets a value on
    the day: the row's `updated_at` for a timestamp when the table has one, a
    time of the day otherwise (never before what the column follows), the day
    itself for a date, and one of the values the column holds for anything
    else. With a `null_rate`, floor(n * null_rate) of the n rows coming to
    match stay null. A row the update takes out of matching loses its value; a
    row that matched and still does keeps it.
    """
    by_name = {column.name: column for column in table_def.columns}
    for column in when_columns(table_def):
        name = column.name
        when = conditions(column) or {}
        if name not in changes.columns and not any(other in changes.columns for other in when):
            continue
        after = before.copy()
        for changed in changes.columns:
            after[changed] = changes[changed]
        was = matching(before, by_name, when)
        now = matching(after, by_name, when)
        redrawn = name in changes.columns
        values = after[name].tolist()
        entering = [
            row
            for row, value in enumerate(values)
            if now[row] and _is_null(value) and (redrawn or not was[row])
        ]
        kind = _temporal_kind(column)
        with own_stream(seed_for(name)):
            stay_null = set(random.sample(entering, _null_count(column, len(entering))))
            rows = [row for row in entering if row not in stay_null]
            if kind is None:
                filled = _draw_like(column, held(name), len(rows), as_of, time_profile)
            else:
                order = _Order(table_def, after)
                filled = [
                    _on_the_day(order, name, kind, after, row, day_start, updated_at)
                    for row in rows
                ]
        for row, value in zip(rows, filled, strict=True):
            values[row] = value
        for row, matches in enumerate(now):
            if not matches:
                values[row] = None
        old = before[name].tolist()
        if redrawn or any(
            _is_null(new) != _is_null(was_value) or (not _is_null(new) and new != was_value)
            for new, was_value in zip(values, old, strict=True)
        ):
            changes[name] = pd.Series(values, index=changes.index, dtype=object)


def _on_the_day(
    order: _Order,
    name: str,
    kind: str,
    after: pd.DataFrame,
    row: int,
    day_start: datetime,
    updated_at: Optional[str],
) -> Any:
    """The moment a row comes to match on a later day: when the update happened."""
    if kind == "date":
        return day_start.date()
    moment = None
    if updated_at is not None and updated_at in after.columns:
        moment = _parse_value(after[updated_at].iloc[row], "timestamp")
    if moment is None:
        moment = day_start + timedelta(seconds=random.randrange(86400))
    lower = order.after(name, row)
    if lower is not None and moment < lower:
        moment = lower
    return _format_value(moment, kind)


def end_of_day(day_start: datetime) -> datetime:
    """The last whole second of the day starting at `day_start`."""
    return day_start + timedelta(days=1, seconds=-1)
