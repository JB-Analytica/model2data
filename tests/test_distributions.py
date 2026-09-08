"""Numeric distribution hints: `distribution` and its `mean`/`stddev`/`median`/`spread`
parameters on integer and decimal columns.

The product ask this closes was "can I make a normal distribution or another kind
of distribution as well?" on a numeric column -- `min`/`max` alone could only shape
a uniform spread. Every statistical test here uses a fixed seed and a generous
tolerance so it never flakes; the exact bounds were chosen by running the real
implementation and leaving headroom, not derived analytically.
"""

import statistics

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import ColumnDef, TableDef


def _single_column_table(column: ColumnDef, table_name: str = "t") -> dict[str, TableDef]:
    return {table_name: TableDef(name=table_name, columns=[ColumnDef("id", "int", {"pk"}), column])}


# ---------------------------------------------------------
# Step 0: pin today's (uniform) behaviour before touching it
# ---------------------------------------------------------
def test_no_distribution_hint_reproduces_pre_1_7_frames():
    """A numeric column with no `distribution` key must still produce exactly
    what 1.6.0 produced with the same seed -- the guard rail every distribution
    added here works against."""
    tables = {
        "t": TableDef(
            name="t",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("amount", "numeric", {"not null"}),
                ColumnDef("qty", "int", {"not null"}),
            ],
        )
    }
    data = generate_data_from_dbml(tables, [], base_rows=8, seed=777)
    assert data["t"]["amount"].tolist() == [
        4169.09,
        3848.52,
        5163.13,
        3006.66,
        25.14,
        4363.61,
        685.34,
        8357.29,
    ]
    assert data["t"]["qty"].tolist() == [7, 89, 51, 52, 82, 100, 97, 9]


# ---------------------------------------------------------
# Shape: each distribution draws roughly what it says it does
# ---------------------------------------------------------
def test_normal_distribution_matches_mean_and_stddev():
    tables = _single_column_table(
        ColumnDef(
            "amount",
            "numeric",
            {"not null"},
            note={"distribution": "normal", "mean": 100, "stddev": 10},
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=1)
    values = data["t"]["amount"].tolist()
    assert 97 <= statistics.mean(values) <= 103
    assert 8 <= statistics.stdev(values) <= 12


def test_lognormal_distribution_median_and_mean():
    tables = _single_column_table(
        ColumnDef(
            "amount",
            "numeric",
            {"not null"},
            note={"distribution": "lognormal", "median": 80, "spread": 0.5},
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=2)
    values = data["t"]["amount"].tolist()
    sample_median = statistics.median(values)
    assert 70 <= sample_median <= 90
    assert statistics.mean(values) > sample_median


def test_exponential_distribution_matches_mean():
    tables = _single_column_table(
        ColumnDef(
            "amount", "numeric", {"not null"}, note={"distribution": "exponential", "mean": 30}
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=3)
    values = data["t"]["amount"].tolist()
    assert 26 <= statistics.mean(values) <= 34


def test_missing_parameters_default_to_midpoint_and_derived_spread():
    """No `mean`/`stddev` at all: normal centres on the midpoint of the
    default int range (0-100 -> 50) with stddev (max-min)/6."""
    tables = _single_column_table(
        ColumnDef("qty", "int", {"not null"}, note={"distribution": "normal"})
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=7)
    values = data["t"]["qty"].tolist()
    assert 45 <= statistics.mean(values) <= 55
    assert 12 <= statistics.stdev(values) <= 21


def test_missing_lognormal_parameters_default_to_midpoint_and_half_spread():
    """No `median`/`spread`: lognormal centres on the midpoint of the default
    decimal range (0-10,000 -> 5,000)."""
    tables = _single_column_table(
        ColumnDef("amount", "numeric", {"not null"}, note={"distribution": "lognormal"})
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=8)
    values = data["t"]["amount"].tolist()
    assert 4000 <= statistics.median(values) <= 6000


def test_missing_exponential_mean_defaults_to_midpoint():
    """No `mean`: exponential's scale defaults to the midpoint of the default
    int range (0-100 -> 50)."""
    tables = _single_column_table(
        ColumnDef("qty", "int", {"not null"}, note={"distribution": "exponential"})
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=9)
    values = data["t"]["qty"].tolist()
    assert 40 <= statistics.mean(values) <= 60


# ---------------------------------------------------------
# Clipping and typing
# ---------------------------------------------------------
def test_clipping_with_explicit_min_leaves_no_negatives():
    """A normal whose mean sits close to an explicit `min` would otherwise
    draw negatives; redraw-then-clamp keeps every value in bounds."""
    tables = _single_column_table(
        ColumnDef(
            "amount",
            "numeric",
            {"not null"},
            note={"distribution": "normal", "mean": 5, "stddev": 10, "min": 0},
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=2000, seed=4)
    values = data["t"]["amount"].tolist()
    assert min(values) >= 0


def test_integer_columns_stay_ints_under_a_distribution():
    tables = _single_column_table(
        ColumnDef(
            "qty", "int", {"not null"}, note={"distribution": "normal", "mean": 50, "stddev": 10}
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=200, seed=5)
    values = data["t"]["qty"].tolist()
    assert all(isinstance(v, int) for v in values)


def test_unique_normal_column_stays_unique():
    """`ensure_unique` still resolves collisions when the generator draws
    from a distribution instead of `random.sample` -- given a spread wide
    enough to hold that many distinct values."""
    tables = {
        "t": TableDef(
            name="t",
            columns=[
                ColumnDef(
                    "id",
                    "int",
                    {"pk"},
                    note={"distribution": "normal", "mean": 5000, "stddev": 500},
                )
            ],
        )
    }
    data = generate_data_from_dbml(tables, [], base_rows=200, seed=6)
    values = data["t"]["id"].tolist()
    assert len(set(values)) == len(values)


# ---------------------------------------------------------
# Validation: every error names the column
# ---------------------------------------------------------
def test_distribution_on_a_non_numeric_column_is_rejected():
    tables = _single_column_table(ColumnDef("label", "varchar", note={"distribution": "normal"}))
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_distribution_rejects_an_unknown_value():
    tables = _single_column_table(ColumnDef("total", "numeric", note={"distribution": "gaussian"}))
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_stddev_without_distribution_is_rejected():
    tables = _single_column_table(ColumnDef("total", "numeric", note={"stddev": 5}))
    with pytest.raises(
        ValueError, match=r't\.total: "stddev" only applies with "distribution": "normal"\.'
    ):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_stddev_with_the_wrong_distribution_is_rejected():
    tables = _single_column_table(
        ColumnDef("total", "numeric", note={"distribution": "exponential", "stddev": 5})
    )
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_median_with_the_wrong_distribution_is_rejected():
    tables = _single_column_table(
        ColumnDef("total", "numeric", note={"distribution": "normal", "median": 5})
    )
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_spread_with_the_wrong_distribution_is_rejected():
    tables = _single_column_table(
        ColumnDef("total", "numeric", note={"distribution": "normal", "spread": 0.5})
    )
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_mean_without_a_matching_distribution_is_rejected():
    tables = _single_column_table(ColumnDef("total", "numeric", note={"mean": 5}))
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


@pytest.mark.parametrize(
    "note",
    [
        {"distribution": "normal", "stddev": -1},
        {"distribution": "normal", "stddev": 0},
        {"distribution": "lognormal", "median": -5},
        {"distribution": "lognormal", "spread": 0},
    ],
)
def test_non_positive_stddev_median_spread_is_rejected(note):
    tables = _single_column_table(ColumnDef("total", "numeric", note=note))
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_a_bool_value_is_rejected_where_a_number_is_expected():
    tables = _single_column_table(
        ColumnDef("total", "numeric", note={"distribution": "normal", "mean": True})
    )
    with pytest.raises(ValueError, match=r"t\.total"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)
