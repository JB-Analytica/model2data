"""Generation follows spec 0.2.0 where the engine used to differ from it.

Each test names the spec rule it pins. See model2data/spec/README.md.
"""

from datetime import date, datetime

import pandas as pd
import pytest

from model2data.generate import kinds
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.faker import (
    generate_column_values,
    get_duplicate_unique_columns,
    reset_duplicate_unique_columns,
)
from model2data.generate.hints import validate_hints
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = datetime(2026, 1, 1)


@pytest.mark.parametrize(
    "data_type, numeric, integer",
    [
        ("int", True, True),
        ("BIGINT", True, True),
        ("numeric(10,2)", True, False),
        ("real", True, False),
        ("money", True, False),
        ("number", True, False),
        ("double precision", True, False),
        ("phone_number", False, False),
        ("text", False, False),
    ],
)
def test_the_numeric_kind(data_type, numeric, integer):
    assert kinds.is_numeric_type(data_type) is numeric
    assert kinds.is_integer_type(data_type) is integer


@pytest.mark.parametrize(
    "data_type, kind",
    [
        ("date", "date"),
        ("timestamp", "timestamp"),
        ("timestamptz", "timestamp"),
        ("datetime", "timestamp"),
        ("date_time", "timestamp"),
        ("time", None),
        ("text", None),
    ],
)
def test_the_temporal_kind(data_type, kind):
    assert kinds.temporal_kind(data_type) == kind


@pytest.mark.parametrize("data_type", ["real", "money", "number"])
def test_real_money_and_number_columns_generate_decimals_in_range(data_type):
    column = ColumnDef("amount", data_type, {"not null"}, note={"min": 5, "max": 10})
    values = generate_column_values(column, row_count=50)
    assert all(isinstance(value, float) and 5 <= value <= 10 for value in values)


def test_min_max_on_a_money_column_is_accepted():
    tables = {
        "t": TableDef("t", [ColumnDef("amount", "money", {"not null"}, note={"min": 1})]),
    }
    validate_hints(tables, [])


def test_a_datetime_column_is_a_timestamp_not_a_time_of_day():
    column = ColumnDef("seen_at", "datetime", {"not null"})
    values = generate_column_values(column, row_count=5, as_of=AS_OF)
    assert all(datetime.fromisoformat(value) < AS_OF for value in values)


def test_a_date_only_column_is_still_a_date():
    column = ColumnDef("born_on", "date", {"not null"})
    values = generate_column_values(column, row_count=5, as_of=AS_OF)
    assert all(isinstance(value, date) for value in values)


def test_null_rate_nulls_exactly_the_floor_of_n_times_the_rate():
    column = ColumnDef("c", "text", set(), note={"null_rate": 0.29})
    values = generate_column_values(column, row_count=100)
    assert sum(value is None for value in values) == 29


def test_a_unique_foreign_key_takes_each_parent_at_most_once():
    tables = {
        "users": TableDef("users", [ColumnDef("id", "int", {"pk"})]),
        "profiles": TableDef(
            "profiles",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("user_id", "int", {"unique", "not null"}),
            ],
        ),
    }
    refs = [
        {
            "source_table": "profiles",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        }
    ]
    data = generate_data_from_dbml(
        tables, refs, base_rows=30, row_overrides={"users": 40}, seed=3, as_of=AS_OF
    )
    user_ids = data["profiles"]["user_id"]
    assert user_ids.is_unique
    assert user_ids.isin(data["users"]["id"]).all()


def test_a_unique_foreign_key_with_too_few_parents_is_reported():
    reset_duplicate_unique_columns()
    parents = [1, 2, 3]
    column = ColumnDef("user_id", "int", {"unique", "not null"})
    values = generate_column_values(
        column, row_count=5, fk_series=pd.Series(parents), ensure_unique=True, table_name="p"
    )
    assert sorted(values[:3]) == parents
    assert get_duplicate_unique_columns() == ["p.user_id: 2 duplicate value(s)"]
