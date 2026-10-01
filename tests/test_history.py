"""A table's history (`incremental.history`), its tests, and `finish_run`."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from test_defects import SHOP

from model2data.dbt.tests import dbt_tests, generate_dbt_yml
from model2data.defects import apply_defects, as_seeded, failing, planned_defects, preset_defects
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.generate.history import history_frames, history_tables
from model2data.model import Defect, dump, load, to_dict, to_engine
from model2data.output import finish_run

AS_OF = date(2026, 1, 1)
HISTORY = SHOP.replace("updated_at: updated_at}", "updated_at: updated_at, history: true}").replace(
    "    incremental: {new_per_day: 4}", "    incremental: {new_per_day: 4, history: true}"
)


@pytest.fixture(scope="module")
def model():
    return load(HISTORY)


@pytest.fixture(scope="module")
def days(model):
    return generate_days(model, 3, seed=11, as_of=AS_OF)


def _tests(tables, refs=()):
    return dbt_tests(tables, list(refs), hint_tests="warn")


def test_history_is_written_back(model):
    assert model.tables["orders"].incremental.history is True
    assert "history: true}" in dump(model)
    assert load(dump(model)) == model


def test_a_0_2_model_given_history_is_written_as_0_3():
    old = load(SHOP.replace("model2data: 0.3.0", "model2data: 0.2.0"))
    assert to_dict(old)["model2data"] == "0.2.0"
    old.tables["orders"].incremental.history = True
    assert to_dict(old)["model2data"] == "0.3.0"


def test_every_version_of_every_row(model, days):
    histories = history_frames(model, days)
    assert list(histories) == ["customers_history", "orders_history"]
    orders = histories["orders_history"]
    state = days[-1].tables["orders"].state
    updates = sum(len(r.tables["orders"].updated) for r in days[1:])
    assert len(orders) == len(state) + updates
    assert list(orders.columns) == [*state.columns, "valid_from", "valid_to", "is_current"]
    assert orders["id"].dtype == "Int64"
    assert (orders.groupby("id")["is_current"].sum() == 1).all()
    current = orders[orders["is_current"]].set_index("id")
    assert current.loc[state["id"], "status"].tolist() == state["status"].tolist()
    assert current["valid_to"].isna().all()
    for _, versions in orders.groupby("id"):
        begins = pd.to_datetime(versions["valid_from"]).tolist()
        assert begins == sorted(begins)
        assert versions["valid_to"].iloc[:-1].tolist() == versions["valid_from"].iloc[1:].tolist()
    updated = orders[~orders["is_current"]]
    assert len(updated) == updates
    # With `updated_at`, a version begins when it was updated.
    assert (orders["valid_from"] == orders["updated_at"]).mean() > 0.9


def test_without_updated_at_a_version_begins_with_its_day(model, days):
    customers = history_frames(model, days)["customers_history"]
    first = customers.iloc[0]
    assert first["valid_from"] == "2026-01-01 00:00:00"
    late = customers[customers["valid_from"] > "2026-01-01 00:00:00"]
    assert set(late["valid_from"]) <= {f"2026-01-0{d} 00:00:00" for d in (2, 3, 4)}


def test_a_version_never_begins_before_the_one_it_follows(model, days):
    # The first day's updated_at placed after a later update: the update begins a second later.
    changed = list(days)
    first = changed[0].tables["orders"]
    state = first.state.copy()
    updated = changed[1].tables["orders"]
    position = updated.updated_positions[0]
    state.loc[position, "updated_at"] = "2027-01-01 00:00:00"
    changed[0] = type(changed[0])(
        0,
        changed[0].date,
        {**changed[0].tables, "orders": type(first)(state, first.updated, state)},
    )
    orders = history_frames(model, changed)["orders_history"]
    key = state.loc[position, "id"]
    versions = orders[orders["id"] == key]
    assert versions["valid_from"].tolist()[:2] == ["2027-01-01 00:00:00", "2027-01-01 00:00:01"]
    assert not failing(_tests(history_tables(model)), {"orders_history": as_seeded(orders)})


def test_one_day_gives_one_version_each(model):
    history = history_frames(model, generate_days(model, 0, seed=11, as_of=AS_OF))
    assert history["orders_history"]["is_current"].all()


def test_the_history_tables_and_their_tests(model, tmp_path):
    tables = history_tables(model)
    orders = tables["orders_history"]
    assert [c.name for c in orders.columns][-3:] == ["valid_from", "valid_to", "is_current"]
    assert all(not c.settings and not c.enum_values for c in orders.columns)
    assert orders.note == {
        "history": {
            "key": ["id"],
            "valid_from": "valid_from",
            "valid_to": "valid_to",
            "current": "is_current",
        }
    }
    names = [t.name for t in _tests({"orders_history": orders})]
    assert names == [
        "model2data_one_current_row_stg_orders_history_is_current__id",
        "model2data_no_overlapping_ranges_stg_orders_history_id__valid_from__valid_to",
    ]
    generate_dbt_yml(tmp_path, {"orders_history": orders}, [])
    assert (tmp_path / "macros" / "model2data_history_tests.sql").exists()
    yml = (tmp_path / "models" / "staging" / "stg_orders_history.yml").read_text()
    assert "model2data_no_overlapping_ranges:" in yml and "model2data_one_current_row:" in yml


def test_a_project_without_history_gets_no_history_macros(model, tmp_path):
    generate_dbt_yml(tmp_path, to_engine(load(SHOP)).tables, [])
    assert not (tmp_path / "macros" / "model2data_history_tests.sql").exists()


def _history_frame(current, valid_to, key=(1, 1, 2)) -> dict:
    frame = pd.DataFrame(
        {
            "id": list(key),
            "valid_from": ["2026-01-01 00:00:00", "2026-01-02 00:00:00", "2026-01-01 00:00:00"],
            "valid_to": valid_to,
            "is_current": current,
        }
    )
    return {"orders_history": as_seeded(frame)}


@pytest.mark.parametrize(
    ("current", "valid_to", "fails"),
    [
        ([False, True, True], ["2026-01-02 00:00:00", None, None], set()),
        ([True, True, True], ["2026-01-02 00:00:00", None, None], {"one"}),
        ([False, False, True], ["2026-01-02 00:00:00", None, None], {"one"}),
        ([False, True, True], ["2026-01-03 00:00:00", None, None], {"overlap"}),
        ([False, True, True], [None, None, None], {"overlap"}),
    ],
)
def test_the_history_checks(model, current, valid_to, fails):
    tests = _tests(history_tables(model))
    failed = failing(tests, _history_frame(current, valid_to))
    kinds = {"one" if "one_current" in name else "overlap" for name in failed if "orders" in name}
    assert kinds == fails


# ---------------------------------------------------------------------------
# finish_run
# ---------------------------------------------------------------------------
def test_finish_run_without_defects_adds_the_history_and_changes_nothing(model, days):
    out = finish_run(model, days)
    assert out.report is None and out.days == days
    assert list(out.tables)[-2:] == ["customers_history", "orders_history"]
    for key in model.tables:
        assert out.frames[key] is days[-1].tables[key].state
    assert out.refs == to_engine(model).refs


def test_finish_run_on_one_day_of_frames(model):
    inputs = to_engine(model)
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=20, seed=1, as_of=AS_OF)
    out = finish_run(
        model, frames, {"orders": [Defect("overlapping_history", count=1)]}, as_of=AS_OF
    )
    assert out.days == []
    assert out.frames["orders_history"]["valid_from"].iloc[0] >= "2025"
    (applied,) = out.report.defects
    assert applied.applied == 0
    assert applied.note.startswith("asked for 1 rows, applied 0: only 0 versions have a later one")


def test_overlapping_history_breaks_only_the_overlap_test(model, days):
    plan = {
        "orders": [
            Defect("overlapping_history", count=3),
            Defect("nulls", column="amount", count=2),
        ]
    }
    out = finish_run(model, days, plan, seed=11)
    overlap, nulls = out.report.defects
    assert overlap.table == "orders_history" and overlap.column == "valid_to"
    assert overlap.row_key is None and overlap.applied == 3
    assert overlap.expected_failures == [
        "model2data_no_overlapping_ranges_stg_orders_history_id__valid_from__valid_to"
    ]
    assert nulls.expected_failures == ["not_null_stg_orders_amount"]
    history = out.frames["orders_history"]
    for number in overlap.rows:
        row, following = history.iloc[number - 1], history.iloc[number]
        assert row["valid_to"] > following["valid_from"] and not row["is_current"]
    # The history is the clean record: the nulls are in orders, not in its history.
    assert out.frames["orders"]["amount"].isna().sum() == 2
    assert history["amount"].notna().all()
    # The days carry the table's defects, and only those.
    assert out.days[-1].tables["orders"].state["amount"].isna().sum() == 2


def test_apply_defects_has_no_history_to_break(model, days):
    _, report = apply_defects(model, days, {"orders": [Defect("overlapping_history", count=1)]})
    assert report.defects[0].table == "orders_history"
    assert report.defects[0].note == (
        "there is no orders_history to break: it needs `incremental.history` on orders, and "
        "model2data.output.finish_run, which builds it"
    )


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def test_presets_break_a_history_on_a_run_of_days(model):
    assert (
        Defect("overlapping_history", count=2)
        in preset_defects(model, "training", days=1)["customers"]
    )
    assert (
        Defect("overlapping_history", share=0.02)
        in preset_defects(model, "messy", days=1)["orders"]
    )
    assert not any(
        d.type == "overlapping_history"
        for entries in preset_defects(model, "messy").values()
        for d in entries
    )


def test_none_ignores_every_defect():
    model = load(SHOP)
    model.tables["orders"].defects = [Defect("nulls", column="amount", count=1)]
    assert planned_defects(model, "clean") == {
        "orders": [Defect("nulls", column="amount", count=1)]
    }
    assert planned_defects(model, "none") == {}
    assert preset_defects(model, "none") == {}
    model.run.defects = "none"
    assert planned_defects(model) == {}
