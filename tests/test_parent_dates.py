"""A child row is created on or after its parents, and integer keys number the rows 1..N."""

from __future__ import annotations

import copy
from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest

from model2data.dbt.hint_tests import PARENT_MACROS_FILE, hint_tests_for
from model2data.dbt.tests import dbt_tests, generate_dbt_yml
from model2data.defects.checks import as_seeded, fails
from model2data.generate.core import generate_data_from_dbml, parent_rules_for
from model2data.generate.days import generate_days
from model2data.generate.hints import validate_hints
from model2data.generate.options import TimeProfile
from model2data.generate.parents import _Window
from model2data.generate.timeline import creation_column
from model2data.model import from_dict, to_engine
from model2data.model.errors import ModelError
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = date(2026, 10, 7)
MIDNIGHT = datetime(2026, 10, 7)

SHOP: dict[str, Any] = {
    "model2data": "0.5.0",
    "tables": {
        "customers": {
            "incremental": {"new_per_day": 8},
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "email": {"type": "email", "unique": True, "not_null": True},
                "created_at": {
                    "type": "timestamp",
                    "not_null": True,
                    "generate": {"growth": 0.5},
                },
            },
        },
        "products": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "launched_on": {"type": "date", "not_null": True},
            }
        },
        "orders": {
            "incremental": {"new_per_day": 30},
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "customer_id": {
                    "type": "bigint",
                    "not_null": True,
                    "references": "customers.id",
                    "generate": {"skew": 0.8},
                },
                "order_date": {
                    "type": "timestamp",
                    "not_null": True,
                    "generate": {"business_hours": True, "growth": 0.5, "seasonality": 0.6},
                },
                "shipped_at": {"type": "timestamp", "generate": {"after": "order_date"}},
            },
        },
        "reviews": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "product_id": {"type": "bigint", "not_null": True, "references": "products.id"},
                "customer_id": {"type": "bigint", "references": "customers.id"},
                "review_date": {"type": "date", "not_null": True},
            }
        },
    },
}

ROWS = {"customers": 300, "products": 40, "orders": 3000, "reviews": 800}


def _generate(document: dict, seed: int = 3, **kwargs) -> dict[str, pd.DataFrame]:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables,
        inputs.refs,
        base_rows=100,
        row_overrides=kwargs.pop("rows", ROWS),
        seed=seed,
        as_of=AS_OF,
        **kwargs,
    )


def _joined(
    frames: dict[str, pd.DataFrame], child: str, fk: str, column: str, parent: str, created: str
) -> pd.DataFrame:
    parent_frame = frames[parent][["id", created]].rename(
        columns={"id": "_key", created: "_parent"}
    )
    rows = frames[child][[fk, column]].dropna().rename(columns={fk: "_key"})
    rows["_key"] = rows["_key"].astype("int64")
    parent_frame["_key"] = parent_frame["_key"].astype("int64")
    joined = rows.merge(parent_frame, on="_key")
    joined["_mine"] = pd.to_datetime(joined[column])
    joined["_theirs"] = pd.to_datetime(joined["_parent"])
    return joined


# ---------------------------------------------------------------------------
# Which column a row comes into being with
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        ([("updated_at", "timestamp"), ("created_at", "timestamp")], "created_at"),
        ([("order_date", "timestamp"), ("shipped_at", "timestamp")], "order_date"),
        ([("birth_date", "date"), ("hire_date", "date")], "hire_date"),
        ([("updated_at", "timestamp"), ("deleted_at", "timestamp")], None),
        ([("name", "varchar")], None),
    ],
)
def test_the_creation_column_is_the_created_stage_else_the_first_unstaged_date(columns, expected):
    table = TableDef("t", [ColumnDef(name, kind) for name, kind in columns])
    found = creation_column(table)
    assert (found.name if found else None) == expected


def test_a_column_with_after_is_never_the_creation_column():
    table = TableDef(
        "t",
        [
            ColumnDef("event_date", "date", note={"after": "logged_at"}),
            ColumnDef("logged_at", "timestamp"),
        ],
    )
    found = creation_column(table)
    assert found is not None and found.name == "logged_at"


# ---------------------------------------------------------------------------
# The first day
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_no_order_is_placed_before_its_customer_signed_up(seed):
    frames = _generate(SHOP, seed=seed)
    joined = _joined(frames, "orders", "customer_id", "order_date", "customers", "created_at")
    assert len(joined) == len(frames["orders"])
    assert (joined["_mine"] >= joined["_theirs"]).all()
    shipped = frames["orders"].dropna(subset=["shipped_at"])
    assert (pd.to_datetime(shipped["shipped_at"]) >= pd.to_datetime(shipped["order_date"])).all()


def test_a_date_follows_every_parent_and_compares_with_a_timestamp_by_day():
    frames = _generate(SHOP)
    by_product = _joined(frames, "reviews", "product_id", "review_date", "products", "launched_on")
    assert (by_product["_mine"] >= by_product["_theirs"]).all()
    by_customer = _joined(
        frames, "reviews", "customer_id", "review_date", "customers", "created_at"
    )
    assert (by_customer["_mine"] >= by_customer["_theirs"].dt.normalize()).all()
    # A nullable foreign key left null constrains nothing, and stays null.
    assert frames["reviews"]["customer_id"].isna().any()


def test_the_orders_keep_their_own_shape():
    """Late rows take an older parent rather than moving their date: the order
    dates' seasonal peak and growth are what the column asks for."""
    frames = _generate(SHOP, rows={**ROWS, "orders": 20000})
    moments = pd.to_datetime(frames["orders"]["order_date"])
    gift = ((moments >= "2025-11-28") & (moments <= "2025-12-24")).sum() / 27
    may = ((moments >= "2026-05-01") & (moments <= "2026-05-31")).sum() / 31
    assert gift / may > 2.5
    hours = moments.dt.hour
    assert ((hours >= 8) & (hours < 18)).mean() > 0.7


def test_after_parent_false_draws_the_column_on_its_own():
    document = copy.deepcopy(SHOP)
    document["tables"]["orders"]["columns"]["order_date"]["generate"]["after_parent"] = False
    frames = _generate(document)
    joined = _joined(frames, "orders", "customer_id", "order_date", "customers", "created_at")
    assert (joined["_mine"] < joined["_theirs"]).any()
    assert "orders" not in parent_rules_for(*_engine(document))


def _engine(document: dict) -> tuple[dict, list]:
    inputs = to_engine(from_dict(document))
    return inputs.tables, inputs.refs


def test_a_one_to_one_child_keeps_its_parent_and_moves_its_date():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "created_at": {"type": "timestamp", "not_null": True},
                }
            },
            "profiles": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "user_id": {
                        "type": "int",
                        "not_null": True,
                        "references": {"to": "users.id", "one_to_one": True},
                    },
                    "created_at": {"type": "timestamp", "not_null": True},
                }
            },
        },
    }
    frames = _generate(document, rows={"users": 200, "profiles": 150})
    profiles = frames["profiles"]
    assert profiles["user_id"].is_unique
    joined = _joined(frames, "profiles", "user_id", "created_at", "users", "created_at")
    assert (joined["_mine"] >= joined["_theirs"]).all()
    assert (joined["_mine"] < MIDNIGHT + pd.Timedelta(seconds=1)).all()


def test_self_references_and_parents_without_a_date_are_not_followed():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "teams": {"columns": {"id": {"type": "int", "pk": True}, "name": "word"}},
            "employees": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "team_id": {"type": "int", "not_null": True, "references": "teams.id"},
                    "manager_id": {"type": "int", "references": "employees.id"},
                    "hired_on": {"type": "date", "not_null": True},
                }
            },
        },
    }
    assert parent_rules_for(*_engine(document)) == {}
    frames = _generate(document, rows={"teams": 5, "employees": 50})
    assert set(frames["employees"]["team_id"]) <= set(frames["teams"]["id"])


@pytest.mark.parametrize("kind", ["date", "timestamp"])
def test_a_parent_created_on_the_last_day_gives_a_child_on_it_not_past_it(kind):
    column = ColumnDef("created_at", kind, {"not null"})
    window = _Window(kind, column, AS_OF, TimeProfile(business_hours=True, growth=0.5))
    last = datetime(2026, 10, 7) if kind == "date" else datetime(2026, 10, 6, 23, 59, 59)
    for _ in range(50):
        moment = window.draw(last)
        assert last <= moment <= MIDNIGHT
    if kind == "timestamp":
        assert window.draw(MIDNIGHT) == MIDNIGHT


def test_a_moved_timestamp_keeps_business_hours():
    column = ColumnDef("created_at", "timestamp", {"not null"})
    window = _Window("timestamp", column, AS_OF, TimeProfile(business_hours=True))
    lower = datetime(2026, 3, 2, 6, 0, 0)
    moments = [window.draw(lower) for _ in range(2000)]
    assert all(lower <= m < MIDNIGHT for m in moments)
    assert sum(8 <= m.hour < 18 for m in moments) / len(moments) > 0.7


def test_the_rule_is_deterministic():
    first = _generate(SHOP, seed=11)
    second = _generate(SHOP, seed=11)
    for key, frame in first.items():
        pd.testing.assert_frame_equal(frame, second[key])


# ---------------------------------------------------------------------------
# The days after the first
# ---------------------------------------------------------------------------
def test_every_day_keeps_new_orders_after_their_customers():
    results = generate_days(
        from_dict(SHOP), 6, base_rows=100, row_overrides=ROWS, seed=4, as_of=AS_OF
    )
    for result in results:
        joined = _joined(
            result.state, "orders", "customer_id", "order_date", "customers", "created_at"
        )
        assert (joined["_mine"] >= joined["_theirs"]).all(), result.day
    last = results[-1].state
    assert last["orders"]["id"].tolist() == list(range(1, len(last["orders"]) + 1))


# ---------------------------------------------------------------------------
# Validation of the hint
# ---------------------------------------------------------------------------
def _issues(document: dict) -> list[str]:
    try:
        from_dict(document)
    except ModelError as error:
        return [str(issue) for issue in error.issues]
    return []


def test_after_parent_on_another_date_names_the_creation_column():
    document = copy.deepcopy(SHOP)
    document["tables"]["orders"]["columns"]["shipped_at"]["generate"]["after_parent"] = False
    (issue,) = _issues(document)
    assert "tables.orders.columns.shipped_at.generate.after_parent" in issue
    assert "which in orders is order_date" in issue


def test_after_parent_on_a_non_date_and_a_non_boolean_are_errors():
    document = copy.deepcopy(SHOP)
    document["tables"]["orders"]["columns"]["customer_id"]["generate"]["after_parent"] = False
    document["tables"]["orders"]["columns"]["order_date"]["generate"]["after_parent"] = "no"
    issues = _issues(document)
    assert any(
        "customer_id.generate.after_parent" in i and "date or timestamp" in i for i in issues
    )
    assert any("order_date.generate.after_parent" in i for i in issues)


def test_after_parent_needs_spec_0_5():
    document = copy.deepcopy(SHOP)
    document["model2data"] = "0.4.0"
    document["tables"]["orders"]["columns"]["order_date"]["generate"]["after_parent"] = False
    (issue,) = _issues(document)
    assert "write `model2data: 0.5.0`" in issue


def test_the_generator_checks_after_parent_on_bare_tables():
    tables = {
        "orders": TableDef(
            "orders",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("order_date", "date"),
                ColumnDef("total", "int", note={"after_parent": False}),
            ],
        )
    }
    with pytest.raises(ValueError, match="only applies to date/timestamp columns"):
        validate_hints(tables, [])
    tables["orders"].columns[2] = ColumnDef("shipped_on", "date", note={"after_parent": False})
    with pytest.raises(ValueError, match="which in orders is order_date"):
        validate_hints(tables, [])


# ---------------------------------------------------------------------------
# dbt
# ---------------------------------------------------------------------------
def test_dbt_gets_one_parent_test_per_followed_foreign_key(tmp_path):
    tables, refs = _engine(SHOP)
    tests = [t for t in hint_tests_for(from_dict(SHOP)) if t.hint == "after_parent"]
    assert [(t.table, t.column, t.arguments["foreign_key"]) for t in tests] == [
        ("orders", "order_date", "customer_id"),
        ("reviews", "review_date", "product_id"),
        ("reviews", "review_date", "customer_id"),
    ]
    by_key = {t.arguments["foreign_key"]: t for t in tests if t.table == "reviews"}
    assert "granularity" not in by_key["product_id"].arguments
    assert by_key["customer_id"].arguments["granularity"] == "day"
    assert tests[0].arguments == {
        "foreign_key": "customer_id",
        "to": "ref('stg_customers')",
        "field": "id",
        "parent_column": "created_at",
    }
    # Bare tables need their refs for it.
    assert not [t for t in hint_tests_for(tables) if t.hint == "after_parent"]

    generate_dbt_yml(tmp_path, tables, refs, hint_tests="warn")
    assert (tmp_path / "macros" / PARENT_MACROS_FILE).exists()
    assert (
        "model2data_not_before_parent" in (tmp_path / "models/staging/stg_orders.yml").read_text()
    )
    plain = tmp_path / "plain"
    generate_dbt_yml(plain, tables, refs, hint_tests="off")
    assert not (plain / "macros" / PARENT_MACROS_FILE).exists()


def test_the_defects_check_reads_the_parent_test_as_its_sql_does():
    tables, refs = _engine(SHOP)
    frames = _generate(SHOP)
    tests = [
        t
        for t in dbt_tests(tables, refs, hint_tests="warn")
        if t.type == "model2data_not_before_parent"
    ]
    assert tests and all(t.parent is not None for t in tests)
    seeded = {key: as_seeded(frame) for key, frame in frames.items()}
    assert not any(fails(t, seeded) for t in tests)

    broken = frames["orders"].copy()
    broken.loc[0, "order_date"] = "2000-01-01 00:00:00"
    seeded["orders"] = as_seeded(broken)
    orders_test = next(t for t in tests if t.table == "orders")
    assert fails(orders_test, seeded)


# ---------------------------------------------------------------------------
# Integer keys
# ---------------------------------------------------------------------------
def test_integer_primary_keys_number_the_rows_in_order():
    frames = _generate(SHOP)
    for key, frame in frames.items():
        assert frame["id"].tolist() == list(range(1, len(frame) + 1)), key
    assert set(frames["orders"]["customer_id"]) <= set(frames["customers"]["id"])


def test_a_drawn_or_borrowed_integer_key_is_listed_in_order():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "accounts": {
                "columns": {
                    "id": {"type": "int", "pk": True, "generate": {"min": 1000, "max": 9999}},
                }
            },
            "settings": {
                "columns": {
                    "account_id": {
                        "type": "int",
                        "pk": True,
                        "references": {"to": "accounts.id", "one_to_one": True},
                    },
                    "theme": "word",
                }
            },
            "tags": {
                "columns": {"code": {"type": "varchar", "pk": True}, "label": "word"},
            },
        },
    }
    frames = _generate(document, rows={"accounts": 60, "settings": 40, "tags": 20})
    accounts = frames["accounts"]["id"].tolist()
    assert accounts == sorted(accounts) and accounts[0] >= 1000 and len(set(accounts)) == 60
    settings = frames["settings"]["account_id"].tolist()
    assert settings == sorted(settings) and set(settings) <= set(accounts)


def test_later_days_continue_the_numbering():
    results = generate_days(
        from_dict(SHOP), 2, base_rows=100, row_overrides=ROWS, seed=4, as_of=AS_OF
    )
    day1 = results[1].tables["customers"].inserted
    assert day1["id"].tolist() == list(range(301, 309))


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
def test_an_empty_or_undated_child_is_left_alone():
    document = copy.deepcopy(SHOP)
    document["tables"]["reviews"]["columns"]["review_date"] = {
        "type": "date",
        "generate": {"null_rate": 1.0},
    }
    frames = _generate(document, rows={**ROWS, "orders": 0})
    assert frames["orders"].empty
    assert frames["reviews"]["review_date"].isna().all()


def test_a_date_filled_by_when_still_follows_its_parent():
    """`when` fills a creation date after the rule ran; the row then moves again."""
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "enums": {"kind": ["a", "b"]},
        "tables": {
            "customers": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "created_at": {"type": "timestamp", "not_null": True},
                }
            },
            "events": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "customer_id": {"type": "int", "not_null": True, "references": "customers.id"},
                    "kind": {"type": "kind", "not_null": True},
                    "created_at": {"type": "timestamp", "generate": {"when": {"kind": ["a"]}}},
                    "updated_at": {"type": "timestamp"},
                }
            },
        },
    }
    for seed in range(1, 6):
        frames = _generate(document, seed=seed, rows={"customers": 50, "events": 400})
        joined = _joined(frames, "events", "customer_id", "created_at", "customers", "created_at")
        assert (joined["_mine"] >= joined["_theirs"]).all()
        events = frames["events"].dropna(subset=["created_at", "updated_at"])
        assert (pd.to_datetime(events["updated_at"]) >= pd.to_datetime(events["created_at"])).all()


def test_the_parent_check_without_the_parent_or_rows_passes():
    tables, refs = _engine(SHOP)
    test = next(
        t
        for t in dbt_tests(tables, refs, hint_tests="warn")
        if t.type == "model2data_not_before_parent"
    )
    frames = _generate(SHOP)
    seeded = {key: as_seeded(frame) for key, frame in frames.items()}
    assert not fails(test, {"orders": seeded["orders"]})
    assert not fails(test, {**seeded, "orders": seeded["orders"].iloc[0:0]})


def test_dbml_with_an_after_parent_note_converts_to_0_5():
    from model2data.model import from_dbml

    model = from_dbml(
        "Table customers {\n  id int [pk]\n  created_at timestamp\n}\n"
        "Table orders {\n  id int [pk]\n  customer_id int [ref: > customers.id]\n"
        "  order_date timestamp [note: '{\"after_parent\": false}']\n}\n"
    )
    assert str(model.version) == "0.5.0"


def test_a_model_given_after_parent_is_written_as_0_5():
    from model2data.model import dump

    document = copy.deepcopy(SHOP)
    document["model2data"] = "0.4.0"
    model = from_dict(document)
    model.tables["orders"].columns["order_date"].generate["after_parent"] = False
    assert "model2data: 0.5.0" in dump(model)


def test_an_enum_named_like_a_date_is_not_a_creation_candidate():
    document = copy.deepcopy(SHOP)
    document["enums"] = {"date_bucket": ["early", "late"]}
    columns = document["tables"]["orders"]["columns"]
    document["tables"]["orders"]["columns"] = {
        "id": columns["id"],
        "bucket": "date_bucket",
        **{k: v for k, v in columns.items() if k != "id"},
    }
    columns = document["tables"]["orders"]["columns"]
    columns["order_date"]["generate"]["after_parent"] = False
    assert _issues(document) == []
