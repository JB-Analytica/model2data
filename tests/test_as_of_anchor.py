"""A seed has to reproduce tomorrow, not only for the rest of today.

Dates and timestamps were generated relative to the wall clock, so two runs of
the same seed on different days agreed on every number and disagreed on every
date. `as_of` pins the anchor instead, and these tests hold both halves of that:
an explicit anchor survives the day changing underneath it, and the default
still follows today.
"""

from datetime import date, datetime, timedelta

import pytest

import model2data.generate.faker as faker_module
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.faker import _anchor_date, _years_before
from model2data.parse.dbml import ColumnDef, TableDef

ANCHOR = date(2024, 3, 15)


def _dated_table() -> dict[str, TableDef]:
    return {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("happened_on", "date", {"not null"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
            ],
        )
    }


def _freeze_today(monkeypatch: pytest.MonkeyPatch, today: date) -> None:
    """Move the day the process thinks it is, the way the calendar would.

    `date.today()` is the only clock generation reads, so replacing it with a
    subclass that answers a fixed day is enough to run "tomorrow" inside a
    single test.
    """

    class _FrozenDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    monkeypatch.setattr(faker_module, "date", _FrozenDate)


def _frames(**kwargs) -> dict:
    return generate_data_from_dbml(_dated_table(), [], base_rows=30, seed=7, **kwargs)


class TestAnchorHelpers:
    def test_none_means_today(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, date(2031, 8, 9))
        assert _anchor_date(None) == date(2031, 8, 9)

    def test_a_date_is_used_as_given(self):
        assert _anchor_date(ANCHOR) == ANCHOR

    def test_a_datetime_is_narrowed_to_its_day(self):
        assert _anchor_date(datetime(2024, 3, 15, 23, 59, 59)) == ANCHOR

    def test_two_years_back_is_the_same_day_of_the_year(self):
        assert _years_before(date(2024, 3, 15), 2) == date(2022, 3, 15)

    def test_the_29th_of_february_lands_on_the_28th(self):
        """The one day of the year with no counterpart two years earlier."""
        assert _years_before(date(2024, 2, 29), 2) == date(2022, 2, 28)


class TestSeededRunsReproduceAcrossDays:
    def test_an_explicit_anchor_survives_the_day_changing(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, date(2024, 3, 15))
        first = _frames(as_of=ANCHOR)["events"]

        _freeze_today(monkeypatch, date(2026, 11, 2))
        second = _frames(as_of=ANCHOR)["events"]

        assert first.equals(second)

    def test_without_an_anchor_the_dates_move_with_the_calendar(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """The behaviour `as_of` exists to opt out of, pinned so it stays opt-out."""
        _freeze_today(monkeypatch, date(2024, 3, 15))
        first = _frames()["events"]

        _freeze_today(monkeypatch, date(2026, 11, 2))
        second = _frames()["events"]

        assert not first["happened_on"].equals(second["happened_on"])
        # Same stream, so the numbers underneath the dates are untouched.
        assert first["id"].equals(second["id"])


class TestTheWindowMoves:
    def test_dates_fall_in_the_two_years_up_to_the_anchor(self):
        dates = _frames(as_of=ANCHOR)["events"]["happened_on"]

        assert dates.min() >= date(2022, 3, 15)
        assert dates.max() <= ANCHOR

    def test_a_different_anchor_shifts_the_window(self):
        earlier = _frames(as_of=date(2019, 1, 1))["events"]["happened_on"]
        later = _frames(as_of=ANCHOR)["events"]["happened_on"]

        assert earlier.max() <= date(2019, 1, 1)
        assert later.min() >= date(2022, 3, 15)
        assert earlier.max() < later.min()

    def test_timestamps_fall_in_the_year_up_to_midnight_on_the_anchor(self):
        stamps = [
            datetime.fromisoformat(value) for value in _frames(as_of=ANCHOR)["events"]["created_at"]
        ]
        midnight = datetime(2024, 3, 15)

        assert min(stamps) >= midnight - timedelta(days=365)
        assert max(stamps) <= midnight
        # Midnight, not `now()`: no sub-second component to churn a CSV.
        assert all(stamp.microsecond == 0 for stamp in stamps)

    def test_a_datetime_anchor_is_read_as_its_day(self):
        from_date = _frames(as_of=ANCHOR)["events"]
        from_datetime = _frames(as_of=datetime(2024, 3, 15, 16, 30, 45))["events"]

        assert from_date.equals(from_datetime)


class TestTheDefaultIsUnchanged:
    def test_no_anchor_still_means_today(self, monkeypatch: pytest.MonkeyPatch):
        _freeze_today(monkeypatch, date(2024, 3, 15))

        implicit = _frames()["events"]
        explicit = _frames(as_of=ANCHOR)["events"]

        assert implicit.equals(explicit)
