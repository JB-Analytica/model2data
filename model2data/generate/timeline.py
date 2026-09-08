"""When generated rows happen: weighted time draws and within-row ordering.

Two independent concerns share this module because both are specifically
about *time*, unlike every other column type the generator draws:

- `weighted_dates`/`weighted_timestamps` apply a `TimeProfile` to the date and
  timestamp branches of `generate_column_values`, shaping *which* moments in
  the window get drawn more often (business hours, a growth trend, a seasonal
  peak). They only run when the profile is not uniform -- the uniform path
  keeps calling Faker exactly as before, so a caller who passes nothing still
  gets what earlier releases generated with the same seed.

- `order_row_times` fixes up rows after the fact so a "created" column never
  lands after its own "updated" column, and a "deleted"/"completed" column
  never lands before either. This is not a profile setting: a row updated
  before it was created is wrong no matter how timestamps were drawn, so it
  runs unconditionally, right after a table's DataFrame is built.

Both draw from the shared `random` module only, which `generate.core` already
seeds per table, so a run stays reproducible under `--seed` without this
module doing any seeding of its own.
"""

from __future__ import annotations

import math
import random
import re
from collections import deque
from datetime import date, datetime, timedelta
from typing import Optional, Union

from model2data.generate.options import TimeProfile
from model2data.parse.dbml import ColumnDef, TableDef

# Mirrors generate.faker's `AsOf`/`_anchor_date`. Not imported from there: this
# module has to stay free of a dependency on generate.faker, because faker.py
# imports the weighted-draw functions below -- importing back would make the
# two modules depend on each other.
AsOf = Union[date, datetime, None]


def _resolve_anchor(as_of: AsOf) -> date:
    """The date a run generates relative to: `as_of`, or today when it is None."""
    if as_of is None:
        return date.today()
    if isinstance(as_of, datetime):
        return as_of.date()
    return as_of


# ---------------------------------------------------------
# Weighted time draws (Feature 1)
# ---------------------------------------------------------
# How far back the timestamp window reaches from the anchor -- matching
# generate.faker's `_random_datetime` window (365 days ending at midnight of
# the anchor).
_TIMESTAMP_WINDOW_DAYS = 365

# The day a seasonal peak sits on (16 November, day 320 of a non-leap year):
# the shape retail, SaaS renewals and most other business calendars share.
_SEASON_PEAK_DOY = 320

# Business-hours weight per hour of the day (index = hour, 0-23). Night hours
# are rare but not impossible, the working core of the day is busiest, and the
# lunch hour dips without disappearing -- the shape a real office or storefront
# produces.
HOUR_WEIGHTS = (
    [0.05] * 6  # 00:00-05:59: night
    + [0.3] * 2  # 06:00-07:59: early
    + [1.0] * 4  # 08:00-11:59: morning core
    + [0.7]  # 12:00-12:59: lunch dip
    + [1.0] * 5  # 13:00-17:59: afternoon core
    + [0.35] * 3  # 18:00-20:59: evening
    + [0.1] * 3  # 21:00-23:59: late
)


def _window_days(start: date, end: date) -> list[date]:
    """Every calendar day from `start` to `end`, inclusive of both ends."""
    span = (end - start).days
    return [start + timedelta(days=offset) for offset in range(span + 1)]


def _day_weight(day: date, start: date, end: date, profile: TimeProfile) -> float:
    """How likely `day` is to be drawn, relative to the other days in the window.

    `trend` carries the growth across the window (`t=0` at `start`, `t=1` at
    `end`); `season` is an annual cycle peaking at `_SEASON_PEAK_DOY`; `weekday`
    thins out weekends when `business_hours` is set. The three multiply rather
    than add so each is a pure scaling factor on the others -- a quiet weekend
    stays quiet whether the window is growing or shrinking.
    """
    span = (end - start).days
    t = 0.0 if span == 0 else (day - start).days / span
    trend = 1 + profile.growth * t

    doy = day.timetuple().tm_yday
    season = 1 + profile.seasonality * math.cos(2 * math.pi * (doy - _SEASON_PEAK_DOY) / 365.25)

    weekday = 0.3 if profile.business_hours and day.weekday() >= 5 else 1.0

    return trend * season * weekday


def weighted_dates(row_count: int, profile: TimeProfile, start: date, end: date) -> list[date]:
    """Draw `row_count` dates from `[start, end]`, shaped by `profile`.

    Each day in the window gets a weight (see `_day_weight`); `random.choices`
    then draws each row independently -- rows are not sorted into date order,
    matching how real tables are laid out. Only called when `profile` is not
    uniform: the uniform path keeps using Faker's own `date_between` directly.
    """
    days = _window_days(start, end)
    weights = [_day_weight(day, start, end, profile) for day in days]
    return random.choices(days, weights=weights, k=row_count)


def weighted_timestamps(row_count: int, profile: TimeProfile, anchor: date) -> list[str]:
    """Draw `row_count` timestamps in the 365 days ending at midnight of `anchor`.

    A day is drawn first, weighted the same way `weighted_dates` weights a
    date. The time of day within that day is drawn separately: uniformly
    across the day's seconds by default, or -- when `profile.business_hours`
    is set -- an hour drawn from `HOUR_WEIGHTS` with a uniform minute and
    second, so a "business hours" run still occasionally produces a night-time
    row instead of hard-cutting the day at 9 and 5.

    Returned as `"YYYY-MM-DD HH:MM:SS"` strings to whole seconds, matching
    `generate.faker._random_datetime`'s format exactly.
    """
    start = anchor - timedelta(days=_TIMESTAMP_WINDOW_DAYS)
    end = anchor - timedelta(days=1)
    days = _window_days(start, end)
    weights = [_day_weight(day, start, end, profile) for day in days]
    chosen_days = random.choices(days, weights=weights, k=row_count)

    values: list[str] = []
    for day in chosen_days:
        if profile.business_hours:
            hour = random.choices(range(24), weights=HOUR_WEIGHTS)[0]
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            offset_seconds = hour * 3600 + minute * 60 + second
        else:
            offset_seconds = random.randint(0, 86399)
        moment = datetime(day.year, day.month, day.day) + timedelta(seconds=offset_seconds)
        values.append(moment.isoformat(sep=" "))
    return values


# ---------------------------------------------------------
# Within-row ordering (Feature 2)
# ---------------------------------------------------------
# How far, on average, a dependent column lands after the column(s) it must
# follow -- 3 days for both units, so "shipped two days after paid" and
# "shipped two days late" both stay plausible.
_TIMESTAMP_GAP_SCALE_SECONDS = 3 * 86400
_DATE_GAP_SCALE_DAYS = 3

# Column-name stems that place a column at a stage in the created -> updated ->
# closed chain. A column matches a stage by tokenizing its name on non-
# alphanumeric characters and looking for the stem as a contiguous run of
# tokens (`_contains_phrase`), so `order_start`/`start_date`/`signed_up_at`
# all match without a separate rule for compound names.
_STAGE_WORDS: tuple[tuple[str, ...], ...] = (
    (  # stage 0: the row comes into being
        "created",
        "inserted",
        "registered",
        "signed_up",
        "signup",
        "ordered",
        "placed",
        "opened",
        "started",
        "start",
        "from",
        "begin",
        "valid_from",
        "effective_from",
    ),
    (  # stage 1: the row is acted on
        "updated",
        "modified",
        "paid",
        "confirmed",
        "approved",
        "shipped",
        "dispatched",
        "sent",
        "processed",
        "last_login",
        "last_seen",
    ),
    (  # stage 2: the row's story ends
        "delivered",
        "completed",
        "closed",
        "finished",
        "ended",
        "end",
        "to",
        "until",
        "cancelled",
        "canceled",
        "deleted",
        "archived",
        "expired",
        "expires",
        "valid_to",
        "effective_to",
        "resolved",
    ),
)

# A birth date has nothing to do with when a row was created, updated or
# closed -- it describes the person/entity, not the record -- so a column
# whose name carries either token is never assigned a stage.
_EXCLUDED_FROM_CHAIN = frozenset({"birth", "dob"})

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _tokenize(name: str) -> list[str]:
    return [token for token in _TOKEN_SPLIT_RE.split(name.lower()) if token]


def _phrase_tokens(word: str) -> tuple[str, ...]:
    return tuple(_tokenize(word))


def _contains_phrase(tokens: list[str], phrase: tuple[str, ...]) -> bool:
    n = len(phrase)
    return any(tuple(tokens[i : i + n]) == phrase for i in range(len(tokens) - n + 1))


def _infer_stage(column_name: str) -> Optional[int]:
    """The chain stage a column's name implies, or None if it names none."""
    tokens = _tokenize(column_name)
    if _EXCLUDED_FROM_CHAIN.intersection(tokens):
        return None
    for stage, words in enumerate(_STAGE_WORDS):
        for word in words:
            if _contains_phrase(tokens, _phrase_tokens(word)):
                return stage
    return None


def _column_kind(data_type: str) -> Optional[str]:
    """`"timestamp"`, `"date"`, or None for anything else -- including plain `time`.

    Matches the same two conditions generate.faker's date/timestamp branches
    already use, so a column this module treats as temporal is exactly a
    column those branches treat as temporal.
    """
    base_type = data_type.lower().split("(")[0].strip()
    if "timestamp" in base_type or "datetime" in base_type:
        return "timestamp"
    if "date" in base_type and "time" not in base_type:
        return "date"
    return None


def _parse_value(value: object, kind: str) -> Optional[datetime]:
    """A row's raw column value as a `datetime`, or None if it isn't one.

    Dates and timestamps are compared and combined as `datetime`s internally
    (a pure date becomes its midnight) so a date column can sit in the same
    dependency chain as a timestamp column. `None`/a non-temporal default
    (the nullability pass already ran) come back as None, which callers treat
    as "nothing to enforce for this row".
    """
    if kind == "timestamp":
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None
    if kind == "date":
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day)
        return None
    return None


def _format_value(moment: datetime, kind: str) -> Union[str, date]:
    """The inverse of `_parse_value`: back to the column's stored representation."""
    if kind == "timestamp":
        return moment.isoformat(sep=" ")
    return moment.date()


def _build_dependencies(
    table_def: TableDef, columns_by_name: dict, temporal_columns: list[ColumnDef]
) -> dict[str, set[str]]:
    """The columns each temporal column must be `>=`, from `after` hints and stages.

    Explicit `after` hints (priority 1) take a column out of the name-based
    stage system entirely (priority 2) -- it already has an explicit
    dependency, so inferring another one from its name would double up on the
    same job with a different, possibly conflicting, answer.
    """
    deps: dict[str, set[str]] = {}
    explicit: set[str] = set()

    for column in temporal_columns:
        after = (column.note or {}).get("after") if column.note else None
        if not after:
            continue
        after_name = str(after)
        target = columns_by_name.get(after_name)
        if target is None:
            raise ValueError(
                f"Table '{table_def.name}' column '{column.name}': after references "
                f"unknown column '{after_name}'."
            )
        if _column_kind(target.data_type) is None:
            raise ValueError(
                f"Table '{table_def.name}' column '{column.name}': after column "
                f"'{after_name}' is not a date or timestamp column."
            )
        deps[column.name] = {after_name}
        explicit.add(column.name)

    stages = {
        column.name: stage
        for column in temporal_columns
        if column.name not in explicit
        for stage in [_infer_stage(column.name)]
        if stage is not None
    }
    for name, stage in stages.items():
        earlier = {other for other, other_stage in stages.items() if other_stage < stage}
        if earlier:
            deps[name] = earlier

    return deps


def _topological_order(table_def: TableDef, deps: dict[str, set[str]]) -> list[str]:
    """Columns in an order where every dependency is processed before its dependent.

    Only nodes in `deps` (columns that get recomputed) need ordering: a
    dependency that isn't itself being recomputed is read as-is, so it can
    never be "not ready yet". Raises if the explicit `after` hints form a
    cycle -- that can only happen there, since the name-inferred stages are a
    strict 0 < 1 < 2 order and can't cycle on their own.
    """
    graph: dict[str, set[str]] = {name: set() for name in deps}
    indegree: dict[str, int] = dict.fromkeys(deps, 0)
    for name, required in deps.items():
        for dep in required:
            if dep in graph:
                graph[dep].add(name)
                indegree[name] += 1

    queue = deque(sorted(name for name, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph[node]):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(deps):
        cyclic = sorted(name for name in deps if name not in order)
        raise ValueError(
            f"Table '{table_def.name}': 'after' hints form a cycle among columns: "
            f"{', '.join(cyclic)}."
        )
    return order


def order_row_times(df, table_def: TableDef, as_of: AsOf = None):
    """Fix up temporal columns so an earlier-stage column never lands later.

    Runs unconditionally (every profile, not just non-uniform ones): a row
    updated before it was created is wrong regardless of how the timestamps
    were drawn. A column with dependencies is set, per row, to the latest of
    those dependencies plus a random gap (capped at the window's end), unless
    either side is null for that row -- a nullable column that happened to
    come back null keeps its null, rather than this pass forcing a value in.

    Leaves `df` untouched when the table holds no `after` hint and no
    recognisable created/updated/closed-style name pair, and never modifies a
    column that isn't a date/timestamp/datetime type.
    """
    columns_by_name = {column.name: column for column in table_def.columns}
    temporal_columns = [
        column
        for column in table_def.columns
        if column.name in df.columns and _column_kind(column.data_type) is not None
    ]
    if not temporal_columns:
        return df

    deps = _build_dependencies(table_def, columns_by_name, temporal_columns)
    if not deps:
        return df

    order = _topological_order(table_def, deps)
    anchor = _resolve_anchor(as_of)
    cap = datetime(anchor.year, anchor.month, anchor.day)

    for column_name in order:
        # `deps` is built only from `temporal_columns` and validated `after`
        # targets (see `_build_dependencies`), so both a dependent column and
        # everything in its `required` set are always date/timestamp columns.
        kind = _column_kind(columns_by_name[column_name].data_type)
        assert kind is not None
        required = deps[column_name]
        required_kinds: dict[str, str] = {}
        for name in required:
            other_kind = _column_kind(columns_by_name[name].data_type)
            assert other_kind is not None
            required_kinds[name] = other_kind
        scale = _TIMESTAMP_GAP_SCALE_SECONDS if kind == "timestamp" else _DATE_GAP_SCALE_DAYS

        for idx in df.index:
            if _parse_value(df.at[idx, column_name], kind) is None:
                # Already null for this row (or holds a non-temporal default):
                # nothing to reorder, and forcing a value in would undo the
                # nullability pass that put it there.
                continue

            earlier = [
                parsed
                for name, other_kind in required_kinds.items()
                for parsed in [_parse_value(df.at[idx, name], other_kind)]
                if parsed is not None
            ]
            if not earlier:
                continue
            latest_required = max(earlier)

            gap = random.expovariate(1 / scale)
            if kind == "timestamp":
                moment = latest_required + timedelta(seconds=round(gap))
            else:
                moment = latest_required + timedelta(days=max(0, round(gap)))
            if moment > cap:
                moment = cap

            df.at[idx, column_name] = _format_value(moment, kind)

    return df
