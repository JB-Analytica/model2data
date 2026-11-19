"""`rollup` (a parent column that totals its child rows) and `at_least` (every parent gets rows)."""

from __future__ import annotations

import copy
import json
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from model2data.dbt.naming import dbt_names, for_dbt
from model2data.dbt.tests import (
    ROLLUP_MACROS_FILE,
    dbt_tests,
    generate_dbt_yml,
    rollup_arguments,
)
from model2data.defects.checks import as_seeded, fails
from model2data.generate import rollups
from model2data.generate.core import (
    generate_data_from_dbml,
    get_uncovered_parents,
    reset_capped_tables,
)
from model2data.generate.days import generate_days
from model2data.generate.hints import validate_hints
from model2data.model import dump, from_dbml, from_dict, load, to_engine
from model2data.model.document import document_version
from model2data.model.errors import ModelError

AS_OF = date(2026, 11, 19)
ROWS = {"customers": 40, "products": 12, "orders": 100, "order_items": 220}


def _shop() -> dict[str, Any]:
    """A coffee webshop: orders total their lines, and every order has at least one."""
    return copy.deepcopy(
        {
            "model2data": "0.6.0",
            "tables": {
                "customers": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "name": {"type": "varchar", "not_null": True},
                    }
                },
                "products": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "price": {
                            "type": "numeric(10,2)",
                            "not_null": True,
                            "generate": {"min": 4, "max": 40},
                        },
                    }
                },
                "orders": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "customer_id": {
                            "type": "bigint",
                            "not_null": True,
                            "references": "customers.id",
                        },
                        "ordered_at": {"type": "timestamp", "not_null": True},
                        "total_amount": {
                            "type": "numeric(12,2)",
                            "not_null": True,
                            "generate": {
                                "rollup": {"of": "order_items", "sum": ["quantity", "unit_price"]}
                            },
                        },
                        "line_count": {
                            "type": "integer",
                            "generate": {"rollup": {"of": "order_items", "count": True}},
                        },
                        "products_bought": {
                            "type": "integer",
                            "generate": {
                                "rollup": {"of": "order_items", "count_distinct": "product_id"}
                            },
                        },
                        "cheapest_line": {
                            "type": "numeric(10,2)",
                            "generate": {"rollup": {"of": "order_items", "min": "unit_price"}},
                        },
                        "last_shipped_on": {
                            "type": "date",
                            "generate": {"rollup": {"of": "order_items", "max": "shipped_on"}},
                        },
                    }
                },
                "order_items": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "order_id": {
                            "type": "bigint",
                            "not_null": True,
                            "references": "orders.id",
                            "generate": {"at_least": 1},
                        },
                        "product_id": {
                            "type": "bigint",
                            "not_null": True,
                            "references": "products.id",
                        },
                        "quantity": {
                            "type": "integer",
                            "not_null": True,
                            "generate": {"min": 1, "max": 6},
                        },
                        "unit_price": {
                            "type": "numeric(10,2)",
                            "not_null": True,
                            "references": "products.price",
                        },
                        "shipped_on": {"type": "date"},
                    }
                },
            },
        }
    )


def _generate(document: dict, seed: int = 7, rows: dict | None = None) -> dict[str, pd.DataFrame]:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables,
        inputs.refs,
        base_rows=50,
        row_overrides=rows or ROWS,
        seed=seed,
        as_of=AS_OF,
    )


def _engine(document: dict) -> tuple[dict, list]:
    inputs = to_engine(from_dict(document))
    return inputs.tables, inputs.refs


def _messages(document: dict) -> list[str]:
    with pytest.raises(ModelError) as raised:
        from_dict(document)
    return [str(issue) for issue in raised.value.issues]


def _column(document: dict, table: str, column: str) -> dict:
    return document["tables"][table]["columns"][column]


# ---------------------------------------------------------------------------
# The end-to-end coffee webshop
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def shop() -> dict[str, pd.DataFrame]:
    return _generate(_shop())


def test_every_order_has_a_line(shop):
    orders, lines = shop["orders"], shop["order_items"]
    assert len(orders) == 100 and len(lines) == 220
    assert set(orders["id"]) <= set(lines["order_id"])


def test_an_order_total_is_the_sum_of_quantity_times_unit_price(shop):
    orders, lines = shop["orders"], shop["order_items"]
    product = lines["quantity"].astype(float) * lines["unit_price"].astype(float)
    expected = product.groupby(lines["order_id"]).sum().round(2)
    totals = orders.set_index("id")["total_amount"].astype(float)
    assert np.allclose(totals.loc[expected.index], expected.values, atol=1e-9)
    assert orders["total_amount"].dtype == np.float64


def test_a_unit_price_is_its_products_price(shop):
    lines, products = shop["order_items"], shop["products"]
    prices = products.set_index("id")["price"]
    assert (lines["unit_price"].values == prices.loc[lines["product_id"]].values).all()


def test_counts_min_and_max(shop):
    orders, lines = shop["orders"], shop["order_items"]
    grouped = lines.groupby("order_id")
    by_id = orders.set_index("id")
    sizes = grouped.size()
    assert (by_id["line_count"].loc[sizes.index].to_numpy() == sizes.to_numpy()).all()
    assert str(orders["line_count"].dtype) == "Int64"
    distinct = grouped["product_id"].nunique()
    assert (by_id["products_bought"].loc[distinct.index].to_numpy() == distinct.to_numpy()).all()
    cheapest = grouped["unit_price"].min()
    assert np.allclose(by_id["cheapest_line"].loc[cheapest.index].astype(float), cheapest)
    shipped = lines.dropna(subset=["shipped_on"]).groupby("order_id")["shipped_on"].max()
    assert (by_id["last_shipped_on"].loc[shipped.index].to_numpy() == shipped.to_numpy()).all()


def test_the_same_seed_gives_the_same_bytes(shop):
    again = _generate(_shop())
    for name, frame in shop.items():
        assert frame.to_csv(index=False) == again[name].to_csv(index=False)


def test_a_model_without_the_hints_draws_what_it_drew_before():
    """The rollup column is drawn as before and then overwritten, so the rest is unchanged."""
    document = _shop()
    plain = copy.deepcopy(document)
    for name in ("total_amount", "line_count", "products_bought", "cheapest_line"):
        del _column(plain, "orders", name)["generate"]
    del _column(plain, "orders", "last_shipped_on")["generate"]
    hinted = _generate(document)
    unhinted = _generate(plain)
    pd.testing.assert_frame_equal(hinted["customers"], unhinted["customers"])
    pd.testing.assert_frame_equal(hinted["products"], unhinted["products"])
    assert (hinted["orders"]["customer_id"] == unhinted["orders"]["customer_id"]).all()


def test_an_order_with_no_lines_totals_zero_and_its_min_is_null():
    document = _shop()
    del _column(document, "order_items", "order_id")["generate"]
    _column(document, "orders", "total_amount")["not_null"] = False
    frames = _generate(document, rows={**ROWS, "order_items": 30})
    orders = frames["orders"]
    lonely = ~orders["id"].isin(frames["order_items"]["order_id"])
    assert lonely.any()
    assert (orders.loc[lonely, "total_amount"] == 0).all()
    assert (orders.loc[lonely, "line_count"] == 0).all()
    assert orders.loc[lonely, "cheapest_line"].isna().all()
    assert orders.loc[lonely, "last_shipped_on"].isna().all()


def test_a_shortfall_is_reported_not_raised():
    reset_capped_tables()
    document = _shop()
    _column(document, "order_items", "order_id")["generate"]["at_least"] = 3
    frames = _generate(document, rows={**ROWS, "order_items": 150})
    lines = frames["order_items"]
    assert len(lines) == 150
    assert lines["order_id"].nunique() == 50  # 150 rows cover 50 orders three times each
    [message] = get_uncovered_parents()
    assert message == (
        "order_items: 150 rows cover 50 of 100 orders; at_least 3 on order_id needs 300"
    )
    reset_capped_tables()
    assert get_uncovered_parents() == []


def test_at_least_two_gives_every_parent_two_rows():
    document = _shop()
    _column(document, "order_items", "order_id")["generate"]["at_least"] = 2
    lines = _generate(document)["order_items"]
    assert lines["order_id"].value_counts().min() >= 2


def test_coverage_survives_nulls_on_a_nullable_foreign_key():
    document = _shop()
    order_id = _column(document, "order_items", "order_id")
    order_id["not_null"] = False
    order_id["generate"]["null_rate"] = 0.3
    _column(document, "orders", "total_amount")["not_null"] = False
    lines = _generate(document)["order_items"]
    assert lines["order_id"].isna().sum() > 0
    assert lines["order_id"].dropna().nunique() == 100


def test_coverage_survives_a_cross_table_after():
    document = _shop()
    _column(document, "order_items", "shipped_on").update(
        {"type": "timestamp", "generate": {"after": "orders.ordered_at"}}
    )
    _column(document, "orders", "last_shipped_on")["type"] = "timestamp"
    frames = _generate(document)
    lines, orders = frames["order_items"], frames["orders"]
    assert lines["order_id"].nunique() == 100
    joined = lines.merge(orders[["id", "ordered_at"]], left_on="order_id", right_on="id")
    shipped = joined.dropna(subset=["shipped_on"])
    assert (pd.to_datetime(shipped["shipped_on"]) >= pd.to_datetime(shipped["ordered_at"])).all()


def test_coverage_survives_composite_key_dedup():
    document = _shop()
    document["tables"]["order_items"]["keys"] = [{"unique": ["order_id", "product_id"]}]
    lines = _generate(document)["order_items"]
    assert lines["order_id"].nunique() == 100
    assert not lines.duplicated(subset=["order_id", "product_id"]).any()


def test_coverage_on_a_self_reference_and_a_rollup_of_it():
    document = {
        "model2data": "0.6.0",
        "tables": {
            "employees": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "manager_id": {
                        "type": "bigint",
                        "references": "employees.id",
                        "generate": {"at_least": 1},
                    },
                    "reports": {
                        "type": "integer",
                        "generate": {"rollup": {"of": "employees", "count": True}},
                    },
                }
            }
        },
    }
    frames = _generate(document, rows={"employees": 30})
    staff = frames["employees"]
    counts = staff["manager_id"].dropna().astype(int).value_counts()
    by_id = staff.set_index("id")["reports"]
    assert (by_id.loc[counts.index].to_numpy() == counts.to_numpy()).all()
    assert by_id.sum() == staff["manager_id"].notna().sum()


def test_a_rollup_of_a_rollup_is_computed_child_first():
    document = _shop()
    document["tables"]["customers"]["columns"]["lifetime_value"] = {
        "type": "numeric(14,2)",
        "generate": {"rollup": {"of": "orders", "sum": "total_amount"}},
    }
    frames = _generate(document)
    orders = frames["orders"]
    expected = orders.groupby("customer_id")["total_amount"].sum().round(2)
    values = frames["customers"].set_index("id")["lifetime_value"]
    assert np.allclose(values.loc[expected.index], expected.values)


def test_via_picks_the_foreign_key_to_group_by():
    document: dict[str, Any] = {
        "model2data": "0.6.0",
        "tables": {
            "accounts": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "sent": {
                        "type": "numeric(12,2)",
                        "generate": {
                            "rollup": {"of": "transfers", "sum": "amount", "via": "from_id"}
                        },
                    },
                }
            },
            "transfers": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "from_id": {"type": "bigint", "references": "accounts.id"},
                    "to_id": {"type": "bigint", "references": "accounts.id"},
                    "amount": {"type": "numeric(10,2)", "not_null": True},
                }
            },
        },
    }
    frames = _generate(document, rows={"accounts": 10, "transfers": 60})
    transfers = frames["transfers"].dropna(subset=["from_id"])
    expected = transfers.groupby("from_id")["amount"].sum().round(2)
    values = frames["accounts"].set_index("id")["sent"]
    assert np.allclose(values.loc[expected.index.astype(int)], expected.values)

    del document["tables"]["accounts"]["columns"]["sent"]["generate"]["rollup"]["via"]
    [message] = _messages(document)
    assert "2 foreign keys onto accounts (from_id, to_id)" in message
    assert message.startswith("tables.accounts.columns.sent.generate.rollup:")


def test_an_integer_sum_stays_an_integer():
    document = _shop()
    document["tables"]["orders"]["columns"]["units"] = {
        "type": "integer",
        "generate": {"rollup": {"of": "order_items", "sum": "quantity"}},
    }
    frames = _generate(document)
    lines = frames["order_items"]
    expected = lines.groupby("order_id")["quantity"].sum()
    units = frames["orders"].set_index("id")["units"]
    assert str(units.dtype) == "Int64"
    assert (units.loc[expected.index].to_numpy() == expected.to_numpy()).all()


def test_a_sum_is_rounded_to_the_scale_of_what_it_sums():
    lines = pd.DataFrame(
        {"order_id": [1, 1, 2], "quantity": [3, 1, 1], "unit_price": [0.335, 0.1, 0.004]}
    )
    orders = pd.DataFrame({"id": [1, 2, 3]})
    rollup = rollups.Rollup(
        "orders", "total", "order_items", "order_id", "id", "sum", ("quantity", "unit_price"), 2
    )
    assert rollups.rolled_up(rollup, orders, lines) == [1.11, 0.0, 0]
    whole = rollups.Rollup("orders", "n", "order_items", "order_id", "id", "sum", ("quantity",), 0)
    assert rollups.rolled_up(whole, orders, lines) == [4, 1, 0]


def test_column_scale():
    tables, _ = _engine(_shop())
    columns = {c.name: c for c in tables["order_items"].columns}
    assert rollups.column_scale(columns["quantity"]) == 0
    assert rollups.column_scale(columns["unit_price"]) == 2
    free = copy.copy(columns["unit_price"])
    free.data_type = "decimal"
    assert rollups.column_scale(free) == 2


# ---------------------------------------------------------------------------
# What the checks refuse
# ---------------------------------------------------------------------------
def _with_rollup(spec: Any, column_type: str = "numeric(12,2)", **extra: Any) -> dict:
    document = _shop()
    _column(document, "orders", "total_amount").update(
        {"type": column_type, "generate": {"rollup": spec, **extra}}
    )
    return document


ROLLUP_PATH = "tables.orders.columns.total_amount.generate.rollup: "


@pytest.mark.parametrize(
    ("spec", "column_type", "extra", "expected"),
    [
        pytest.param(
            {"of": "nowhere", "count": True},
            "integer",
            {},
            "names the table 'nowhere' in `of`, which is not in the model",
            id="unknown-table",
        ),
        pytest.param(
            {"of": "customers", "count": True},
            "integer",
            {},
            "reads customers, which has no foreign key onto orders",
            id="no-foreign-key",
        ),
        pytest.param(
            {"of": "order_items", "sum": "missing"},
            "numeric(12,2)",
            {},
            "`sum` names 'missing', which is not a column of order_items",
            id="unknown-column",
        ),
        pytest.param(
            {"of": "order_items", "sum": "shipped_on"},
            "numeric(12,2)",
            {},
            "`sum` names shipped_on, and order_items.shipped_on (date) is not numeric",
            id="sum-of-a-date",
        ),
        pytest.param(
            {"of": "order_items", "sum": "quantity"},
            "varchar",
            {},
            '"varchar" is not numeric',
            id="sum-into-text",
        ),
        pytest.param(
            {"of": "order_items", "sum": ["quantity", "unit_price"]},
            "integer",
            {},
            "a decimal column, and orders.total_amount is an integer column: make it numeric",
            id="sum-of-decimals-into-integer",
        ),
        pytest.param(
            {"of": "order_items", "count": True},
            "numeric(12,2)",
            {},
            "`count` counts rows, so orders.total_amount must be an integer column",
            id="count-into-decimal",
        ),
        pytest.param(
            {"of": "order_items", "min": "shipped_on"},
            "numeric(12,2)",
            {},
            "needs orders.total_amount to be a date column too",
            id="min-of-a-date-into-a-number",
        ),
        pytest.param(
            {"of": "order_items", "max": "unit_price"},
            "date",
            {},
            "a number, needs orders.total_amount to be numeric",
            id="max-of-a-number-into-a-date",
        ),
        pytest.param(
            {"of": "order_items", "max": "unit_price"},
            "integer",
            {},
            "a decimal column, needs orders.total_amount to be a decimal column",
            id="max-of-a-decimal-into-an-integer",
        ),
        pytest.param(
            {"of": "order_items", "count": True},
            "integer",
            {"min": 0},
            'decides the value, so the column cannot also have "min"',
            id="conflicting-hint",
        ),
        pytest.param(
            {"of": "order_items", "sum": "quantity", "via": "product_id"},
            "numeric(12,2)",
            {},
            "`via` names 'product_id', which is not a foreign key of order_items onto orders",
            id="via-not-onto-the-parent",
        ),
    ],
)
def test_a_rollup_that_cannot_work_is_refused(spec, column_type, extra, expected):
    messages = _messages(_with_rollup(spec, column_type, **extra))
    assert any(m.startswith(ROLLUP_PATH) and expected in m for m in messages), messages


def test_a_min_of_text_is_refused():
    document = _shop()
    document["tables"]["order_items"]["columns"]["note"] = {"type": "varchar"}
    _column(document, "orders", "total_amount").update(
        {"type": "varchar", "generate": {"rollup": {"of": "order_items", "min": "note"}}}
    )
    [message] = _messages(document)
    assert '"varchar" is not numeric or a date or timestamp column' in message
    # The engine says the same of a text column a valid document cannot carry.
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["cheapest_line"].note = {"rollup": {"of": "order_items", "min": "id"}}
    columns["cheapest_line"].data_type = "varchar"
    tables["order_items"].columns[0].data_type = "varchar"
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("is not a numeric, date or timestamp column" in m for m in messages), messages


def test_a_rollup_on_a_key_a_foreign_key_or_an_enum_is_refused():
    document = _shop()
    _column(document, "orders", "line_count")["unique"] = True
    assert any("cannot sit on a primary key" in m for m in _messages(document))

    document = _shop()
    _column(document, "orders", "customer_id")["generate"] = {
        "rollup": {"of": "order_items", "count": True}
    }
    assert any("cannot sit on a foreign key" in m for m in _messages(document))

    document = _shop()
    document["enums"] = {"size": ["1", "2"]}
    _column(document, "orders", "line_count")["type"] = "size"
    assert any('"size" is not numeric' in m for m in _messages(document))


def test_reading_a_rollup_while_generating_is_refused():
    document = _shop()
    document["tables"]["orders"]["columns"]["coupon"] = {
        "type": "varchar",
        "generate": {"when": {"total_amount": [0]}},
    }
    messages = _messages(document)
    assert any(
        m.startswith("tables.orders.columns.coupon.generate.when:")
        and "orders.total_amount, a rollup" in m
        for m in messages
    ), messages

    document = _shop()
    document["tables"]["refunds"] = {
        "columns": {
            "id": {"type": "bigint", "pk": True},
            "amount": {"type": "numeric(12,2)", "references": "orders.total_amount"},
        }
    }
    messages = _messages(document)
    assert any(m.startswith("tables.refunds.columns.amount.references:") for m in messages)


def test_reading_a_rollup_with_after_is_refused():
    document = _shop()
    _column(document, "orders", "last_shipped_on")["type"] = "timestamp"
    _column(document, "order_items", "shipped_on").update(
        {"type": "timestamp", "generate": {"after": "orders.last_shipped_on"}}
    )
    messages = _messages(document)
    assert any(".generate.after:" in m and "a rollup" in m for m in messages), messages


def test_rollups_that_read_each_other_are_refused():
    document = {
        "model2data": "0.6.0",
        "tables": {
            "nodes": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "parent_id": {"type": "bigint", "references": "nodes.id"},
                    "a": {
                        "type": "integer",
                        "generate": {"rollup": {"of": "nodes", "sum": "b"}},
                    },
                    "b": {
                        "type": "integer",
                        "generate": {"rollup": {"of": "nodes", "sum": "a"}},
                    },
                }
            }
        },
    }
    messages = _messages(document)
    assert any("reads it back" in m for m in messages), messages


def test_a_not_null_min_without_at_least_warns():
    document = _shop()
    _column(document, "orders", "cheapest_line")["not_null"] = True
    model = from_dict(document)
    assert not any("at_least" in str(w) for w in model.warnings)
    del _column(document, "order_items", "order_id")["generate"]
    model = from_dict(document)
    [warning] = [str(w) for w in model.warnings if "at_least" in str(w)]
    assert "tables.orders.columns.cheapest_line.generate.rollup:" in warning
    assert "add `at_least: 1` to order_items.order_id" in warning


@pytest.mark.parametrize(
    ("column", "settings", "expected"),
    [
        ("quantity", {}, "only sits on a foreign-key column"),
        ("order_id", {"unique": True}, "cannot sit on a unique foreign key"),
    ],
)
def test_at_least_where_it_cannot_work_is_refused(column, settings, expected):
    document = _shop()
    _column(document, "order_items", column).update(settings)
    _column(document, "order_items", column).setdefault("generate", {})["at_least"] = 1
    messages = _messages(document)
    assert any(
        m.startswith(f"tables.order_items.columns.{column}.generate.at_least:") and expected in m
        for m in messages
    ), messages


def test_the_engine_checks_the_same_things():
    tables, refs = _engine(_shop())
    validate_hints(tables, refs)
    columns = {c.name: c for c in tables["order_items"].columns}
    columns["order_id"].note = {"at_least": 0}
    with pytest.raises(ValueError, match=r"order_items\.order_id: .*whole number of 1 or more"):
        validate_hints(tables, refs)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("order_items", "must be an object naming the child table"),
        ({"of": "order_items", "count": True, "avg": "x"}, "has no 'avg'"),
        ({"of": "order_items"}, "needs exactly one aggregate"),
        ({"of": "order_items", "count": True, "sum": "quantity"}, "and it has 2"),
        ({"count": True}, "needs `of`"),
        ({"of": "order_items", "count": 1}, "`count` must be true"),
        ({"of": "order_items", "sum": []}, "`sum` must be a column of order_items"),
        ({"of": "order_items", "min": ["quantity"]}, "`min` must be a column of order_items"),
        ({"of": "order_items", "count": True, "via": 3}, "`via` must be a column"),
    ],
)
def test_the_engine_refuses_a_malformed_rollup(spec, expected):
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["line_count"].note = {"rollup": spec}
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any(expected in m for m in messages), messages


def test_a_rollup_onto_a_non_key_is_refused():
    tables, refs = _engine(_shop())
    refs = [
        {**r, "target_column": "line_count"} if r["source_column"] == "order_id" else r
        for r in refs
    ]
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("not a key" in m for m in messages), messages


def test_days_refuses_a_rollup_over_a_table_that_moves_on():
    document = _shop()
    document["tables"]["order_items"]["incremental"] = {"new_per_day": 5}
    with pytest.raises(ValueError, match="rollup"):
        generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)


# ---------------------------------------------------------------------------
# Spec version, DBML and the mirroring warning
# ---------------------------------------------------------------------------
def test_an_older_document_cannot_use_the_hints():
    document = _shop()
    document["model2data"] = "0.5.0"
    messages = _messages(document)
    assert any("rollup" in m and "0.6.0" in m for m in messages), messages
    assert any("at_least" in m and "0.6.0" in m for m in messages), messages


def test_a_document_with_the_hints_is_written_as_0_6():
    model = from_dict(_shop())
    assert str(document_version(model)) == "0.6.0"
    assert load(dump(model)).tables["orders"].columns["total_amount"].generate["rollup"] == {
        "of": "order_items",
        "sum": ["quantity", "unit_price"],
    }


DBML = """
Table orders {
  id bigint [pk]
  total numeric(12,2) [note: '{"rollup": {"of": "lines", "sum": ["qty", "price"]}}']
}
Table products {
  id bigint [pk]
  price numeric(10,2) [not null]
}
Table lines {
  id bigint [pk]
  order_id bigint [not null, ref: > orders.id, note: '{"at_least": 1}']
  product_id bigint [not null, ref: > products.id]
  qty integer [not null, note: '{"min": 1, "max": 6}']
  price numeric(10,2) [not null, ref: > products.price]
}
"""


def test_the_hints_read_from_dbml_notes():
    model = from_dbml(DBML)
    assert model.version == "0.6.0"
    assert model.tables["orders"].columns["total"].generate["rollup"]["of"] == "lines"
    assert model.tables["lines"].columns["order_id"].generate == {"at_least": 1}
    assert not any("price" in str(w) and "mirror" in str(w) for w in model.warnings)
    frames = _generate(json.loads(json.dumps(_as_dict(model))), rows={"orders": 20, "lines": 50})
    lines = frames["lines"]
    assert lines["order_id"].nunique() == 20
    expected = (lines["qty"] * lines["price"]).groupby(lines["order_id"]).sum().round(2)
    totals = frames["orders"].set_index("id")["total"]
    assert np.allclose(totals.loc[expected.index], expected.values)


def _as_dict(model) -> dict:
    from model2data.model.document import to_dict

    return to_dict(model)


def test_a_mirrored_reference_beside_its_foreign_key_does_not_warn():
    model = from_dict(_shop())
    assert not [w for w in model.warnings if "unit_price" in str(w)]


def test_a_plain_column_reference_without_a_foreign_key_still_warns():
    document = _shop()
    del _column(document, "order_items", "product_id")["references"]
    model = from_dict(document)
    assert [w for w in model.warnings if "unit_price" in str(w)]


def test_a_mirrored_reference_beside_a_table_foreign_key_does_not_warn():
    document = _shop()
    del _column(document, "order_items", "product_id")["references"]
    document["tables"]["products"]["columns"]["roast"] = {"type": "varchar", "not_null": True}
    document["tables"]["products"]["keys"] = [{"unique": ["id", "roast"]}]
    document["tables"]["order_items"]["columns"]["roast"] = {"type": "varchar"}
    document["tables"]["order_items"]["foreign_keys"] = [
        {
            "columns": ["product_id", "roast"],
            "references": "products",
            "to_columns": ["id", "roast"],
        }
    ]
    model = from_dict(document)
    assert not [w for w in model.warnings if "unit_price" in str(w)]


# ---------------------------------------------------------------------------
# The dbt test
# ---------------------------------------------------------------------------
def test_the_dbt_test_is_written_with_its_macro(tmp_path):
    tables, refs = _engine(_shop())
    generate_dbt_yml(tmp_path, tables, refs, source_name="shop")
    text = (tmp_path / "models" / "staging" / "stg_orders.yml").read_text()
    assert "model2data_rollup:" in text
    assert "child: ref('stg_order_items')" in text
    assert "tolerance: 0.005" in text
    macros = list(tmp_path.rglob(ROLLUP_MACROS_FILE))
    assert len(macros) == 1


def test_no_rollup_writes_no_macro(tmp_path):
    document = _shop()
    for column in document["tables"]["orders"]["columns"].values():
        column.pop("generate", None)
    _column(document, "order_items", "order_id").pop("generate")
    tables, refs = _engine(document)
    generate_dbt_yml(tmp_path, tables, refs, source_name="shop")
    assert not list(tmp_path.rglob(ROLLUP_MACROS_FILE))


def test_rollup_arguments():
    sum_rollup = rollups.Rollup("o", "t", "l", "o_id", "id", "sum", ("q", "p"), 2)
    assert rollup_arguments(sum_rollup) == {
        "child": "ref('stg_l')",
        "foreign_key": "o_id",
        "field": "id",
        "aggregate": "sum",
        "columns": ["q", "p"],
        "tolerance": 0.005,
    }
    whole = rollups.Rollup("o", "t", "l", "o_id", "id", "sum", ("q",), 0)
    assert rollup_arguments(whole)["tolerance"] == 0
    count = rollups.Rollup("o", "t", "l", "o_id", "id", "count", (), 0)
    assert "columns" not in rollup_arguments(count)
    assert "tolerance" not in rollup_arguments(count)


def test_the_defects_check_reads_the_rollup_test_as_its_sql_does(shop):
    tables, refs = _engine(_shop())
    tests = [t for t in dbt_tests(tables, refs) if t.type == "model2data_rollup"]
    assert {t.column for t in tests} == {
        "total_amount",
        "line_count",
        "products_bought",
        "cheapest_line",
        "last_shipped_on",
    }
    assert all(t.parent == ("order_items", "order_id") for t in tests)
    seeded = {key: as_seeded(frame) for key, frame in shop.items()}
    assert not any(fails(t, seeded) for t in tests)
    assert not fails(tests[0], {"orders": seeded["orders"]})

    for test in tests:
        broken = shop["orders"].copy()
        if test.column == "last_shipped_on":
            broken["last_shipped_on"] = "1999-01-01"
        else:
            broken[test.column] = 9999
        assert fails(test, {**seeded, "orders": as_seeded(broken)}), test.column
        emptied = shop["orders"].copy()
        emptied[test.column] = None
        assert fails(test, {**seeded, "orders": as_seeded(emptied)}), test.column


def test_the_cli_reports_a_shortfall(tmp_path, monkeypatch, capsys):
    from model2data.cli import main as generate_cli

    monkeypatch.chdir(tmp_path)
    document = _shop()
    _column(document, "order_items", "order_id")["generate"]["at_least"] = 3
    model = tmp_path / "shop.model2data.yml"
    model.write_text(dump(from_dict(document)), encoding="utf-8")
    generate_cli(
        file=model,
        rows=20,
        rows_for=["order_items=30"],
        seed=1,
        name="short",
        force=True,
        adapter="duckdb",
    )
    out = capsys.readouterr().out
    assert "Foreign keys with too few rows for their at_least" in out
    assert "order_items: 30 rows cover 10 of 20 orders; at_least 3 on order_id needs 60" in out


# ---------------------------------------------------------------------------
# Paths the coffee webshop does not take
# ---------------------------------------------------------------------------
def test_coverage_on_a_deferred_foreign_key():
    """customers.favourite_order_id points forward at orders, which reads customers."""
    document = _shop()
    document["tables"]["customers"]["columns"]["favourite_order_id"] = {
        "type": "bigint",
        "references": "orders.id",
        "generate": {"at_least": 1},
    }
    frames = _generate(document, rows={**ROWS, "customers": 120})
    favourites = frames["customers"]["favourite_order_id"]
    assert set(frames["orders"]["id"]) <= set(favourites.dropna())


def test_dedup_leaves_a_row_whose_key_columns_are_all_covered():
    document = _shop()
    document["tables"]["order_items"]["keys"] = [{"unique": ["order_id", "product_id"]}]
    _column(document, "order_items", "product_id")["generate"] = {"at_least": 1}
    lines = _generate(document, rows={**ROWS, "products": 100, "order_items": 120})["order_items"]
    assert lines["order_id"].nunique() == 100
    assert lines["product_id"].nunique() == 100


def test_a_programmatic_model_with_the_hints_is_written_as_0_6():
    document = _shop()
    document["model2data"] = "0.5.0"
    del _column(document, "order_items", "order_id")["generate"]
    for column in document["tables"]["orders"]["columns"].values():
        column.pop("generate", None)
    model = from_dict(document)
    assert str(document_version(model)) == "0.5.0"
    model.tables["orders"].columns["line_count"].generate["rollup"] = {
        "of": "order_items",
        "count": True,
    }
    assert str(document_version(model)) == "0.6.0"


def test_the_cli_names_the_spec_a_file_is_written_against(tmp_path):
    from model2data.cli import _spec_of

    model = tmp_path / "shop.model2data.yml"
    model.write_text(dump(from_dict(_shop())), encoding="utf-8")
    assert _spec_of(model) == "0.6.0"


def test_a_rollup_onto_a_table_outside_the_default_schema():
    document = _shop()
    tables = document["tables"]
    tables["sales.order_items"] = tables.pop("order_items")
    for column in tables["orders"]["columns"].values():
        rollup = column.get("generate", {}).get("rollup")
        if rollup:
            rollup["of"] = "sales.order_items"
    engine_tables, refs = for_dbt(*_engine(document), dbt_names(tables))
    tests = [t for t in dbt_tests(engine_tables, refs) if t.type == "model2data_rollup"]
    assert tests and all(t.arguments["child"] == "ref('stg_sales_order_items')" for t in tests), [
        t.arguments for t in tests
    ]


def test_an_enum_child_column_has_no_min():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["order_items"].columns}
    columns["shipped_on"].enum_values = ["a", "b"]
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("is not a numeric, date or timestamp column" in m for m in messages), messages


def test_a_rollup_read_by_two_others_is_not_a_cycle():
    tables, refs = _engine(_shop())
    order_columns = {c.name: c for c in tables["orders"].columns}
    order_columns["line_count"].note = {"rollup": {"of": "order_items", "count": True}}
    customer = tables["customers"]
    from model2data.parse.dbml import ColumnDef

    customer.columns.append(
        ColumnDef(
            "spend",
            "numeric(14,2)",
            settings=set(),
            note={"rollup": {"of": "orders", "sum": ["total_amount", "line_count"]}},
        )
    )
    assert not [p for p in rollups.problems(tables, refs) if not p.warning]


def test_a_missing_value_is_any_scalar_null():
    assert rollups._missing(None) and rollups._missing(float("nan")) and rollups._missing(pd.NA)
    assert not rollups._missing(3) and not rollups._missing([1, 2])


def test_the_defects_check_skips_a_line_without_an_order(shop):
    tables, refs = _engine(_shop())
    [test] = [
        t
        for t in dbt_tests(tables, refs)
        if t.type == "model2data_rollup" and t.column == "line_count"
    ]
    lines = shop["order_items"].astype({"order_id": "object"})
    lines.loc[0, "order_id"] = None
    seeded = {"orders": as_seeded(shop["orders"]), "order_items": as_seeded(lines)}
    assert fails(test, seeded)


# ---------------------------------------------------------------------------
# Engine-level checks a valid document never reaches
# ---------------------------------------------------------------------------
def test_the_engine_refuses_at_least_on_a_column_that_is_no_foreign_key():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["order_items"].columns}
    columns["quantity"].note = {"min": 1, "max": 6, "at_least": 1}
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert '"at_least" only applies to foreign-key columns.' in messages, messages


def test_the_engine_refuses_a_rollup_on_a_foreign_key():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["customer_id"].note = {"rollup": {"of": "order_items", "count": True}}
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("cannot sit on a foreign key" in m for m in messages), messages


def test_the_engine_refuses_a_sum_over_a_column_that_is_not_numeric():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["line_count"].note = {"rollup": {"of": "order_items", "sum": "shipped_on"}}
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("`sum` names shipped_on" in m and "is not numeric" in m for m in messages), messages


def test_a_rollup_that_reads_the_same_rollup_twice_is_not_a_cycle():
    tables, refs = _engine(_shop())
    from model2data.parse.dbml import ColumnDef

    tables["customers"].columns.append(
        ColumnDef(
            "spend_squared",
            "numeric(14,2)",
            settings=set(),
            note={"rollup": {"of": "orders", "sum": ["total_amount", "total_amount"]}},
        )
    )
    assert not [p for p in rollups.problems(tables, refs) if "reads it back" in p.message]


def test_the_engine_refuses_a_rollup_on_an_enum_column():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["line_count"].enum_values = ["few", "many"]
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert '"rollup" cannot sit on an enum column.' in messages, messages


def test_the_engine_refuses_a_sum_into_a_column_that_is_not_numeric():
    tables, refs = _engine(_shop())
    columns = {c.name: c for c in tables["orders"].columns}
    columns["last_shipped_on"].note = {"rollup": {"of": "order_items", "sum": "quantity"}}
    messages = [p.message for p in rollups.problems(tables, refs)]
    assert any("`sum` adds numbers" in m for m in messages), messages
