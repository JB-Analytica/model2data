"""`nullable_foreign_keys`: the foreign keys a model leaves empty on some rows, and how many."""

from __future__ import annotations

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.model import from_dict, nullable_foreign_keys, to_engine

DOCUMENT = {
    "model2data": "0.10.0",
    "tables": {
        "customers": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "region": {"type": "varchar"},
            }
        },
        "orders": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "customer_id": {"type": "int", "references": "customers.id"},
                "agent_id": {
                    "type": "int",
                    "references": "customers.id",
                    "generate": {"null_rate": 0.35},
                },
                "billing_id": {"type": "int", "not_null": True, "references": "customers.id"},
                "region": {
                    "type": "varchar",
                    "references": {"to": "customers.region", "via": "billing_id"},
                },
            },
        },
        "pairs": {
            "columns": {
                "a": {"type": "int"},
                "b": {"type": "int"},
                "c": {"type": "int", "pk": True},
            },
            "foreign_keys": [
                {"columns": ["a", "b"], "references": "grid", "to_columns": ["x", "y"]}
            ],
        },
        "grid": {"columns": {"x": {"type": "int", "pk": True}, "y": {"type": "int", "pk": True}}},
    },
}


def _model(rows: int | None = None, **extra):
    document = {**DOCUMENT, **extra}
    if rows is not None:
        document["run"] = {"rows": rows}
    return from_dict(document)


def test_lists_nullable_single_and_composite_foreign_keys_with_their_share():
    found = nullable_foreign_keys(_model(rows=200))
    assert found == [
        ("orders", "customer_id", "customers", 0.2),
        ("orders", "agent_id", "customers", 0.35),
        ("pairs", "a", "grid", 0.2),
        ("pairs", "b", "grid", 0.2),
    ]


def test_a_copied_value_a_not_null_and_a_key_are_left_out():
    names = {(table, column) for table, column, _, _ in nullable_foreign_keys(_model())}
    assert ("orders", "billing_id") not in names  # not_null
    assert ("orders", "region") not in names  # a copy, not a foreign key
    assert ("orders", "id") not in names


def test_the_default_share_is_the_engines_for_the_tables_rows():
    small = nullable_foreign_keys(_model(rows=50))[0][3]
    assert small == pytest.approx(0.2)  # capped at a fifth
    assert nullable_foreign_keys(_model())[0][3] == pytest.approx(0.2)  # the default 100 rows
    assert nullable_foreign_keys(_model(rows=10_000))[0][3] == pytest.approx(50 / 10_050)
    assert nullable_foreign_keys(_model(), rows=1000)[0][3] == pytest.approx(50 / 1050)


def test_rows_per_table_decides_for_its_table():
    document = {**DOCUMENT, "run": {"rows": 100_000, "rows_per_table": {"orders": 50}}}
    found = nullable_foreign_keys(from_dict(document))
    assert found[0][3] == pytest.approx(0.2)  # 50 rows: capped at a fifth
    assert found[2][3] == pytest.approx(50 / 100_050)  # pairs: the run's rows


def test_the_share_is_what_the_engine_leaves_empty():
    model = _model(rows=300)
    (_, _, _, default), (_, _, _, rate), *_ = nullable_foreign_keys(model)
    engine = to_engine(model)
    data = generate_data_from_dbml(engine.tables, engine.refs, base_rows=300, seed=3)
    orders = data["orders"]
    assert orders["customer_id"].isna().mean() == pytest.approx(default, abs=0.01)
    assert orders["agent_id"].isna().mean() == pytest.approx(rate, abs=0.01)


def test_combinations_columns_are_left_out():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "a": {"columns": {"id": {"type": "int", "pk": True}}},
            "b": {"columns": {"id": {"type": "int", "pk": True}}},
            "ab": {
                "combinations": ["a_id", "b_id"],
                "columns": {
                    "a_id": {"type": "int", "references": "a.id"},
                    "b_id": {"type": "int", "references": "b.id"},
                },
            },
        },
    }
    document["model2data"] = "0.10.0"
    assert nullable_foreign_keys(from_dict(document)) == []


def test_a_foreign_key_in_a_composite_primary_key_is_left_out():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "a": {"columns": {"id": {"type": "int", "pk": True}}},
            "link": {
                "columns": {
                    "a_id": {"type": "int", "references": "a.id"},
                    "n": {"type": "int"},
                },
                "keys": [{"pk": ["a_id", "n"]}],
            },
        },
    }
    assert nullable_foreign_keys(from_dict(document)) == []
