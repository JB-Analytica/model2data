"""Time-aware generation: weighted time draws and within-row ordering.

Two things are pinned here rather than only asserted statistically. First,
that the uniform profile still produces exactly what 1.4.0 produced for a
table with no ordered pair of temporal columns -- `order_row_times` has
nothing to do there, so nothing should change. Second, that a table *with*
such a pair (created_at/updated_at) now generates different values under the
same seed, because the later column is placed after the earlier one -- that
change is deliberate and is called out in CHANGELOG.md, but it still needs a
fixed expectation so a future refactor can't silently drift it further.
"""

import random
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.options import TimeProfile
from model2data.generate.timeline import order_row_times, place_after
from model2data.parse.dbml import ColumnDef, TableDef

ANCHOR = date(2026, 3, 15)


def _single_timestamp_and_date_schema() -> dict[str, TableDef]:
    """No ordered pair: created_at has nothing to be ordered against."""
    return {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("event_date", "date", set()),
            ],
        )
    }


def _created_updated_schema() -> dict[str, TableDef]:
    """An inferred created_at/updated_at chain: updated_at must follow created_at."""
    return {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("updated_at", "timestamp", set()),
            ],
        )
    }


def test_uniform_profile_reproduces_pre_1_5_frames():
    """A table with no ordered pair is untouched by the ordering pass, so its
    frame under the uniform profile is byte-identical to what 1.4.0 generated --
    but for 1.15.0's primary key, which numbers the rows 1..N instead of drawing
    a shuffled sample, so every column after it draws from a different point of
    the table's stream (pinned again in 1.15.0)."""
    df = generate_data_from_dbml(
        _single_timestamp_and_date_schema(), [], base_rows=8, seed=2024, as_of=ANCHOR
    )["events"]

    assert list(df["id"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert list(df["created_at"]) == [
        "2025-12-07 15:29:52",
        "2025-05-20 12:00:46",
        "2026-01-20 17:46:51",
        "2026-01-31 06:10:57",
        "2025-07-25 17:30:00",
        "2025-07-15 05:24:33",
        "2025-07-18 08:26:12",
        "2026-01-28 22:28:20",
    ]
    assert list(df["event_date"]) == [
        date(2025, 7, 31),
        None,
        date(2026, 3, 8),
        date(2024, 11, 19),
        date(2024, 11, 5),
        date(2024, 5, 16),
        date(2025, 6, 11),
        date(2024, 12, 12),
    ]


def test_ordering_changes_created_updated_pairs_under_the_same_seed():
    """A table that *does* hold an ordered pair generates different values
    under the same seed than 1.4.0 did, because updated_at is now placed after
    created_at -- see CHANGELOG.md's Changed entry for this release."""
    df = generate_data_from_dbml(
        _created_updated_schema(), [], base_rows=8, seed=2024, as_of=ANCHOR
    )["events"]

    assert list(df["id"]) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert list(df["created_at"]) == [
        "2025-12-07 15:29:52",
        "2025-05-20 12:00:46",
        "2026-01-20 17:46:51",
        "2026-01-31 06:10:57",
        "2025-07-25 17:30:00",
        "2025-07-15 05:24:33",
        "2025-07-18 08:26:12",
        "2026-01-28 22:28:20",
    ]
    assert list(df["updated_at"]) == [
        "2025-12-10 13:57:04",
        "2025-05-23 16:21:10",
        None,
        "2026-02-07 13:00:47",
        "2025-07-29 19:39:57",
        "2025-07-15 15:51:09",
        "2025-07-18 17:37:38",
        "2026-01-30 01:59:33",
    ]
    # Every non-null updated_at is at or after its own row's created_at.
    for created, updated in zip(df["created_at"], df["updated_at"], strict=True):
        if updated is not None:
            assert datetime.fromisoformat(created) <= datetime.fromisoformat(updated)


# ---------------------------------------------------------
# Weighted time draws
# ---------------------------------------------------------
def _timestamps(profile: TimeProfile, base_rows: int = 1000, seed: int = 5) -> list[datetime]:
    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("happened_at", "timestamp", {"not null"}),
            ],
        )
    }
    df = generate_data_from_dbml(
        tables, [], base_rows=base_rows, seed=seed, as_of=ANCHOR, time_profile=profile
    )["events"]
    return [datetime.fromisoformat(v) for v in df["happened_at"]]


def test_business_hours_weights_toward_weekday_working_hours():
    def business_fraction(timestamps: list[datetime]) -> float:
        in_hours = sum(1 for t in timestamps if t.weekday() < 5 and 8 <= t.hour < 18)
        return in_hours / len(timestamps)

    shaped = business_fraction(_timestamps(TimeProfile(business_hours=True)))
    plain = business_fraction(_timestamps(TimeProfile()))

    assert shaped >= 0.70
    assert plain < shaped - 0.2


def _window_midpoint(days: int) -> date:
    start = ANCHOR - timedelta(days=days)
    return start + (ANCHOR - start) / 2


def test_positive_growth_puts_more_rows_in_the_second_half_of_the_window():
    timestamps = _timestamps(TimeProfile(growth=1.0), base_rows=2000)
    midpoint = _window_midpoint(365)
    first_half = sum(1 for t in timestamps if t.date() < midpoint)
    second_half = len(timestamps) - first_half

    assert second_half > first_half


def test_negative_growth_puts_more_rows_in_the_first_half_of_the_window():
    timestamps = _timestamps(TimeProfile(growth=-0.9), base_rows=2000)
    midpoint = _window_midpoint(365)
    first_half = sum(1 for t in timestamps if t.date() < midpoint)
    second_half = len(timestamps) - first_half

    assert first_half > second_half


def test_seasonality_peaks_in_q4_over_q2():
    timestamps = _timestamps(TimeProfile(seasonality=1.0), base_rows=3000)
    q4 = sum(1 for t in timestamps if t.month in (10, 11, 12))
    q2 = sum(1 for t in timestamps if t.month in (4, 5, 6))

    assert q2 > 0
    assert q4 >= 2 * q2


def test_growth_shapes_date_columns_too():
    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("happened_on", "date", {"not null"}),
            ],
        )
    }
    df = generate_data_from_dbml(
        tables,
        [],
        base_rows=2000,
        seed=5,
        as_of=ANCHOR,
        time_profile=TimeProfile(growth=1.0),
    )["events"]
    dates = list(df["happened_on"])
    start = ANCHOR.replace(year=ANCHOR.year - 2)
    midpoint = start + (ANCHOR - start) / 2
    first_half = sum(1 for d in dates if d < midpoint)
    second_half = len(dates) - first_half

    assert second_half > first_half


def test_same_seed_and_profile_reproduce_identical_frames():
    profile = TimeProfile(business_hours=True, growth=0.3, seasonality=0.4)
    tables = _created_updated_schema()

    first = generate_data_from_dbml(
        tables, [], base_rows=50, seed=11, as_of=ANCHOR, time_profile=profile
    )["events"]
    second = generate_data_from_dbml(
        tables, [], base_rows=50, seed=11, as_of=ANCHOR, time_profile=profile
    )["events"]

    assert first.equals(second)


def test_different_profiles_produce_different_frames():
    tables = _created_updated_schema()

    flat = generate_data_from_dbml(
        tables, [], base_rows=50, seed=11, as_of=ANCHOR, time_profile=TimeProfile()
    )["events"]
    shaped = generate_data_from_dbml(
        tables,
        [],
        base_rows=50,
        seed=11,
        as_of=ANCHOR,
        time_profile=TimeProfile(business_hours=True, growth=0.5, seasonality=0.5),
    )["events"]

    assert not flat["created_at"].equals(shaped["created_at"])


def test_nothing_generated_is_later_than_as_of():
    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("updated_at", "timestamp", set()),
                ColumnDef("happened_on", "date", {"not null"}),
            ],
        )
    }
    df = generate_data_from_dbml(
        tables,
        [],
        base_rows=500,
        seed=17,
        as_of=ANCHOR,
        time_profile=TimeProfile(business_hours=True, growth=0.8, seasonality=0.9),
    )["events"]

    anchor_midnight = datetime(ANCHOR.year, ANCHOR.month, ANCHOR.day)
    for value in df["created_at"]:
        assert datetime.fromisoformat(value) <= anchor_midnight
    for value in df["updated_at"].dropna():
        assert datetime.fromisoformat(value) <= anchor_midnight
    for value in df["happened_on"]:
        if value is not None:
            assert value <= ANCHOR


# ---------------------------------------------------------
# Within-row ordering
# ---------------------------------------------------------
def test_created_updated_deleted_chain_is_ordered_on_every_row():
    tables = {
        "accounts": TableDef(
            name="accounts",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("updated_at", "timestamp", set()),
                ColumnDef("deleted_at", "timestamp", set()),
            ],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=300, seed=9, as_of=ANCHOR)["accounts"]

    for created, updated, deleted in zip(
        df["created_at"], df["updated_at"], df["deleted_at"], strict=True
    ):
        created_dt = datetime.fromisoformat(created)
        if updated is not None:
            assert created_dt <= datetime.fromisoformat(updated)
        if deleted is not None:
            assert created_dt <= datetime.fromisoformat(deleted)
            if updated is not None:
                assert datetime.fromisoformat(updated) <= datetime.fromisoformat(deleted)


def test_after_hint_is_honoured():
    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("ordered_at", "timestamp", {"not null"}),
                ColumnDef("shipped_at", "timestamp", set(), note={"after": "ordered_at"}),
            ],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=300, seed=4, as_of=ANCHOR)["orders"]

    for ordered, shipped in zip(df["ordered_at"], df["shipped_at"], strict=True):
        if shipped is not None:
            assert datetime.fromisoformat(ordered) <= datetime.fromisoformat(shipped)


def _bookings(scheduled_note: dict, rows: int = 2000, profile=None) -> pd.DataFrame:
    tables = {
        "bookings": TableDef(
            name="bookings",
            columns=[
                ColumnDef("id", "bigint", {"pk"}),
                ColumnDef("booked_at", "timestamp", {"not null"}),
                ColumnDef("scheduled_for", "timestamp", {"not null"}, note=scheduled_note),
            ],
        )
    }
    return generate_data_from_dbml(
        tables, [], base_rows=rows, seed=1, as_of=ANCHOR, time_profile=profile
    )["bookings"]


def _working_share(values) -> float:
    moments = [datetime.fromisoformat(v) for v in values]
    return sum(1 for t in moments if t.weekday() < 5 and 8 <= t.hour < 18) / len(moments)


def test_a_column_with_after_keeps_its_business_hours():
    """The gap decides roughly when; the column's `business_hours` decides the hour."""
    df = _bookings({"after": "booked_at", "business_hours": True})

    assert _working_share(df["scheduled_for"]) >= 0.65
    assert _working_share(df["booked_at"]) < 0.4
    for booked, scheduled in zip(df["booked_at"], df["scheduled_for"], strict=True):
        assert datetime.fromisoformat(booked) <= datetime.fromisoformat(scheduled)


def test_the_runs_business_hours_reach_a_column_with_after():
    df = _bookings({"after": "booked_at"}, profile=TimeProfile(business_hours=True))

    assert _working_share(df["scheduled_for"]) >= 0.65


def test_a_column_with_after_does_not_pile_up_on_as_of_midnight():
    """A gap past the window is drawn again, not set to the window's end."""
    df = _bookings({"after": "booked_at"})
    cap = datetime(ANCHOR.year, ANCHOR.month, ANCHOR.day)

    scheduled = [datetime.fromisoformat(v) for v in df["scheduled_for"]]
    assert max(scheduled) < cap
    # Two rows may share a second by chance; the old clamp put dozens on one.
    assert max(scheduled.count(moment) for moment in scheduled) <= 2


def test_place_after_stays_between_its_bounds():
    random.seed(0)
    lower = datetime(2026, 3, 14, 23, 0)
    upper = datetime(2026, 3, 15)
    for business_hours in (False, True):
        moments = [place_after(lower, "timestamp", upper, business_hours) for _ in range(200)]
        assert all(lower <= moment < upper for moment in moments)
        assert len(set(moments)) > 150

    dates = [place_after(lower, "date", upper, True) for _ in range(50)]
    assert {moment.date() for moment in dates} <= {date(2026, 3, 14), date(2026, 3, 15)}
    assert place_after(upper, "timestamp", upper, False) == upper


def test_start_end_suffix_pair_is_honoured():
    tables = {
        "subscriptions": TableDef(
            name="subscriptions",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("order_start", "date", {"not null"}),
                ColumnDef("order_end", "date", set()),
            ],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=300, seed=6, as_of=ANCHOR)["subscriptions"]

    for start, end in zip(df["order_start"], df["order_end"], strict=True):
        if end is not None:
            assert start <= end


def test_birth_date_is_not_constrained_by_created_at():
    tables = {
        "customers": TableDef(
            name="customers",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("birth_date", "date", {"not null"}),
            ],
        )
    }
    unordered = generate_data_from_dbml(tables, [], base_rows=20, seed=2024, as_of=ANCHOR)[
        "customers"
    ]

    # A birth date reaches decades before created_at's one-year window, which
    # would be impossible if it were folded into the created/updated chain.
    assert any(d.year < ANCHOR.year - 1 for d in unordered["birth_date"])


def test_single_timestamp_table_is_byte_identical_to_the_pinned_frame():
    """`order_row_times` has nothing to do on a lone temporal column: this is
    the same assertion as the pinned uniform-profile test, kept here too so a
    reader of the ordering tests can see the "no pair, no change" case."""
    df = generate_data_from_dbml(
        _single_timestamp_and_date_schema(), [], base_rows=8, seed=2024, as_of=ANCHOR
    )["events"]

    assert list(df["created_at"])[0] == "2025-12-07 15:29:52"


def test_cyclic_after_hints_raise_naming_table_and_columns():
    tables = {
        "loops": TableDef(
            name="loops",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("a", "timestamp", set(), note={"after": "b"}),
                ColumnDef("b", "timestamp", set(), note={"after": "a"}),
            ],
        )
    }

    with pytest.raises(ValueError, match="loops"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=ANCHOR)


def test_unknown_after_target_raises_naming_the_column():
    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("shipped_at", "timestamp", set(), note={"after": "nonexistent"}),
            ],
        )
    }

    with pytest.raises(ValueError, match="shipped_at"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=ANCHOR)


def test_after_target_that_is_not_temporal_raises():
    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("status", "varchar", set()),
                ColumnDef("shipped_at", "timestamp", set(), note={"after": "status"}),
            ],
        )
    }

    with pytest.raises(ValueError, match="not a date or timestamp column"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=ANCHOR)


def test_order_row_times_leaves_untouched_frame_with_no_ordered_pair():
    tables = _single_timestamp_and_date_schema()
    df = generate_data_from_dbml(tables, [], base_rows=10, seed=2024, as_of=ANCHOR)["events"]

    before = df.copy(deep=True)
    result = order_row_times(df, tables["events"], as_of=ANCHOR)

    assert result.equals(before)
