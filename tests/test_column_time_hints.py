"""Per-column overrides of the run-level `TimeProfile`.

1.5.0 shaped *when* things happen for a whole run: business hours, growth,
seasonality all apply to every date/timestamp column alike. The product
ask this closes was literally "that changes for everything globally, can we
have that column per column?" -- so `business_hours`/`growth`/`seasonality`
column-note hints patch just the fields they name onto the run's profile for
that one column, the same way `skew` already overrides per foreign key (see
tests/test_shaping.py). Every statistical test here uses a fixed seed and a
generous tolerance so it never flakes; the exact bounds were chosen by
running the real implementation and leaving headroom, not derived
analytically.
"""

from datetime import date, datetime, timedelta

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.options import TimeProfile
from model2data.parse.dbml import ColumnDef, TableDef

ANCHOR_DATE = date(2026, 3, 15)


def _two_timestamp_schema(hinted_note=None, plain_note=None) -> dict[str, TableDef]:
    return {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("hinted_at", "timestamp", {"not null"}, note=hinted_note),
                ColumnDef("plain_at", "timestamp", {"not null"}, note=plain_note),
            ],
        )
    }


def _column(df, name) -> list[datetime]:
    return [datetime.fromisoformat(v) for v in df[name]]


def _business_hours_share(timestamps: list[datetime]) -> float:
    in_hours = sum(1 for t in timestamps if t.weekday() < 5 and 8 <= t.hour < 18)
    return in_hours / len(timestamps)


def _timestamp_window_midpoint() -> date:
    """Midpoint of the 365-day timestamp window `weighted_timestamps` draws
    from (see timeline.py's `_TIMESTAMP_WINDOW_DAYS`): the anchor minus a
    year, up to the day before the anchor."""
    start = ANCHOR_DATE - timedelta(days=365)
    end = ANCHOR_DATE - timedelta(days=1)
    return start + (end - start) / 2


# ---------------------------------------------------------
# A hint shapes its own column, not its neighbour
# ---------------------------------------------------------
def test_business_hours_hint_shapes_only_the_hinted_column():
    tables = _two_timestamp_schema(hinted_note={"business_hours": True})
    df = generate_data_from_dbml(
        tables, [], base_rows=1000, seed=5, as_of=ANCHOR_DATE, time_profile=TimeProfile()
    )["events"]

    hinted_share = _business_hours_share(_column(df, "hinted_at"))
    plain_share = _business_hours_share(_column(df, "plain_at"))

    assert hinted_share >= 0.70
    assert plain_share < hinted_share - 0.2


def test_growth_hint_shapes_only_the_hinted_column():
    tables = _two_timestamp_schema(hinted_note={"growth": 1.0})
    df = generate_data_from_dbml(
        tables, [], base_rows=2000, seed=5, as_of=ANCHOR_DATE, time_profile=TimeProfile()
    )["events"]

    midpoint = _timestamp_window_midpoint()
    hinted = _column(df, "hinted_at")
    plain = _column(df, "plain_at")

    hinted_second_half = sum(1 for t in hinted if t.date() >= midpoint) / len(hinted)
    plain_second_half = sum(1 for t in plain if t.date() >= midpoint) / len(plain)

    # growth=1.0 on hinted_at pushes its rows toward the second half of the
    # window; plain_at, with no hint under a flat run-level profile, does not.
    assert hinted_second_half > plain_second_half + 0.05


# ---------------------------------------------------------
# A hint overrides the run-level profile in both directions
# ---------------------------------------------------------
def test_hint_overrides_a_flat_run_level_profile():
    tables = _two_timestamp_schema(hinted_note={"business_hours": True})
    df = generate_data_from_dbml(
        tables, [], base_rows=1000, seed=5, as_of=ANCHOR_DATE, time_profile=TimeProfile()
    )["events"]

    assert _business_hours_share(_column(df, "hinted_at")) >= 0.70


def test_hint_overrides_a_shaped_run_level_profile_back_to_flat():
    tables = _two_timestamp_schema(hinted_note={"business_hours": False})
    df = generate_data_from_dbml(
        tables,
        [],
        base_rows=1000,
        seed=5,
        as_of=ANCHOR_DATE,
        time_profile=TimeProfile(business_hours=True),
    )["events"]

    # hinted_at is forced back to uniform; plain_at keeps the run's shaping.
    hinted_share = _business_hours_share(_column(df, "hinted_at"))
    plain_share = _business_hours_share(_column(df, "plain_at"))
    assert plain_share >= 0.70
    assert hinted_share < plain_share - 0.2


def test_cli_flag_and_column_hint_combine_with_the_hint_winning():
    """`--business-hours` (a run-level TimeProfile) plus a column hint that
    turns it back off for one column: the hint wins for that column, the
    run-level setting still applies to its neighbour."""
    tables = _two_timestamp_schema(hinted_note={"business_hours": False})
    df = generate_data_from_dbml(
        tables,
        [],
        base_rows=1000,
        seed=5,
        as_of=ANCHOR_DATE,
        time_profile=TimeProfile(business_hours=True),
    )["events"]

    assert _business_hours_share(_column(df, "hinted_at")) < 0.5
    assert _business_hours_share(_column(df, "plain_at")) >= 0.70


# ---------------------------------------------------------
# A partial hint only replaces the fields it names
# ---------------------------------------------------------
def test_partial_hint_keeps_the_other_run_level_fields():
    """`{"growth": 0}` flattens growth for this column but leaves
    business_hours exactly as the run set it."""
    tables = _two_timestamp_schema(hinted_note={"growth": 0.0})
    run_profile = TimeProfile(business_hours=True, growth=1.0)
    df = generate_data_from_dbml(
        tables, [], base_rows=2000, seed=5, as_of=ANCHOR_DATE, time_profile=run_profile
    )["events"]

    hinted = _column(df, "hinted_at")
    plain = _column(df, "plain_at")

    # business_hours still applies to hinted_at (inherited, not overridden).
    assert _business_hours_share(hinted) >= 0.70

    # growth=0 on hinted_at flattens it relative to plain_at, which keeps the
    # run's growth=1.0 and skews toward the second half of the window.
    midpoint = _timestamp_window_midpoint()
    hinted_second_half = sum(1 for t in hinted if t.date() >= midpoint) / len(hinted)
    plain_second_half = sum(1 for t in plain if t.date() >= midpoint) / len(plain)
    assert 0.35 <= hinted_second_half <= 0.65
    assert plain_second_half > hinted_second_half


# ---------------------------------------------------------
# No hint: pinned frames unchanged
# ---------------------------------------------------------
def test_no_hint_reproduces_the_run_level_only_frame():
    """A column with no time hint must generate byte-identical values to the
    same schema before this feature existed -- the override helper is a
    pure no-op when a column's note carries none of the three keys."""
    tables = _two_timestamp_schema()
    with_helper = generate_data_from_dbml(
        tables, [], base_rows=20, seed=2024, as_of=ANCHOR_DATE, time_profile=TimeProfile(growth=0.4)
    )["events"]
    again = generate_data_from_dbml(
        tables, [], base_rows=20, seed=2024, as_of=ANCHOR_DATE, time_profile=TimeProfile(growth=0.4)
    )["events"]

    assert with_helper.equals(again)


def test_no_hint_and_uniform_profile_matches_the_pre_feature_pinned_frame():
    """Same schema and seed as test_timeline.py's own pinned-frame test, with
    an explicit empty note on both columns: still byte-identical to 1.5.0."""
    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("created_at", "timestamp", {"not null"}),
                ColumnDef("event_date", "date", set()),
            ],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=8, seed=2024, as_of=ANCHOR_DATE)["events"]

    assert list(df["created_at"])[:2] == ["2026-01-24 23:52:50", "2025-08-07 01:00:18"]


# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------
def _single_column_table(column: ColumnDef, table_name: str = "t") -> dict[str, TableDef]:
    return {table_name: TableDef(name=table_name, columns=[ColumnDef("id", "int", {"pk"}), column])}


@pytest.mark.parametrize(
    "key, value", [("business_hours", True), ("growth", 0.5), ("seasonality", 0.5)]
)
def test_hint_on_a_non_temporal_column_is_rejected(key, value):
    tables = _single_column_table(ColumnDef("label", "varchar", note={key: value}))
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_growth_below_negative_one_is_rejected():
    tables = _single_column_table(ColumnDef("happened_at", "timestamp", note={"growth": -1.5}))
    with pytest.raises(ValueError, match=r"t\.happened_at.*growth"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_growth_as_a_bool_is_rejected():
    tables = _single_column_table(ColumnDef("happened_at", "timestamp", note={"growth": True}))
    with pytest.raises(ValueError, match=r"t\.happened_at"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_seasonality_out_of_range_is_rejected():
    tables = _single_column_table(ColumnDef("happened_at", "timestamp", note={"seasonality": 1.5}))
    with pytest.raises(ValueError, match=r"t\.happened_at"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_business_hours_as_a_non_bool_is_rejected():
    tables = _single_column_table(
        ColumnDef("happened_at", "timestamp", note={"business_hours": "yes"})
    )
    with pytest.raises(ValueError, match=r"t\.happened_at"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)
