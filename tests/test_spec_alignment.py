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


# ---------------------------------------------------------
# PostgreSQL serial pseudo-types are integers
# ---------------------------------------------------------
_SERIAL_AND_INTEGER = [
    ("serial", "integer"),
    ("serial4", "integer"),
    ("bigserial", "bigint"),
    ("serial8", "bigint"),
    ("smallserial", "smallint"),
    ("serial2", "smallint"),
    ("BIGSERIAL", "bigint"),
]


@pytest.mark.parametrize(("serial", "_integer"), _SERIAL_AND_INTEGER)
def test_a_serial_type_is_an_integer_and_so_numeric(serial, _integer):
    assert kinds.is_integer_type(serial)
    assert kinds.is_numeric_type(serial)
    assert not kinds.is_decimal_type(serial)
    assert not kinds.is_boolean_type(serial)
    assert kinds.temporal_kind(serial) is None


def test_a_type_that_only_looks_like_serial_is_not_an_integer():
    assert not kinds.is_integer_type("serialized")
    assert not kinds.is_integer_type("text")


def test_a_serial_column_is_not_free_text():
    from model2data.generate.faker import is_free_text_type

    assert not is_free_text_type("bigserial")
    assert is_free_text_type("varchar(20)")


def _serial_tables(parent_type: str):
    return {
        "a": TableDef(name="a", columns=[ColumnDef("id", parent_type, {"pk"})]),
        "b": TableDef(
            name="b",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("a_id", "bigint")],
        ),
    }


_SERIAL_REFS = [
    {
        "source_table": "b",
        "source_column": "a_id",
        "target_table": "a",
        "target_column": "id",
    }
]


@pytest.mark.parametrize(("serial", "integer"), _SERIAL_AND_INTEGER[:6])
def test_a_serial_key_generates_what_its_integer_counterpart_does(serial, integer):
    as_of = datetime(2026, 1, 1)
    got = generate_data_from_dbml(
        _serial_tables(serial), _SERIAL_REFS, base_rows=25, seed=3, as_of=as_of
    )
    want = generate_data_from_dbml(
        _serial_tables(integer), _SERIAL_REFS, base_rows=25, seed=3, as_of=as_of
    )
    for table in ("a", "b"):
        pd.testing.assert_frame_equal(got[table], want[table])
    assert str(got["a"]["id"].dtype) == "Int64"
    assert got["b"]["a_id"].dropna().isin(got["a"]["id"]).all()
    assert got["b"]["a_id"].notna().any()


def test_a_bigserial_key_and_its_bigint_foreign_key_from_a_model_document():
    from model2data.model import from_dict, to_engine

    document = {
        "model2data": "0.2.0",
        "tables": {
            "a": {"columns": {"id": {"type": "bigserial", "pk": True}}},
            "b": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "a_id": {"type": "bigint", "references": "a.id"},
                }
            },
        },
    }
    engine = to_engine(from_dict(document))
    frames = generate_data_from_dbml(
        engine.tables, engine.refs, base_rows=20, seed=1, as_of=datetime(2026, 1, 1)
    )
    assert frames["a"]["id"].map(type).eq(int).all()
    assert frames["b"]["a_id"].dropna().isin(frames["a"]["id"]).all()


def test_a_bigserial_key_and_its_bigint_foreign_key_from_dbml(tmp_path):
    from model2data.parse.dbml import parse_dbml

    path = tmp_path / "serial.dbml"
    path.write_text(
        "Table a {\n  id bigserial [pk]\n}\n"
        "Table b {\n  id int [pk]\n  a_id bigint [ref: > a.id]\n}\n"
    )
    tables, refs = parse_dbml(path)
    frames = generate_data_from_dbml(tables, refs, base_rows=20, seed=1, as_of=datetime(2026, 1, 1))
    assert frames["a"]["id"].map(type).eq(int).all()
    assert frames["b"]["a_id"].dropna().isin(frames["a"]["id"]).all()
    assert frames["b"]["a_id"].notna().any()
