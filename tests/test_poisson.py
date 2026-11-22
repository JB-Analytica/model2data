"""`distribution: poisson` (spec 0.9.0): whole-number counts on an integer column."""

from __future__ import annotations

import copy
import math
import random
import statistics
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from model2data.dbt.hint_tests import hint_tests_for
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.faker import generate_column_values, poisson_draw
from model2data.model import from_dict, to_engine
from model2data.model.dbml import from_dbml
from model2data.model.document import document_version
from model2data.model.validate import check
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = date(2026, 11, 22)


def _draws(mean: float, count: int = 5000, seed: int = 3) -> list[int]:
    random.seed(seed)
    return [poisson_draw(mean) for _ in range(count)]


@pytest.mark.parametrize("mean", [0.2, 1.5, 4.0, 9.5, 10.0, 40.0, 300.0, 5000.0])
def test_the_draw_has_its_mean_as_mean_and_variance(mean):
    values = _draws(mean)
    assert all(isinstance(value, int) and value >= 0 for value in values)
    # Five standard errors of the mean, and a generous band on the variance.
    assert statistics.fmean(values) == pytest.approx(mean, abs=5 * math.sqrt(mean / len(values)))
    assert statistics.pvariance(values) == pytest.approx(mean, rel=0.12)


def test_the_draw_follows_the_poisson_probabilities():
    values = _draws(1.5, count=20000)
    for k in range(5):
        expected = math.exp(-1.5) * 1.5**k / math.factorial(k)
        assert values.count(k) / len(values) == pytest.approx(expected, abs=0.012)


def test_the_draw_is_the_seed_s():
    assert _draws(1.2, 200, seed=9) == _draws(1.2, 200, seed=9)
    assert _draws(60.0, 200, seed=9) == _draws(60.0, 200, seed=9)
    assert _draws(60.0, 200, seed=9) != _draws(60.0, 200, seed=10)


def _column(name: str = "goals", data_type: str = "int", **note: Any) -> ColumnDef:
    return ColumnDef(name, data_type, {"not null"}, note={"distribution": "poisson", **note})


def _values(column: ColumnDef, count: int = 5000, seed: int = 1) -> list:
    random.seed(seed)
    return generate_column_values(column, row_count=count, table_name="games")


def test_an_integer_column_draws_counts_around_its_mean():
    values = _values(_column(mean=1.5))
    assert all(isinstance(value, int) for value in values)
    assert statistics.fmean(values) == pytest.approx(1.5, abs=0.08)
    assert statistics.pvariance(values) == pytest.approx(1.5, rel=0.1)


def test_a_mean_left_out_is_the_midpoint_of_the_range():
    assert statistics.fmean(_values(_column(min=0, max=10))) == pytest.approx(5, abs=0.15)
    # The column's default range is 0 to 100.
    assert statistics.fmean(_values(_column())) == pytest.approx(50, abs=0.5)


def test_min_and_max_clip_the_draws():
    values = _values(_column(mean=3, min=2, max=4))
    assert set(values) == {2, 3, 4}
    # A mean far below the floor: every redraw misses, and the value is clamped to it.
    assert set(_values(_column(mean=0.1, min=5, max=8), count=50)) == {5}
    assert max(_values(_column(mean=1.5, max=9))) <= 9


def test_the_sports_example_scores_about_a_draw_in_four():
    home = _values(_column(mean=1.5, max=9), seed=4)
    away = _values(_column(mean=1.2, max=9), seed=5)
    draws = sum(h == a for h, a in zip(home, away, strict=True)) / len(home)
    assert draws == pytest.approx(0.255, abs=0.03)
    assert statistics.fmean([h + a for h, a in zip(home, away, strict=True)]) == pytest.approx(
        2.7, abs=0.1
    )


# ---------------------------------------------------------------------------
# The model document
# ---------------------------------------------------------------------------
def _model(goals: dict, version: str = "0.9.0") -> dict:
    return copy.deepcopy(
        {
            "model2data": version,
            "tables": {
                "games": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "goals": goals,
                    }
                }
            },
        }
    )


def _errors(document: dict) -> list[str]:
    return [str(issue) for issue in check(document) if issue.is_error]


def test_a_document_with_poisson_generates_it():
    model = from_dict(
        _model({"type": "int", "generate": {"distribution": {"kind": "poisson", "mean": 2}}})
    )
    inputs = to_engine(model)
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=2000, seed=1)
    goals = frames["games"]["goals"].dropna()
    assert statistics.fmean(goals) == pytest.approx(2, abs=0.15)


@pytest.mark.parametrize(
    ("goals", "message"),
    [
        (
            {"type": "decimal(6,2)", "generate": {"distribution": "poisson"}},
            "games.columns.goals.generate.distribution: poisson draws whole numbers, so it "
            "applies to an integer column only: goals is decimal(6,2)",
        ),
        (
            {"type": "int", "generate": {"distribution": {"kind": "poisson", "mean": math.inf}}},
            "games.columns.goals.generate.distribution.mean: must be a finite number, not inf",
        ),
        (
            {"type": "int", "generate": {"distribution": {"kind": "poisson", "mean": 0}}},
            "games.columns.goals.generate.distribution.mean",
        ),
        (
            {"type": "int", "generate": {"distribution": {"kind": "poisson", "stddev": 1}}},
            "games.columns.goals.generate.distribution",
        ),
    ],
    ids=["decimal", "infinite-mean", "zero-mean", "foreign-parameter"],
)
def test_validation_reports_a_poisson_that_cannot_be(goals, message):
    errors = _errors(_model(goals))
    assert any(error.startswith(f"tables.{message}") for error in errors), errors


def test_poisson_is_spec_0_9_0():
    for distribution in ("poisson", {"kind": "poisson", "mean": 1.5}):
        errors = _errors(
            _model({"type": "int", "generate": {"distribution": distribution}}, "0.8.0")
        )
        assert errors == [
            "tables.games.columns.goals.generate.distribution: `distribution: poisson` is spec "
            "0.9.0, and the document is written against 0.8: write `model2data: 0.9.0`"
        ]
    assert _errors(_model({"type": "int", "generate": {"distribution": "normal"}}, "0.8.0")) == []


def test_a_model_read_as_0_8_that_uses_poisson_is_written_as_0_9():
    model = from_dict(_model({"type": "int", "generate": {"distribution": "poisson"}}))
    assert document_version(replace(model, version="0.8.0")) == "0.9.0"
    plain = from_dict(_model({"type": "int"}, "0.8.0"))
    assert document_version(plain) == "0.8.0"


def test_dbml_with_poisson_converts_to_a_0_9_document():
    model = from_dbml(
        """
Table games {
  id bigint [pk]
  goals int [note: '{"distribution": "poisson", "mean": 1.5, "max": 9}']
}
"""
    )
    assert str(document_version(model)) == "0.9.0"
    assert model.tables["games"].columns["goals"].generate["distribution"] == {
        "kind": "poisson",
        "mean": 1.5,
    }


# ---------------------------------------------------------------------------
# The engine's own checks (DBML notes, hand-built tables)
# ---------------------------------------------------------------------------
def _refused(column: ColumnDef, pattern: str) -> None:
    tables = {"t": TableDef(name="t", columns=[ColumnDef("id", "int", {"pk"}), column])}
    with pytest.raises(ValueError, match=pattern):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=AS_OF)


def test_the_engine_refuses_poisson_on_a_decimal_column():
    _refused(
        _column(data_type="numeric(8,2)"),
        r't\.goals: "distribution" poisson draws whole numbers, so it applies to an integer '
        r"column only \(numeric\(8,2\) is not one\)",
    )


@pytest.mark.parametrize("mean", [0, -1, math.inf, math.nan, "1", True])
def test_the_engine_refuses_a_poisson_mean_that_is_not_a_positive_finite_number(mean):
    _refused(_column(mean=mean), r't\.goals: "mean" must be a finite number greater than 0')


def test_a_mean_still_needs_a_distribution_that_takes_one():
    _refused(
        ColumnDef("goals", "int", set(), note={"mean": 2}),
        r'"mean" only applies with "distribution": "normal" or "exponential" or "poisson"',
    )


def test_a_distribution_writes_no_dbt_test():
    tables = {"t": TableDef(name="t", columns=[ColumnDef("id", "int", {"pk"}), _column(mean=2)])}
    assert hint_tests_for(tables) == []
