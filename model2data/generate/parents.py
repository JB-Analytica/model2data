"""A child row comes into being on or after the parent rows it points at.

An order placed before its customer signed up, a review written before the
product existed: each column is plausible on its own and the pair is not. So a
table's **creation column** (see `generate.timeline.creation_column`: its
`created_at`, `signup_date`, or else its first date that follows nothing, such
as `order_date`) is kept on or after the creation column of every parent row
its foreign keys point at, on every row, by the rule `parent_rules` works out:

- The rule follows a foreign key onto a parent's primary or unique key (one
  parent row per value), to a parent that has a creation column and is
  generated before the child. A self-reference, a foreign key that breaks a
  cycle (its parent is generated after the child), a parent without a date
  and a reference onto a column that is no key are not followed; a null
  foreign key constrains nothing.
- `after_parent: false` on the child's creation column turns the rule off for
  the table.
- A date compared with a timestamp compares by day: a date is not before the
  timestamp's day, and a timestamp is not before the date's midnight.

How a row is made to hold (`follow_parents`), keeping the child's own shape:

1. A row that already holds keeps every value: the column was drawn exactly
   as it is without the rule.
2. A row whose date is before a parent's takes another parent through that
   foreign key, one created on or before its date, drawn from the parents the
   column already points at (so a popular customer stays popular, and one
   created earlier has had longer to order). The child's date keeps its draw,
   so its growth, seasonality and business hours stay exactly as asked. A
   foreign key that must be unique (a one-to-one) keeps its parent.
3. A row with no such parent to take (it is dated before every parent the
   column points at, or its foreign key is unique) moves its date instead,
   drawn again from the column's own shape cut to the part of the window on or
   after the latest of its parents: the same day weights (growth, seasonality,
   weekdays) and, for a timestamp, the same hours. A parent created at the
   very end of the window gives a child at the very end of it, never past it.

Because a kept row is a draw from the column's shape and a moved one is a draw
from that shape cut at the parent, step 3 alone gives each row exactly the
shape cut at its parent; step 2 keeps the column's shape whole wherever a
parent allows it.

All draws come from the table's own stream (seeded by `generate.core`), so the
rule is as reproducible as the rest of the table.
"""

from __future__ import annotations

import random
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import accumulate
from typing import Optional

import numpy as np
import pandas as pd

from model2data.generate import kinds
from model2data.generate.faker import _DATE_WINDOW_YEARS, _column_time_profile, _years_before
from model2data.generate.options import UNIFORM, TimeProfile
from model2data.generate.relationships import _key_kind
from model2data.generate.timeline import (
    _TIMESTAMP_WINDOW_DAYS,
    HOUR_WEIGHTS,
    AsOf,
    _day_weight,
    _resolve_anchor,
    _window_days,
    column_business_hours,
    creation_column,
)
from model2data.parse.dbml import ColumnDef, TableDef

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class ParentLink:
    """One foreign key of a child along which its creation column follows a parent's.

    `fk` is the child's foreign-key column, `parent_key` the parent column it
    references, `parent_column` the parent's creation column, of `parent_kind`
    (`"date"` or `"timestamp"`). `unique` is true
    when the foreign key takes each parent at most once (a one-to-one), so a
    row cannot simply take another parent. `by_day` is true when the two
    columns compare by day (one is a date, the other a timestamp).
    """

    fk: str
    parent_table: str
    parent_key: str
    parent_column: str
    parent_kind: str
    unique: bool
    by_day: bool


@dataclass(frozen=True)
class ParentRule:
    """A table's creation column (`column`, of `kind`) and the parents it follows."""

    table: str
    column: str
    kind: str
    links: tuple[ParentLink, ...]


def after_parent_enabled(column: ColumnDef) -> bool:
    """False when the column opts out with `after_parent: false`."""
    return (column.note or {}).get("after_parent") is not False


def parent_rules(
    tables: Mapping[str, TableDef], fk_refs: list[dict], order: list[str]
) -> dict[str, ParentRule]:
    """The rule of every table it applies to, by table key.

    `fk_refs` are the foreign keys as `generate.relationships.classify_refs`
    finds them, `order` the order tables are generated in: a parent counts
    only when it comes before the child, which leaves out a self-reference and
    a foreign key that breaks a cycle (its parent is drawn after the child).
    """
    position = {name: index for index, name in enumerate(order)}
    rules: dict[str, ParentRule] = {}
    for table_name, table_def in tables.items():
        column = creation_column(table_def)
        if column is None or not after_parent_enabled(column):
            continue
        kind = kinds.temporal_kind(column.data_type)
        assert kind is not None
        by_name = {c.name: c for c in table_def.columns}
        links: list[ParentLink] = []
        for ref in fk_refs:
            if ref["source_table"] != table_name:
                continue
            parent = ref["target_table"]
            parent_def = tables.get(parent)
            fk_column = by_name.get(ref["source_column"])
            if (
                parent_def is None
                or fk_column is None
                or parent == table_name
                or position.get(parent, len(order)) >= position.get(table_name, -1)
                or _key_kind(dict(tables), ref) not in ("pk", "unique")
            ):
                continue
            parent_column = creation_column(parent_def)
            if parent_column is None:
                continue
            parent_kind = kinds.temporal_kind(parent_column.data_type)
            assert parent_kind is not None
            link = ParentLink(
                fk=fk_column.name,
                parent_table=parent,
                parent_key=ref["target_column"],
                parent_column=parent_column.name,
                parent_kind=parent_kind,
                unique="pk" in fk_column.settings or "unique" in fk_column.settings,
                by_day=parent_kind != kind,
            )
            if link not in links:
                links.append(link)
        if links:
            rules[table_name] = ParentRule(table_name, column.name, kind, tuple(links))
    return rules


# ---------------------------------------------------------
# Reading moments
# ---------------------------------------------------------
def moments(series: pd.Series, kind: str) -> pd.Series:
    """A column's values as `datetime64` (NaT for a null or a value that is no moment).

    A timestamp column holds `"YYYY-MM-DD HH:MM:SS"` text, a date column `date`s.
    """
    if kind == "timestamp":
        return pd.to_datetime(series, format=_TS_FORMAT, errors="coerce")
    return pd.to_datetime(series, errors="coerce")


def parent_floor(
    frame: pd.DataFrame, link: ParentLink, parents: Mapping[str, pd.DataFrame]
) -> pd.Series:
    """Per row of `frame`: the creation moment of the parent its `link` points at.

    NaT where the foreign key is null, points at no row, or the parent's date
    is null. By day (midnight) when `link.by_day`.
    """
    parent = parents[link.parent_table]
    created = moments(parent[link.parent_column], link.parent_kind)
    if link.by_day:
        created = created.dt.normalize()
    keys = parent[link.parent_key]
    lookup = pd.Series(created.to_numpy(), index=_key_index(keys))
    lookup = lookup[~lookup.index.duplicated(keep="first")]
    values = frame[link.fk]
    mapped = _key_values(values).map(lookup)
    return pd.Series(pd.to_datetime(mapped.to_numpy()), index=frame.index)


def _key_index(keys: pd.Series) -> pd.Index:
    return pd.Index(_key_values(keys).tolist(), dtype=object)


def _key_values(values: pd.Series) -> pd.Series:
    """Keys as plain Python values (`tolist` unboxes them), so an `Int64` parent and an
    int child match, and a missing key (`pd.NA`, NaN) as None."""
    return pd.Series(
        [None if pd.isna(value) else value for value in values.tolist()],
        index=values.index,
        dtype=object,
    )


# ---------------------------------------------------------
# Day 0: making a table's rows hold
# ---------------------------------------------------------
def follow_parents(
    df: pd.DataFrame,
    rule: ParentRule,
    column: ColumnDef,
    parents: Mapping[str, pd.DataFrame],
    *,
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
    repick: bool = True,
) -> list:
    """Make every row of `df` hold `rule`, in place; return the index labels whose date moved.

    See the module docstring for how. `parents` holds the generated parent
    tables. `repick` false skips step 2 (taking another parent), for a pass
    that must leave the foreign keys as they are.
    """
    own = moments(df[rule.column], rule.kind)
    if not bool(own.notna().any()):
        return []

    floors: list[pd.Series] = []
    for link in rule.links:
        floor = parent_floor(df, link, parents)
        late = own.notna() & floor.notna() & (own < floor)
        if repick and not link.unique and bool(late.any()):
            floor = _repick(df, link, own, floor, late)
        floors.append(floor)

    lowest = pd.concat(floors, axis=1).max(axis=1) if len(floors) > 1 else floors[0]
    late = own.notna() & lowest.notna() & (own < lowest)
    if not bool(late.any()):
        return []

    window = _Window(rule.kind, column, as_of, time_profile)
    positions = np.flatnonzero(late.to_numpy())
    floor_values = lowest.to_numpy()
    values = df[rule.column].tolist()
    for position in positions:
        lower = pd.Timestamp(floor_values[position]).to_pydatetime()
        values[position] = _stored(window.draw(lower), rule.kind)
    df[rule.column] = pd.Series(values, index=df.index, dtype=object)
    return df.index[positions].tolist()


def _repick(
    df: pd.DataFrame, link: ParentLink, own: pd.Series, floor: pd.Series, late: pd.Series
) -> pd.Series:
    """Give each late row a parent created by its date, drawn from those the column holds.

    Returns the rows' parent moments after the change. A row no parent the
    column holds was created by keeps its parent (its date moves instead).
    """
    floor_ns = floor.to_numpy(dtype="datetime64[ns]").astype("int64")
    held = (floor.notna() & df[link.fk].notna()).to_numpy()
    keys = df[link.fk].tolist()
    held_positions = np.flatnonzero(held)
    order = held_positions[np.argsort(floor_ns[held_positions], kind="stable")]
    stamps = floor_ns[order].tolist()
    own_ns = own.to_numpy(dtype="datetime64[ns]").astype("int64")
    new_floor = floor_ns.copy()
    changed = False
    for position in np.flatnonzero(late.to_numpy()).tolist():
        count = bisect_right(stamps, int(own_ns[position]))
        if count == 0:
            continue
        source = int(order[random.randrange(count)])
        keys[position] = keys[source]
        new_floor[position] = floor_ns[source]
        changed = True
    if not changed:
        return floor
    df[link.fk] = pd.Series(keys, index=df.index, dtype=df[link.fk].dtype)
    # NaT went through int64 as its sentinel and comes back as NaT.
    return pd.Series(new_floor.view("datetime64[ns]"), index=floor.index)


def _stored(moment: datetime, kind: str) -> object:
    """A moment as the column stores it: `"YYYY-MM-DD HH:MM:SS"` text, or a `date`."""
    if kind == "timestamp":
        return moment.strftime(_TS_FORMAT)
    return moment.date()


class _Window:
    """A column's own window and shape, drawn from on or after a given moment."""

    def __init__(
        self,
        kind: str,
        column: ColumnDef,
        as_of: AsOf,
        time_profile: Optional[TimeProfile],
    ) -> None:
        anchor = _resolve_anchor(as_of)
        self.kind = kind
        self.business_hours = column_business_hours(column, time_profile)
        profile = _column_time_profile(column, time_profile) or UNIFORM
        if kind == "date":
            start = _years_before(anchor, _DATE_WINDOW_YEARS)
            end = anchor
        else:
            start = anchor - timedelta(days=_TIMESTAMP_WINDOW_DAYS)
            end = anchor - timedelta(days=1)
        self.start = start
        self.days = _window_days(start, end)
        weights = [_day_weight(day, start, end, profile) for day in self.days]
        self.added_up = list(accumulate(weights))
        self.weights = weights
        # A timestamp's window ends at midnight of the anchor, a date's on the anchor.
        self.cap = datetime(anchor.year, anchor.month, anchor.day)

    def draw(self, lower: datetime) -> datetime:
        """A moment on or after `lower` in the window, in the column's shape."""
        if self.kind == "date":
            return self._date(lower)
        return self._timestamp(lower)

    def _date(self, lower: datetime) -> datetime:
        # A parent is never dated after the anchor, so `first` is inside the window.
        first = min(max(0, (lower.date() - self.start).days), len(self.days) - 1)
        before = self.added_up[first - 1] if first else 0.0
        point = before + random.random() * (self.added_up[-1] - before)
        index = min(bisect_right(self.added_up, point), len(self.days) - 1)
        index = max(index, first)
        day = self.days[index]
        return datetime(day.year, day.month, day.day)

    def _timestamp(self, lower: datetime) -> datetime:
        if lower >= self.cap:
            # A parent created at the very end of the window (a date on the
            # anchor day): the child is created at the end too, never past it.
            return lower
        # Only a row dated before its parent moves, and it is dated inside the
        # window, so the parent is too: `first` is never before the window.
        first = (lower.date() - self.start).days
        offset = lower.hour * 3600 + lower.minute * 60 + lower.second
        first_share = self.weights[first] * self._share_after(offset)
        rest = self.added_up[-1] - self.added_up[first]
        point = random.random() * (first_share + rest)
        if point < first_share or rest <= 0:
            day = self.days[first]
            seconds = self._time_after(offset)
        else:
            target = self.added_up[first] + (point - first_share)
            index = min(bisect_right(self.added_up, target), len(self.days) - 1)
            day = self.days[max(index, first + 1)]
            seconds = self._time_after(0)
        moment = datetime(day.year, day.month, day.day) + timedelta(seconds=seconds)
        return max(moment, lower)

    def _share_after(self, offset: int) -> float:
        """The share of a day's draws that fall at or after `offset` seconds into it."""
        if not self.business_hours:
            return (86400 - offset) / 86400
        hour, into = divmod(offset, 3600)
        remaining = HOUR_WEIGHTS[hour] * (3600 - into) / 3600 + sum(HOUR_WEIGHTS[hour + 1 :])
        return remaining / sum(HOUR_WEIGHTS)

    def _time_after(self, offset: int) -> int:
        """Seconds into a day, at or after `offset`, drawn as the column draws a time of day."""
        if not self.business_hours:
            return random.randint(offset, 86399)
        hour, into = divmod(offset, 3600)
        hours = list(range(hour, 24))
        weights = [HOUR_WEIGHTS[hour] * (3600 - into) / 3600] + [HOUR_WEIGHTS[h] for h in hours[1:]]
        chosen = random.choices(hours, weights=weights)[0]
        if chosen == hour:
            return hour * 3600 + random.randint(into, 3599)
        return chosen * 3600 + random.randrange(3600)


# ---------------------------------------------------------
# Later days: a new row's timestamp on its day
# ---------------------------------------------------------
def floor_on_day(
    frame: pd.DataFrame, rule: ParentRule, parents: Mapping[str, pd.DataFrame]
) -> pd.Series:
    """Per new row: the latest creation moment among the parents it points at (NaT for none)."""
    floors = [parent_floor(frame, link, parents) for link in rule.links]
    return pd.concat(floors, axis=1).max(axis=1) if len(floors) > 1 else floors[0]
