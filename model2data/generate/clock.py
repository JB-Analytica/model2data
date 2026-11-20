"""Weekdays and clock times: `days` and `times` on a date or timestamp column (spec 0.8.0).

`days` names the weekdays a column's values fall on, as a list (equal weights) or a
mapping of weekday to a positive weight; a day of the window that is another weekday
is never drawn. `times` names the times of day a timestamp falls at, as a list or a
mapping of entry to weight, an entry being an exact clock time (`"20:45"`,
`"20:45:30"`) or a range (`"09:00-18:00"`, start included, end excluded). An entry is
drawn by its weight, then the moment within it evenly, to the second.

Both replace what `business_hours` would decide for the column: `days` its weekend
thinning, `times` its working-hours profile. The run's (or the column's) `growth` and
`seasonality` still multiply into a day's weight.

`Calendar` is the draw: one weight per day of a window, each day's times clipped to
the part of the day inside the window. It draws only from the shared `random` module,
as the rest of generation does, and only for a column that carries one of the hints,
so a model without them draws exactly what it did before.
"""

from __future__ import annotations

import math
import random
import re
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import accumulate
from typing import Any, Callable, Optional

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_SECONDS = 86400
_CLOCK = re.compile(r"^(\d{2}):(\d{2})(?::(\d{2}))?$")
# A `times` entry as the schema's `clockTime` writes it: what `parse_slot` reads, but for
# the order of a range's ends.
TIME_ENTRY = (
    r"^([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?"
    r"(-(([01][0-9]|2[0-3]):[0-5][0-9](:[0-5][0-9])?|24:00(:00)?))?$"
)

# A span of seconds into a day, the end excluded, and its weight.
Slot = tuple[int, int, float]
# The whole day, evenly: what a timestamp column without `times` draws its time from.
WHOLE_DAY: tuple[Slot, ...] = ((0, DAY_SECONDS, 1.0),)


class NoDayError(ValueError):
    """A window holds no day (or no time of a day) the column's `days` and `times` allow."""


def _weight(value: Any) -> Optional[float]:
    """`value` as a weight when it is a positive, finite number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _entries(value: Any, what: str) -> list[tuple[Any, Any]]:
    """A list (each weighing 1) or a mapping, as (entry, weight) pairs."""
    if isinstance(value, list):
        if not value:
            raise ValueError(f"must list at least one {what}")
        seen: set[str] = set()
        for entry in value:
            if isinstance(entry, str):
                if entry in seen:
                    raise ValueError(f"lists {entry!r} twice")
                seen.add(entry)
        return [(entry, 1) for entry in value]
    if isinstance(value, Mapping):
        if not value:
            raise ValueError(f"must name at least one {what}")
        return list(value.items())
    raise ValueError(
        f"must be a list of {what}s or a mapping of {what} to a positive weight (got {value!r})"
    )


def parse_days(value: Any) -> tuple[float, ...]:
    """A `days` hint as seven weights, Monday first, 0 for a weekday it leaves out.

    Raises ValueError with a message naming the bad entry (to follow the column's label).
    """
    weights = [0.0] * 7
    for name, raw in _entries(value, "weekday"):
        if not isinstance(name, str) or name not in WEEKDAYS:
            raise ValueError(f"names {name!r}, which is not a weekday ({', '.join(WEEKDAYS)})")
        weight = _weight(raw)
        if weight is None:
            raise ValueError(f"weighs {name} {raw!r}: a weight is a positive number")
        weights[WEEKDAYS.index(name)] = weight
    return tuple(weights)


def _seconds(text: str, *, end: bool = False) -> Optional[int]:
    """`HH:MM` or `HH:MM:SS` as seconds into the day; `24:00` only as the end of a range."""
    found = _CLOCK.fullmatch(text)
    if found is None:
        return None
    hour, minute, second = int(found.group(1)), int(found.group(2)), int(found.group(3) or 0)
    if end and (hour, minute, second) == (24, 0, 0):
        return DAY_SECONDS
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def parse_slot(entry: Any) -> tuple[int, int]:
    """One `times` entry as `(start, end)` seconds into the day, the end excluded."""
    if not isinstance(entry, str):
        raise ValueError(
            f'has {entry!r}, which is not a clock time: write "HH:MM", "HH:MM:SS" or a range '
            '"HH:MM-HH:MM" as a string'
        )
    if "-" in entry:
        start_text, end_text = entry.split("-", 1)
        start, end = _seconds(start_text), _seconds(end_text, end=True)
        if start is None or end is None:
            raise ValueError(
                f'has {entry!r}, which is not a range of clock times: write "HH:MM-HH:MM" '
                '(seconds allowed, "24:00" as the end of the day)'
            )
        if end <= start:
            raise ValueError(
                f"has {entry!r}, a range that ends at or before it starts (a range does not "
                "wrap past midnight: split it in two)"
            )
        return start, end
    start = _seconds(entry)
    if start is None:
        raise ValueError(
            f'has {entry!r}, which is not a clock time: write "HH:MM" or "HH:MM:SS" '
            '(00:00 to 23:59:59), or a range "HH:MM-HH:MM"'
        )
    return start, start + 1


def parse_times(value: Any) -> tuple[Slot, ...]:
    """A `times` hint as slots, in the order the hint lists them.

    Raises ValueError with a message naming the bad entry (to follow the column's label).
    """
    slots = []
    for entry, raw in _entries(value, "clock time"):
        start, end = parse_slot(entry)
        weight = _weight(raw)
        if weight is None:
            raise ValueError(f"weighs {entry!r} {raw!r}: a weight is a positive number")
        slots.append((start, end, weight))
    return tuple(slots)


@dataclass(frozen=True)
class Clock:
    """The weekdays (Monday first) and times of day a column's values fall on."""

    days: tuple[float, ...]
    slots: tuple[Slot, ...]


def clip(slots: tuple[Slot, ...], low: int, high: int) -> list[Slot]:
    """The parts of `slots` within `[low, high)`, each weight scaled by the share it keeps."""
    kept = []
    for start, end, weight in slots:
        first, last = max(start, low), min(end, high)
        if first < last:
            kept.append((first, last, weight * (last - first) / (end - start)))
    return kept


def draw_time(slots: list[Slot]) -> int:
    """Seconds into a day: a slot by its weight, then a second within it evenly."""
    if len(slots) == 1:
        start, end, _ = slots[0]
    else:
        start, end, _ = random.choices(slots, weights=[slot[2] for slot in slots])[0]
    return start if end - start == 1 else start + random.randrange(end - start)


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day)


def _into(moment: datetime) -> int:
    return moment.hour * 3600 + moment.minute * 60 + moment.second


class Calendar:
    """The days of a window, `start` to `end` (both included), weighted for a column's clock.

    A day weighs `base(day)` (growth and seasonality) times its weekday's weight. On a
    timestamp column the first and last day count only the part of their times inside
    the window: a day with none weighs 0.
    """

    def __init__(
        self,
        clock: Clock,
        kind: str,
        start: datetime,
        end: datetime,
        base: Callable[[date], float],
    ) -> None:
        self.clock = clock
        self.kind = kind
        self.start, self.end = start, end
        first, last = start.date(), end.date()
        self.first = first
        self.days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
        self.full = sum(slot[2] for slot in clock.slots)
        self.raw = [base(day) * clock.days[day.weekday()] for day in self.days]
        weights = list(self.raw)
        if kind == "timestamp" and self.days:
            for index in {0, len(self.days) - 1}:
                weights[index] = self.raw[index] * self._share(self._slots(index))
        self.weights = weights
        self.added_up = list(accumulate(weights))

    def _bounds(self, index: int) -> tuple[int, int]:
        """The seconds of day `index` inside the window, the end excluded."""
        low = _into(self.start) if index == 0 else 0
        high = _into(self.end) + 1 if index == len(self.days) - 1 else DAY_SECONDS
        return low, high

    def _slots(self, index: int, low: int = 0) -> list[Slot]:
        first, high = self._bounds(index)
        return clip(self.clock.slots, max(first, low), high)

    def _share(self, slots: list[Slot]) -> float:
        return sum(slot[2] for slot in slots) / self.full

    @property
    def empty(self) -> bool:
        """Whether no day of the window can be drawn."""
        return not self.days or self.added_up[-1] <= 0

    def describe(self) -> str:
        """The window and the hints, for a message that nothing fits."""
        shown = "%Y-%m-%d %H:%M:%S" if self.kind == "timestamp" else "%Y-%m-%d"
        what = "its `days`" if self.kind == "date" else "its `days` and `times`"
        return (
            f"no moment from {self.start.strftime(shown)} to {self.end.strftime(shown)} "
            f"falls on {what}: widen the window or allow more"
        )

    def draw(self, count: int) -> list[datetime]:
        """`count` moments of the window, each day by its weight, then a time of it."""
        if self.empty:
            raise NoDayError(self.describe())
        chosen = random.choices(range(len(self.days)), cum_weights=self.added_up, k=count)
        moments = []
        for index in chosen:
            moment = _midnight(self.days[index])
            if self.kind == "timestamp":
                moment += timedelta(seconds=draw_time(self._slots(index)))
            moments.append(moment)
        return moments

    def draw_after(self, lower: datetime) -> datetime:
        """A moment on or after `lower` in the window, by the same weights; `lower` when none is."""
        if not self.days or lower.date() > self.days[-1]:
            return lower
        first = max(0, (lower.date() - self.first).days)
        if self.kind == "timestamp":
            low = _into(lower) if lower.date() == self.days[first] else 0
            head = self._slots(first, low)
            first_weight = self.raw[first] * self._share(head)
        else:
            head = []
            first_weight = self.weights[first]
        rest = self.added_up[-1] - self.added_up[first]
        total = first_weight + rest
        if total <= 0:
            return lower
        point = random.random() * total
        if point < first_weight or rest <= 0:
            index = first
        else:
            target = self.added_up[first] + (point - first_weight)
            index = min(bisect_right(self.added_up, target), len(self.days) - 1)
            index = max(index, first + 1)
            head = self._slots(index)
        moment = _midnight(self.days[index])
        if self.kind == "date":
            return moment
        return max(moment + timedelta(seconds=draw_time(head)), lower)


def clock_of(note: Optional[Mapping[str, Any]], business_hours: bool) -> Optional[Clock]:
    """The clock of a column whose hints carry `days` or `times`, None for any other.

    What the column leaves out comes from `business_hours` (the column's own, else the
    run's): without `days` a weekend weighs `weekend` against a weekday's 1 under it,
    without `times` a time of day follows `hours` (one weight per hour) under it.
    """
    note = note or {}
    if "days" not in note and "times" not in note:
        return None
    if "days" in note:
        days = parse_days(note["days"])
    elif business_hours:
        days = (1.0,) * 5 + (WEEKEND_WEIGHT,) * 2
    else:
        days = (1.0,) * 7
    if "times" in note:
        slots = parse_times(note["times"])
    elif business_hours:
        slots = tuple(
            (hour * 3600, (hour + 1) * 3600, weight) for hour, weight in enumerate(HOUR_WEIGHTS)
        )
    else:
        slots = WHOLE_DAY
    return Clock(days, slots)


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

# A weekend day's weight against a weekday's under `business_hours`.
WEEKEND_WEIGHT = 0.3
