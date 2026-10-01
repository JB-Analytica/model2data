"""Regressions from the review of defects: each case once broke the promise or crashed."""

from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from test_defects import SHOP
from test_model import _issues
from typer.testing import CliRunner

from model2data.cli import app
from model2data.defects import apply_defects, preset_defects
from model2data.defects.apply import _invalid_value, _Outcome, _Patch, _recount
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.generate.history import history_frames
from model2data.model import Defect, load, to_engine

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "defects"
AS_OF = date(2026, 1, 1)
runner = CliRunner()


def _frames(model, rows=40, seed=11):
    inputs = to_engine(model)
    return generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=rows, seed=seed, as_of=AS_OF
    )


def test_late_updates_on_a_table_with_a_nullable_integer():
    model = load(FIXTURES / "lu2.model2data.yml")
    days = generate_days(model, 3, seed=4, as_of=AS_OF)
    _, report = apply_defects(model, days, {"orders": [Defect("late_updates", count=2)]}, seed=4)
    assert report.defects[0].applied == 2


def test_orphans_of_dates_and_uuids_are_dates_and_uuids():
    model = load(FIXTURES / "pkd.model2data.yml")
    frames = _frames(model, rows=8, seed=2)
    plan = {
        "c": [
            Defect("orphan_foreign_keys", column="u_id", count=2),
            Defect("orphan_foreign_keys", column="d", count=2),
        ]
    }
    broken, report = apply_defects(model, frames, plan, seed=2)
    rows = [n - 1 for d in report.defects for n in d.row_numbers]
    for value in broken["c"]["d"].iloc[rows]:
        pd.Timestamp(value)  # a date, past every date the parent holds
    assert max(map(str, broken["c"]["d"])) > max(map(str, broken["cal"]["d"]))
    assert all(len(v) == 36 for v in broken["c"]["u_id"])


def test_an_orphan_date_after_another_column_does_not_crash():
    model = load(FIXTURES / "dfk.model2data.yml")
    _, report = apply_defects(
        model, _frames(model, rows=10, seed=5), {"ev": model.tables["ev"].defects}, seed=5
    )
    (applied,) = report.defects
    assert applied.applied == 2
    # The generator's own data already breaks that test: the report says the defect cannot show.
    assert report.failing_without_defects == ["relationships_stg_ev_d__d__ref_stg_cal_"]
    assert applied.note is not None and "already fails without any defect" in applied.note


def test_a_duplicates_source_is_left_alone_by_later_defects():
    model = load(SHOP)
    frames = _frames(model, rows=12, seed=15)
    plan = {
        "lines": [
            Defect("duplicate_keys", count=5),
            Defect("orphan_foreign_keys", column="order_id", count=12),
        ]
    }
    broken, report = apply_defects(model, frames, plan, seed=15)
    duplicates, orphans = report.defects
    keys = list(broken["lines"][["order_id", "line"]].itertuples(index=False))
    for number in duplicates.row_numbers:
        assert keys.count(keys[number - 1]) == 2
    assert not set(duplicates.row_numbers) & set(orphans.row_numbers)


def test_a_row_undone_is_not_counted():
    frame = pd.DataFrame({"id": [1, 1, 3], "x": ["a", "b", "c"]})
    outcome = _Outcome("id", [0, 2], [_Patch(0, "id", 1, 0), _Patch(2, "id", 9, 0)], days=[1, 2])
    _recount(Defect("duplicate_keys", count=2), outcome, frame)
    assert outcome.positions == [0] and outcome.days == [1]
    assert outcome.note == "1 of its rows were undone by a later defect"
    unchanged = _Outcome("x", [], [])
    _recount(Defect("nulls", column="x", count=1), unchanged, frame)
    _recount(Defect("nulls", column="x", count=1), unchanged, None)
    assert unchanged.note is None


def test_history_keeps_columns_named_like_its_bookkeeping():
    model = load(FIXTURES / "hcol.model2data.yml")
    history = history_frames(model, generate_days(model, 2, seed=1, as_of=AS_OF))["orders_history"]
    assert list(history.columns) == [
        "id",
        "status",
        "_position",
        "valid_from",
        "valid_to",
        "is_current",
    ]
    assert history["_position"].notna().any()


def test_invalid_values_vary_and_avoid_members():
    rng = random.Random(3)
    members = ["pending", "paid"]
    values = {_invalid_value(rng, "paid", members) for _ in range(30)}
    assert len(values) > 2 and not values & set(members)
    assert {_invalid_value(rng, "1", ["1", "2"]) for _ in range(20)} <= {3, 4, 5}


def test_late_arriving_cutoff_counts_the_defects_before_it():
    model = load(SHOP)
    days = generate_days(model, 3, seed=11, as_of=AS_OF)
    plan = {
        "orders": [
            Defect("nulls", column="updated_at", count=60),
            Defect("late_arriving", count=2),
        ]
    }
    broken, report = apply_defects(model, days, plan, seed=11)
    late = report.defects[1]
    final = broken[-1].tables["orders"].state
    for number, day in zip(late.row_numbers, late.days or [], strict=True):
        seen = pd.to_datetime(broken[day - 1].tables["orders"].state["updated_at"]).max()
        assert pd.Timestamp(final["updated_at"].iloc[number - 1]) < seen


def test_a_late_defect_says_what_it_breaks():
    from model2data.defects.apply import _applied

    model = to_engine(load(SHOP))
    outcome = _Outcome("updated_at", [0], [], days=[1], note="these rows arrived late")
    applied = _applied(
        model,
        "orders",
        Defect("late_arriving", count=1),
        1,
        outcome,
        pd.DataFrame({"id": [7]}),
        ["unique_stg_orders_updated_at"],
    )
    assert applied.note == "it also breaks unique_stg_orders_updated_at; these rows arrived late"
    messy = _applied(
        model,
        "orders",
        Defect("messy_text", column="x", count=1),
        1,
        _Outcome("x", [0], []),
        pd.DataFrame({"id": [7]}),
        [],
    )
    assert messy.note is None


def test_a_defect_on_an_sql_keyword_says_so():
    model = load(
        "model2data: 0.3.0\ntables:\n  t:\n    columns:\n      id: {type: int, pk: true}\n"
        "      select: {type: text, not_null: true}\n"
    )
    _, report = apply_defects(
        model, _frames(model, rows=10), {"t": [Defect("nulls", column="select", count=1)]}, seed=1
    )
    assert "select is an SQL keyword" in report.defects[0].note


def test_a_preset_with_nothing_to_break_says_so(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "bare.model2data.yml"
    model.write_text("model2data: 0.3.0\ntables:\n  t:\n    columns:\n      n: int\n")
    assert preset_defects(load(model), "training") == {}
    result = runner.invoke(app, ["--file", str(model), "--defects", "training", "--rows", "10"])
    assert result.exit_code == 0, result.output
    assert "The training preset finds nothing to break in this model: no defects." in result.output


def test_presets_skip_key_enums_and_time_columns():
    model = load(
        "model2data: 0.3.0\nenums:\n  s: [a, b]\ntables:\n  t:\n    columns:\n"
        "      code: {type: s, pk: true}\n      at: time\n      label: text\n"
    )
    messy = preset_defects(model, "messy")["t"]
    assert Defect("messy_text", column="label", share=0.05) in messy
    assert not any(d.type == "invalid_values" for d in messy)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _doc(columns: dict, defects: list, **table) -> dict:
    return {
        "model2data": "0.3.0",
        "enums": {"s": ["a", "b"]},
        "tables": {
            "p": {"columns": {"id": {"type": "int", "pk": True}, "on": "boolean"}},
            "t": {"columns": columns, "defects": defects, **table},
        },
    }


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            _doc(
                {"id": {"type": "int", "pk": True}},
                [{"type": "duplicate_keys", "column": ["a", "b"], "count": 1}],
            ),
            "tables.t.defects.0.column: must be a string, not a list",
            id="column-as-a-list",
        ),
        pytest.param(
            _doc(
                {"id": {"type": "int", "pk": True}, "f": {"type": "boolean", "references": "p.on"}},
                [{"type": "orphan_foreign_keys", "column": "f", "count": 1}],
            ),
            "tables.t.defects.0.column: names f, a boolean: it has no value its parent does not hold",
            id="boolean-orphan",
        ),
        pytest.param(
            _doc(
                {"code": {"type": "s", "pk": True}},
                [{"type": "invalid_values", "column": "code", "count": 1}],
            ),
            "tables.t.defects.0.column: names code, part of a key of t: an invalid value there would "
            "break its key tests and joins too, not only accepted_values",
            id="invalid-on-a-key",
        ),
        pytest.param(
            _doc(
                {"id": {"type": "int", "pk": True}, "at": "time"},
                [{"type": "messy_text", "column": "at", "count": 1}],
            ),
            "tables.t.defects.0.column: names at, which is not a text column (time is not text)",
            id="messy-on-time",
        ),
    ],
)
def test_review_validation(document, expected):
    assert expected in _issues(document)


def test_a_history_whose_dbt_name_is_taken_is_refused():
    document = {
        "model2data": "0.3.0",
        "tables": {
            "orders": {
                "incremental": {"history": True},
                "columns": {"id": {"type": "int", "pk": True}},
            },
            "Orders History": {"columns": {"id": "int"}},
        },
    }
    assert _issues(document) == [
        "tables.orders.incremental.history: writes the table orders_history, whose dbt name "
        "orders_history is also the table Orders History's: rename one of them"
    ]


def test_a_boolean_foreign_key_gets_no_orphans():
    model = load(
        "model2data: 0.3.0\ntables:\n  p:\n    columns:\n      on: {type: boolean, unique: true}\n"
        "  t:\n    columns:\n      id: {type: int, pk: true}\n      f: {type: boolean, references: p.on}\n"
    )
    _, report = apply_defects(
        model, _frames(model, rows=10), {"t": [Defect("orphan_foreign_keys", column="f", count=1)]}
    )
    assert report.defects[0].note == "f is a boolean: no value of it is missing from its parent"


def test_an_undone_row_without_days():
    frame = pd.DataFrame({"x": ["a", "b"]})
    outcome = _Outcome("x", [0, 1], [_Patch(0, "x", None, 0), _Patch(1, "x", "b", 0)])
    _recount(Defect("nulls", column="x", count=2), outcome, frame)
    assert outcome.positions == [1] and outcome.days is None
