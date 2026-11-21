"""`days` and `times` (spec 0.8.0): the weekdays and clock times a date or timestamp falls on."""

from __future__ import annotations

import random
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.hint_tests import CLOCK_MACROS_FILE, hint_tests_for, write_hint_macros
from model2data.generate import clock, timeline
from model2data.generate.clock import Calendar, Clock
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.generate.options import TimeProfile
from model2data.model import dump, from_dict, load, to_engine
from model2data.model.dbml import from_dbml
from model2data.model.document import document_version
from model2data.model.validate import check, schema
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = date(2026, 11, 20)
KICKOFF = {"fri": 1, "sat": 3, "sun": 3}
SLOTS = {"16:00": 1, "18:30": 1, "20:45": 2}


def _league(**generate: Any) -> dict[str, Any]:
    """One table of fixtures whose `kickoff_at` carries `generate`."""
    return {
        "model2data": "0.8.0",
        "tables": {
            "fixtures": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "kickoff_at": {"type": "timestamp", "not_null": True, "generate": generate},
                }
            }
        },
    }


def _frames(document: dict, rows: int = 400, seed: int = 5, **run: Any) -> dict:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=rows, seed=seed, as_of=AS_OF, **run
    )


def _moments(values: pd.Series) -> list[datetime]:
    return [value.to_pydatetime() for value in pd.to_datetime(values.dropna())]


def _errors(document: dict) -> list[str]:
    return [str(issue) for issue in check(document) if issue.is_error]


def _has(messages: list[str], path: str, text: str) -> bool:
    return any(path in message and text in message for message in messages)


# ---------------------------------------------------------------------------
# Reading the hints
# ---------------------------------------------------------------------------
def test_days_read_as_seven_weights_monday_first():
    assert clock.parse_days(["mon", "sat"]) == (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    assert clock.parse_days({"fri": 1, "sat": 3, "sun": 2.5}) == (0, 0, 0, 0, 1, 3, 2.5)


def test_times_read_as_slots_in_the_order_given():
    assert clock.parse_times(["20:45", "09:00:30", "09:00-18:00", "22:00-24:00"]) == (
        (74700, 74701, 1.0),
        (32430, 32431, 1.0),
        (32400, 64800, 1.0),
        (79200, 86400, 1.0),
    )
    assert clock.parse_times({"18:30": 2}) == ((66600, 66601, 2.0),)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (["mon", "funday"], "names 'funday', which is not a weekday"),
        (["Mon"], "names 'Mon'"),
        ({"sat": 0}, "weighs sat 0: a weight is a positive number"),
        ({"sat": -1}, "weighs sat -1"),
        ({"sat": True}, "weighs sat True"),
        ({"sat": float("inf")}, "weighs sat inf"),
        ([], "must list at least one weekday"),
        ({}, "must name at least one weekday"),
        (["sat", "sat"], "lists 'sat' twice"),
        ("sat", "must be a list of weekdays or a mapping"),
        ([1], "names 1"),
    ],
)
def test_a_bad_days_hint_names_the_bad_entry(value, text):
    with pytest.raises(ValueError, match=re.escape(text)):
        clock.parse_days(value)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (["25:00"], "has '25:00', which is not a clock time"),
        (["9:00"], "has '9:00'"),
        (["18:30 "], "has '18:30 '"),
        (["24:00"], "has '24:00'"),
        (["12:60"], "has '12:60'"),
        (["18:00-09:00"], "a range that ends at or before it starts"),
        (["09:00-09:00"], "ends at or before it starts"),
        (["09:00-25:00"], "which is not a range of clock times"),
        (["0900-1000"], "which is not a range of clock times"),
        ([1110], "has 1110, which is not a clock time"),
        ({"18:30": 0}, "weighs '18:30' 0: a weight is a positive number"),
        (["18:30", "18:30"], "lists '18:30' twice"),
        ([], "must list at least one clock time"),
        (18, "must be a list of clock times or a mapping"),
    ],
)
def test_a_bad_times_hint_names_the_bad_entry(value, text):
    with pytest.raises(ValueError, match=re.escape(text)):
        clock.parse_times(value)


def test_the_schema_and_the_reader_agree_on_a_time_entry():
    assert schema()["$defs"]["clockTime"]["pattern"] == clock.TIME_ENTRY
    for entry in ("00:00", "23:59:59", "09:00-18:00", "22:00-24:00", "22:00:00-24:00:00"):
        assert re.fullmatch(clock.TIME_ENTRY, entry)
        clock.parse_slot(entry)
    for entry in ("24:00", "9:00", "12:60", "09:00-24:30"):
        assert not re.fullmatch(clock.TIME_ENTRY, entry)


def test_a_column_without_days_takes_them_from_business_hours():
    assert clock.clock_of({}, True) is None
    assert clock.clock_of(None, False) is None
    shop = clock.clock_of({"times": ["09:00-18:00"]}, True)
    assert shop is not None and shop.days == (1.0,) * 5 + (0.3, 0.3)
    plain = clock.clock_of({"times": ["09:00-18:00"]}, False)
    assert plain is not None and plain.days == (1.0,) * 7
    weekend = clock.clock_of({"days": ["sat"]}, True)
    assert weekend is not None and len(weekend.slots) == 24
    assert [slot[2] for slot in weekend.slots] == clock.HOUR_WEIGHTS
    whole = clock.clock_of({"days": ["sat"]}, False)
    assert whole is not None and whole.slots == clock.WHOLE_DAY


# ---------------------------------------------------------------------------
# The draw
# ---------------------------------------------------------------------------
def test_kickoffs_fall_on_their_days_and_times_and_the_weights_roughly_hold():
    frames = _frames(_league(days=KICKOFF, times=SLOTS), rows=4000)
    moments = _moments(frames["fixtures"]["kickoff_at"])
    assert len(moments) == 4000
    days = Counter(moment.strftime("%a") for moment in moments)
    times = Counter(moment.strftime("%H:%M:%S") for moment in moments)
    assert set(days) == {"Fri", "Sat", "Sun"}
    assert set(times) == {"16:00:00", "18:30:00", "20:45:00"}
    assert days["Sat"] / days["Fri"] == pytest.approx(3, rel=0.2)
    assert days["Sun"] / days["Fri"] == pytest.approx(3, rel=0.2)
    assert times["20:45:00"] / times["16:00:00"] == pytest.approx(2, rel=0.15)
    assert times["18:30:00"] / times["16:00:00"] == pytest.approx(1, rel=0.15)
    # The run's window: the year up to as_of.
    assert min(moments) >= datetime(2025, 11, 20) and max(moments) < datetime(2026, 11, 20)


def test_a_range_stays_inside_and_spreads_to_the_second():
    frames = _frames(
        _league(days=["mon", "tue", "wed", "thu", "fri", "sat"], times=["09:00-18:00"])
    )
    moments = _moments(frames["fixtures"]["kickoff_at"])
    assert all(moment.weekday() != 6 for moment in moments)
    assert all(9 <= moment.hour < 18 for moment in moments)
    assert len({moment.second for moment in moments}) > 30
    assert {moment.hour for moment in moments} == set(range(9, 18))


def test_a_date_column_takes_days_alone():
    document = {
        "model2data": "0.8.0",
        "tables": {
            "t": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "on": {"type": "date", "not_null": True, "generate": {"days": ["sat", "sun"]}},
                }
            }
        },
    }
    days = pd.to_datetime(_frames(document)["t"]["on"])
    assert set(days.dt.weekday) == {5, 6}
    assert days.min() >= pd.Timestamp("2024-11-20") and days.max() <= pd.Timestamp("2026-11-20")


def test_a_date_window_holds_its_ends_and_the_clock():
    frames = _frames(_league(min="2026-08-15 17:00", max="2027-05-15", days=KICKOFF, times=SLOTS))
    moments = _moments(frames["fixtures"]["kickoff_at"])
    assert min(moments) >= datetime(2026, 8, 15, 17) and max(moments) <= datetime(2027, 5, 15, 21)
    assert any(moment > datetime(2026, 11, 20) for moment in moments)
    assert {moment.weekday() for moment in moments} <= {4, 5, 6}
    assert {moment.strftime("%H:%M") for moment in moments} <= {"16:00", "18:30", "20:45"}


def test_on_the_edge_days_of_a_window_only_the_times_inside_it_count():
    # 15 Aug 2026 is a Saturday: from 17:00 only 18:30 and 20:45 are left of it; the window
    # ends on Sunday 16 Aug at 18:30 exactly, so 16:00 and 18:30 of it count.
    frames = _frames(
        _league(min="2026-08-15 17:00", max="2026-08-16 18:30", days=KICKOFF, times=SLOTS),
        rows=600,
    )
    seen = Counter(_moments(frames["fixtures"]["kickoff_at"]))
    assert set(seen) == {
        datetime(2026, 8, 15, 18, 30),
        datetime(2026, 8, 15, 20, 45),
        datetime(2026, 8, 16, 16),
        datetime(2026, 8, 16, 18, 30),
    }


def test_growth_still_weighs_the_days_it_allows():
    frames = _frames(_league(days=["sat"], growth=1.0), rows=3000)
    moments = _moments(frames["fixtures"]["kickoff_at"])
    early = sum(moment < datetime(2026, 5, 20) for moment in moments)
    assert {moment.weekday() for moment in moments} == {5}
    assert early < len(moments) - early


def test_a_window_that_leans_on_as_of_and_has_no_allowed_day_fails_when_generating():
    # Validation cannot know the window without the run's day: generation names the column.
    document = _league(min="2026-11-17", days=["sat"])
    assert _errors(document) == []
    with pytest.raises(ValueError, match=r"fixtures\.kickoff_at: no moment from 2026-11-17"):
        _frames(document)


def test_after_lands_on_the_clock_after_what_it_follows():
    document = _league(min="2026-08-15", max="2027-05-15", days=KICKOFF, times=SLOTS)
    columns = document["tables"]["fixtures"]["columns"]
    columns["report_at"] = {
        "type": "timestamp",
        "not_null": True,
        "generate": {"after": "kickoff_at", "days": ["mon", "tue"], "times": ["09:00-12:00"]},
    }
    columns["paid_on"] = {
        "type": "date",
        "not_null": True,
        "generate": {"after": "kickoff_at", "days": ["wed"]},
    }
    fixtures = _frames(document, rows=500)["fixtures"]
    kickoff = pd.to_datetime(fixtures["kickoff_at"])
    report = pd.to_datetime(fixtures["report_at"])
    paid = pd.to_datetime(fixtures["paid_on"])
    assert (report >= kickoff).all() and (paid >= kickoff.dt.normalize()).all()
    assert set(report.dt.weekday) == {0, 1}
    assert report.dt.hour.between(9, 11).all()
    assert set(paid.dt.weekday) == {2}


def test_a_child_follows_its_parent_on_its_own_clock():
    document = {
        "model2data": "0.8.0",
        "tables": {
            "fixtures": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "kickoff_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {"min": "2026-12-01", "max": "2027-02-01", "days": KICKOFF},
                    },
                }
            },
            "tickets": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "fixture_id": {
                        "type": "bigint",
                        "not_null": True,
                        "references": "fixtures.id",
                    },
                    "sold_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {
                            "min": "2026-11-25",
                            "max": "2027-02-05",
                            "after": "fixtures.kickoff_at",
                            "days": ["mon", "tue", "wed", "thu", "fri", "sat"],
                            "times": ["09:00-18:00"],
                        },
                    },
                    "sold_on": {
                        "type": "date",
                        "not_null": True,
                        "generate": {
                            "min": "2026-11-25",
                            "max": "2027-02-28",
                            "after": "fixtures.kickoff_at",
                            "days": ["sun"],
                        },
                    },
                }
            },
        },
    }
    frames = _frames(document, rows=300, seed=3, row_overrides={"fixtures": 30})
    merged = frames["tickets"].merge(frames["fixtures"], left_on="fixture_id", right_on="id")
    sold = pd.to_datetime(merged["sold_at"])
    kickoff = pd.to_datetime(merged["kickoff_at"])
    assert (sold >= kickoff).all()
    assert sold.dt.weekday.isin([0, 1, 2, 3, 4, 5]).all() and sold.dt.hour.between(9, 17).all()
    on = pd.to_datetime(merged["sold_on"])
    assert (on >= kickoff.dt.normalize()).all() and set(on.dt.weekday) == {6}


def test_when_fills_on_the_clock():
    # Played before the run's day, so a replay has room between the game and as_of.
    document = _league(min="2026-01-01", max="2026-10-01", days=KICKOFF, times=SLOTS)
    document["enums"] = {"state": ["played", "postponed"]}
    columns = document["tables"]["fixtures"]["columns"]
    columns["state"] = {"type": "state", "not_null": True}
    columns["replayed_at"] = {
        "type": "timestamp",
        "generate": {
            "after": "kickoff_at",
            "when": {"state": ["postponed"]},
            "days": ["wed"],
            "times": ["19:45"],
        },
    }
    fixtures = _frames(document, rows=300)["fixtures"]
    postponed = fixtures[fixtures["state"] == "postponed"]
    replayed = pd.to_datetime(postponed["replayed_at"])
    assert (
        replayed.notna().all()
        and fixtures[fixtures["state"] == "played"]["replayed_at"].isna().all()
    )
    assert set(replayed.dt.weekday) == {2}
    assert set(replayed.dt.strftime("%H:%M:%S")) == {"19:45:00"}
    assert (replayed >= pd.to_datetime(postponed["kickoff_at"])).all()


def test_a_rule_draws_again_on_the_clock():
    document = _league(days=KICKOFF, times=SLOTS)
    columns = document["tables"]["fixtures"]["columns"]
    columns["home"] = {"type": "int", "generate": {"min": 1, "max": 3}}
    columns["away"] = {"type": "int", "generate": {"min": 1, "max": 3}}
    columns["kickoff_at"]["generate"]["min"] = "2026-01-01"
    document["tables"]["fixtures"]["rules"] = [
        {"kickoff_at": {"gt": "2026-06-01 00:00:00"}},
    ]
    moments = _moments(_frames(document)["fixtures"]["kickoff_at"])
    assert all(moment > datetime(2026, 6, 1) for moment in moments)
    assert {moment.weekday() for moment in moments} <= {4, 5, 6}
    assert {moment.strftime("%H:%M") for moment in moments} <= {"16:00", "18:30", "20:45"}


def test_the_same_seed_gives_the_same_values_and_another_seed_others():
    document = _league(min="2026-08-15", max="2027-05-15", days=KICKOFF, times=SLOTS)
    first = _frames(document)["fixtures"]
    assert first.equals(_frames(document)["fixtures"])
    assert not first.equals(_frames(document, seed=6)["fixtures"])


def test_a_model_without_the_hints_draws_what_it_drew_before():
    # The clocked branch is never reached: the same frames with and without the new module.
    plain = _league(min="2026-08-15", max="2027-05-15")
    plain["model2data"] = "0.7.0"
    before = _frames(plain)["fixtures"]
    original = timeline.column_clock
    try:
        timeline.column_clock = lambda column, profile: None  # type: ignore[assignment]
        again = _frames(plain)["fixtures"]
    finally:
        timeline.column_clock = original
    assert before.equals(again)


# ---------------------------------------------------------------------------
# Calendar and placement, directly
# ---------------------------------------------------------------------------
def _clock(days: Any = None, times: Any = None) -> Clock:
    found = clock.clock_of(
        {**({"days": days} if days else {}), **({"times": times} if times else {})}, False
    )
    assert found is not None
    return found


def test_calendar_draw_after_keeps_to_the_day_and_after_the_moment():
    random.seed(1)
    games = _clock(KICKOFF, SLOTS)
    calendar = Calendar(
        games, "timestamp", datetime(2026, 8, 1), datetime(2026, 8, 31), lambda d: 1
    )
    for _ in range(200):
        lower = (
            datetime(2026, 8, 1) + (datetime(2026, 8, 31) - datetime(2026, 8, 1)) * random.random()
        )
        moment = calendar.draw_after(lower)
        assert moment >= lower
        if moment != lower:
            assert moment.weekday() in (4, 5, 6) and moment.strftime("%H:%M") in (
                "16:00",
                "18:30",
                "20:45",
            )
    # After the window, or after its last allowed moment: what it follows.
    assert calendar.draw_after(datetime(2026, 9, 2)) == datetime(2026, 9, 2)
    late = datetime(2026, 8, 30, 21)
    assert calendar.draw_after(late) == late
    # The same day still has a later slot.
    saturday = datetime(2026, 8, 29, 17)
    assert calendar.draw_after(saturday).date() >= saturday.date()


def test_calendar_draw_after_on_dates_and_an_empty_window():
    random.seed(2)
    weekend = _clock(["sat", "sun"])
    dates = Calendar(weekend, "date", datetime(2026, 8, 3), datetime(2026, 8, 31), lambda d: 1)
    for _ in range(50):
        moment = dates.draw_after(datetime(2026, 8, 10, 15))
        assert moment.weekday() in (5, 6) and moment.time() == datetime.min.time()
    empty = Calendar(weekend, "date", datetime(2026, 8, 3), datetime(2026, 8, 7), lambda d: 1)
    assert empty.empty
    assert empty.draw_after(datetime(2026, 8, 4)) == datetime(2026, 8, 4)
    with pytest.raises(clock.NoDayError, match="falls on its `days`: widen"):
        empty.draw(1)
    nothing = Calendar(weekend, "date", datetime(2026, 8, 7), datetime(2026, 8, 3), lambda d: 1)
    assert nothing.empty and nothing.draw_after(datetime(2026, 8, 4)) == datetime(2026, 8, 4)


def test_place_after_on_the_clock_and_its_fallbacks():
    random.seed(3)
    games = _clock(KICKOFF, SLOTS)
    lower = datetime(2026, 8, 12, 12)
    for _ in range(100):
        moment = timeline.place_after(lower, "timestamp", datetime(2026, 9, 30), False, games)
        assert moment > lower and moment.weekday() in (4, 5, 6)
        assert moment.strftime("%H:%M") in ("16:00", "18:30", "20:45")
    # A window too short for the gaps: drawn among the allowed moments that are left.
    narrow = timeline.place_after(
        datetime(2026, 8, 14, 17), "timestamp", datetime(2026, 8, 14, 19), False, games
    )
    assert narrow == datetime(2026, 8, 14, 18, 30)
    # No allowed moment before the bound: what it follows, as a window with no room gives.
    stuck = datetime(2026, 8, 10, 9)
    assert timeline.place_after(stuck, "timestamp", datetime(2026, 8, 12), False, games) == stuck
    assert timeline.place_after(stuck, "timestamp", stuck, False, games) == stuck
    # Dates: the weekday, on or after the day it follows.
    sunday = _clock(["sun"])
    for _ in range(50):
        moment = timeline.place_after(lower, "date", datetime(2026, 12, 31), False, sunday)
        assert moment.weekday() == 6 and moment.date() >= lower.date()
    assert timeline.place_after(lower, "date", datetime(2026, 8, 13), False, sunday) == lower


def test_a_gap_that_ends_past_the_days_last_time_tries_again(monkeypatch):
    # A Saturday at 21:00, after the 20:45 kick-off: a gap that stays on that day has no
    # time left, so the next gap is drawn, a week on.
    gaps = iter([timedelta(0), timedelta(days=7)])
    monkeypatch.setattr(timeline, "gap", lambda kind: next(gaps))
    saturday = _clock(["sat"], ["20:45"])
    lower = datetime(2026, 8, 15, 21)
    moment = timeline.place_after(lower, "timestamp", datetime(2026, 9, 30), False, saturday)
    assert moment == datetime(2026, 8, 22, 20, 45)


def test_clocked_values_take_growth_and_seasonality_but_not_business_hours():
    random.seed(4)
    moments = timeline.clocked_values(
        500,
        "timestamp",
        _clock(["mon"], ["03:00"]),
        TimeProfile(business_hours=True, growth=0.5),
        datetime(2026, 1, 1),
        datetime(2026, 12, 31, 23, 59, 59),
    )
    assert {(moment.weekday(), moment.hour) for moment in moments} == {(0, 3)}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validate_reports_each_misuse_with_its_path():
    def errors_for(**generate: Any) -> list[str]:
        return _errors(_league(**generate))

    assert errors_for(days=KICKOFF, times=SLOTS) == []
    assert _has(errors_for(days=["funday"]), "kickoff_at.generate.days", "")
    assert _has(errors_for(days={"sat": 0}), "kickoff_at.generate.days", "")
    assert _has(errors_for(times=["25:00"]), "kickoff_at.generate.times", "")
    assert _has(
        errors_for(times=["18:00-09:00"]),
        "kickoff_at.generate.times",
        "a range that ends at or before it starts",
    )
    assert _has(
        errors_for(days=["sat"], business_hours=True),
        "kickoff_at.generate.days",
        "and `business_hours` both decide when kickoff_at's values fall",
    )
    assert _has(
        errors_for(times=["09:00"], business_hours=True),
        "kickoff_at.generate.times",
        "drop `business_hours`",
    )
    assert errors_for(days=["sat"], business_hours=False) == []


def test_validate_reports_times_on_a_date_and_either_on_a_key():
    document = _league()
    columns = document["tables"]["fixtures"]["columns"]
    columns["on"] = {"type": "date", "generate": {"days": ["sat"], "times": ["16:00"]}}
    columns["slot"] = {"type": "timestamp", "unique": True, "generate": {"days": ["sat"]}}
    messages = _errors(document)
    assert _has(messages, "on.generate.times", "applies to a timestamp column only: on is a date")
    assert not _has(messages, "on.generate.days", "")
    assert _has(messages, "slot.generate.days", "cannot be set on slot, a key, unique")


def test_validate_reports_a_window_with_no_allowed_moment():
    weekdays_only = _league(min="2026-11-17", max="2026-11-20", days=["sat"])
    assert _has(
        _errors(weekdays_only),
        "kickoff_at.generate.days",
        "no moment from 2026-11-17 00:00:00 to 2026-11-20 23:59:59 falls on its `days` and `times`",
    )
    # The last Saturday ends before the only time: still nothing.
    late = _league(min="2026-11-21", max="2026-11-21 15:00", days=["sat"], times=["16:00"])
    assert _has(_errors(late), "kickoff_at.generate.days", "no moment")
    # One bound and the run's day fix the window too.
    leaning = _league(min="2026-11-17", days=["sat"])
    leaning["run"] = {"as_of": "2026-11-20"}  # the window ends at midnight of Friday 20th
    assert _has(_errors(leaning), "kickoff_at.generate.days", "no moment")
    # A window that is itself wrong is reported once, as a window.
    inverted = _league(min="2026-11-24", max="2026-11-17", days=["sat"])
    assert not _has(_errors(inverted), "generate.days", "no moment")
    assert _errors(_league(min="2026-11-17", max="2026-11-21", days=["sat"])) == []


def test_derive_and_rollup_cannot_carry_them():
    document = _league(days=["sat"])
    columns = document["tables"]["fixtures"]["columns"]
    columns["day"] = {
        "type": "date",
        "generate": {"derive": {"date_of": "kickoff_at"}, "days": ["sat"]},
    }
    assert _has(_errors(document), "day.generate.days", "`derive` decides its value")


def test_days_and_times_need_a_0_8_document_and_older_ones_still_read():
    older = _league(days=["sat"], times=["16:00"])
    older["model2data"] = "0.7.0"
    messages = _errors(older)
    assert _has(messages, "kickoff_at.generate.days", "`days` is spec 0.8.0")
    assert _has(messages, "kickoff_at.generate.times", "`times` is spec 0.8.0")
    plain = _league(min="2026-08-15")
    plain["model2data"] = "0.7.0"
    assert _errors(plain) == []
    assert str(document_version(from_dict(plain))) == "0.7.0"
    plain["model2data"] = 0.8
    assert _errors(plain) == []


def test_a_document_that_uses_them_is_written_as_0_8_and_round_trips():
    model = from_dict(_league(days=KICKOFF, times=SLOTS))
    assert str(document_version(model)) == "0.8.0"
    again = load(dump(model))
    assert again == model
    assert again.tables["fixtures"].columns["kickoff_at"].generate["times"] == SLOTS
    # A model read as an earlier version (from DBML, say) is written as 0.8.0 once it uses them.
    assert document_version(replace(model, version="0.7.0")) == "0.8.0"


def test_dbml_with_them_converts_to_a_0_8_document():
    text = """
Table fixtures {
  id bigint [pk]
  kickoff_at timestamp [note: '{"days": ["sat", "sun"], "times": ["15:00"]}']
}
"""
    model = from_dbml(text)
    assert str(document_version(model)) == "0.8.0"


# ---------------------------------------------------------------------------
# The engine's own checks (DBML notes, hand-built tables)
# ---------------------------------------------------------------------------
def _tables(*columns: ColumnDef) -> dict[str, TableDef]:
    return {"t": TableDef(name="t", columns=[ColumnDef("id", "int", {"pk"}), *columns])}


def _refused(tables: dict[str, TableDef], pattern: str) -> None:
    with pytest.raises(ValueError, match=pattern):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=AS_OF)


@pytest.mark.parametrize(
    ("column", "pattern"),
    [
        (ColumnDef("n", "int", set(), note={"days": ["sat"]}), '"days" only applies to date'),
        (ColumnDef("n", "int", set(), note={"times": ["16:00"]}), '"times" only applies to date'),
        (
            ColumnDef("d", "date", set(), note={"times": ["16:00"]}),
            '"times" only applies to timestamp columns: a date has no time of day',
        ),
        (
            ColumnDef("d", "date", {"unique"}, note={"days": ["sat"]}),
            '"days" cannot be used on a key, unique or foreign-key column',
        ),
        (
            ColumnDef("d", "date", set(), note={"days": ["sat"], "business_hours": True}),
            '"days" and "business_hours" both decide',
        ),
        (
            ColumnDef("d", "date", set(), note={"days": ["satur"]}),
            "t.d: \"days\" names 'satur', which is not a weekday",
        ),
        (
            ColumnDef("s", "timestamp", set(), note={"times": ["18:00-09:00"]}),
            "t.s: \"times\" has '18:00-09:00', a range that ends at or before it starts",
        ),
        (
            ColumnDef("s", "timestamp", set(), note={"times": {"18:00": -2}}),
            "weighs '18:00' -2",
        ),
        (
            ColumnDef(
                "s",
                "timestamp",
                set(),
                note={"days": ["sat"], "min": "2026-11-17", "max": "2026-11-20"},
            ),
            "t.s: no moment from 2026-11-17 00:00:00 to 2026-11-20 23:59:59",
        ),
    ],
)
def test_the_engine_refuses_a_misused_clock(column, pattern):
    _refused(_tables(column), pattern)


def test_the_engine_refuses_them_beside_derive_and_rollup():
    derived = ColumnDef("d", "date", set(), note={"derive": {"date_of": "s"}, "days": ["sat"]})
    _refused(_tables(ColumnDef("s", "timestamp", set()), derived), 'cannot also have "days"')


def test_days_refuses_a_table_with_them_that_moves_on():
    document = _league(days=["sat"])
    document["tables"]["fixtures"]["incremental"] = {"new_per_day": 3}
    with pytest.raises(ValueError, match='"days" places the first day\'s values'):
        generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)
    document = _league(times=["16:00"])
    document["tables"]["fixtures"]["incremental"] = {"new_per_day": 3}
    with pytest.raises(ValueError, match='drop "times"'):
        generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)


# ---------------------------------------------------------------------------
# dbt tests
# ---------------------------------------------------------------------------
def test_days_and_times_write_their_dbt_tests():
    document = _league(days=KICKOFF, times={"20:45": 2, "09:00-18:00": 1, "22:00-24:00": 1})
    columns = document["tables"]["fixtures"]["columns"]
    columns["on"] = {"type": "date", "generate": {"days": ["sun", "mon"]}}
    found = {(t.column, t.test): t.arguments for t in hint_tests_for(from_dict(document))}
    assert found == {
        ("kickoff_at", "model2data_weekdays"): {"days": ["fri", "sat", "sun"]},
        ("kickoff_at", "model2data_times_of_day"): {
            "times": ["20:45:00", "09:00:00-18:00:00", "22:00:00-24:00:00"]
        },
        ("on", "model2data_weekdays"): {"days": ["mon", "sun"]},
    }


def test_no_dbt_test_for_a_clock_that_cannot_hold():
    tables = _tables(
        ColumnDef("n", "int", set(), note={"days": ["sat"]}),
        ColumnDef("d", "date", set(), note={"times": ["16:00"]}),
        ColumnDef("s", "timestamp", set(), note={"days": ["funday"]}),
    )
    assert hint_tests_for(tables) == []


DBT = shutil.which("dbt")


@pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")
def test_the_dbt_tests_pass_on_the_data_and_catch_a_value_off_the_clock(tmp_path, monkeypatch):
    document = _league(days=KICKOFF, times={"18:30": 1, "20:45": 2, "12:00-14:00": 1})
    path = tmp_path / "league.model2data.yml"
    path.write_text(yaml.safe_dump(document))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["--file", str(path), "--rows", "80", "--seed", "1", "--name", "lg"]
        + ["--hint-tests", "error"],
    )
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_lg"

    def build() -> subprocess.CompletedProcess:
        return subprocess.run(
            [DBT or "dbt", "build", "--profiles-dir", "."],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=240,
        )

    clean = build()
    assert clean.returncode == 0, clean.stdout + clean.stderr
    seed = project / "seeds" / "raw" / "fixtures.csv"
    lines = seed.read_text().splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ",2026-11-17 03:00:00"  # a Tuesday, at night
    seed.write_text("\n".join(lines) + "\n")
    broken = build()
    assert broken.returncode != 0
    for test in ("model2data_weekdays", "model2data_times_of_day"):
        assert any(test in line and "FAIL" in line for line in broken.stdout.splitlines()), (
            test,
            broken.stdout,
        )


def test_the_clock_macros_are_written_only_beside_a_test_that_needs_them(tmp_path):
    write_hint_macros(tmp_path / "plain")
    assert not (tmp_path / "plain" / "macros" / CLOCK_MACROS_FILE).exists()
    write_hint_macros(tmp_path / "clocked", clock=True)
    sql = (tmp_path / "clocked" / "macros" / CLOCK_MACROS_FILE).read_text()
    for name in ("model2data_weekdays", "model2data_times_of_day"):
        assert f"{{% test {name}(" in sql
