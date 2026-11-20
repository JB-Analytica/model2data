"""Date windows: `min` and `max` on a date or timestamp column (spec 0.7.0).

Without them every date falls in the two years up to `as_of` and every timestamp in
the year up to it. With `min` and/or `max` (ISO 8601 strings) the column's values
fall between them, and the bounds may lie after `as_of`, so a schedule can sit
ahead of the run's day:

- Both given: the window is `[min, max]`.
- Only `min`: the window runs from `min` to the end of the default window (`as_of`);
  a `min` on or after `as_of` has none of it left, so the window is the default's
  length starting at `min`.
- Only `max`: the window runs from the start of the default window to `max`; a
  `max` on or before that start gives the default's length ending at `max`.
- A date-only `max` on a timestamp column is the end of that day (23:59:59), so
  `max: "2027-04-30"` includes the 30th. A date column holds the day of either form.

`growth`, `seasonality` and `business_hours` shape the draw inside the window as
they do inside the default one, `growth` running from the window's start to its end.
A column without either bound is drawn as it always was.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Optional

from model2data.generate import comparison as cmp

DATE_SPAN_DAYS = 2 * 365
TIMESTAMP_SPAN_DAYS = 365
_END_OF_DAY = timedelta(days=1, seconds=-1)


def parse_bound(value: Any) -> Optional[tuple[datetime, bool]]:
    """`value` as `(moment, date_only)` when it is an ISO 8601 date or timestamp string."""
    if not isinstance(value, str):
        return None
    moment = cmp.parse_temporal("timestamp", value)
    return None if moment is None else (moment, len(value) == 10)


def has_window(note: Optional[Mapping[str, Any]]) -> bool:
    """Whether a temporal column's hints carry a `min` or a `max` (as dates)."""
    return note is not None and any(isinstance(note.get(key), str) for key in ("min", "max"))


def bounds(
    note: Optional[Mapping[str, Any]], kind: str
) -> tuple[Optional[datetime], Optional[datetime]]:
    """The column's explicit `min` and `max` as moments, None for one left out."""
    note = note or {}
    low = parse_bound(note.get("min"))
    high = parse_bound(note.get("max"))
    start = low[0] if low is not None else None
    end = None
    if high is not None:
        moment, date_only = high
        end = moment + _END_OF_DAY if date_only and kind == "timestamp" else moment
    if kind == "date":
        start = datetime(start.year, start.month, start.day) if start is not None else None
        end = datetime(end.year, end.month, end.day) if end is not None else None
    return start, end


def default_window(kind: str, anchor: date) -> tuple[datetime, datetime]:
    """The window a column without `min` and `max` is drawn in: up to midnight of `anchor`."""
    midnight = datetime(anchor.year, anchor.month, anchor.day)
    if kind == "date":
        try:
            start = midnight.replace(year=midnight.year - 2)
        except ValueError:
            start = midnight.replace(year=midnight.year - 2, day=28)
        return start, midnight
    return midnight - timedelta(days=TIMESTAMP_SPAN_DAYS), midnight


def window(note: Optional[Mapping[str, Any]], kind: str, anchor: date) -> tuple[datetime, datetime]:
    """The `(start, end)` a column's values fall in, `end` included."""
    low, high = bounds(note, kind)
    start, end = default_window(kind, anchor)
    span = timedelta(days=DATE_SPAN_DAYS if kind == "date" else TIMESTAMP_SPAN_DAYS)
    if low is not None and high is not None:
        return low, high
    if low is not None:
        return low, end if end > low else low + span
    if high is not None:
        return (start if start < high else high - span), high
    return start, end
