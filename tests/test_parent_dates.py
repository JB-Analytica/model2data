"""Cross-table `after` (a child's date after its parent row's), its warning, and keys 1..N."""

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
from model2data.model import Suggestion, dump, from_dbml, from_dict, to_engine
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
                    "generate": {
                        "business_hours": True,
                        "growth": 0.5,
                        "seasonality": 0.6,
                        "after": "customers.created_at",
                    },
                },
                "shipped_at": {"type": "timestamp", "generate": {"after": "order_date"}},
            },
        },
        "reviews": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "product_id": {"type": "bigint", "not_null": True, "references": "products.id"},
                "customer_id": {"type": "bigint", "references": "customers.id"},
                "review_date": {
                    "type": "date",
                    "not_null": True,
                    "generate": {"after": ["products.launched_on", "customers.created_at"]},
                },
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


def _engine(document: dict) -> tuple[dict, list]:
    inputs = to_engine(from_dict(document))
    return inputs.tables, inputs.refs


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


def _without(document: dict, table: str, column: str) -> dict:
    plain = copy.deepcopy(document)
    plain["tables"][table]["columns"][column]["generate"].pop("after")
    return plain


def _issues(document: dict) -> list[str]:
    try:
        model = from_dict(document)
    except ModelError as error:
        return [str(issue) for issue in error.issues]
    return [str(issue) for issue in model.warnings]


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


def test_without_the_hint_orders_predate_their_customers():
    frames = _generate(_without(SHOP, "orders", "order_date"))
    joined = _joined(frames, "orders", "customer_id", "order_date", "customers", "created_at")
    assert (joined["_mine"] < joined["_theirs"]).mean() > 0.3


def test_a_list_follows_every_parent_and_compares_a_date_with_a_timestamp_by_day():
    frames = _generate(SHOP)
    by_product = _joined(frames, "reviews", "product_id", "review_date", "products", "launched_on")
    assert (by_product["_mine"] >= by_product["_theirs"]).all()
    by_customer = _joined(
        frames, "reviews", "customer_id", "review_date", "customers", "created_at"
    )
    assert (by_customer["_mine"] >= by_customer["_theirs"].dt.normalize()).all()
    # A nullable foreign key left null constrains nothing, and stays null.
    assert frames["reviews"]["customer_id"].isna().any()


def test_a_list_may_mix_a_column_of_the_row_with_a_parents():
    document = copy.deepcopy(SHOP)
    columns = document["tables"]["orders"]["columns"]
    columns["shipped_at"]["generate"]["after"] = ["order_date", "customers.created_at"]
    columns["order_date"]["generate"].pop("after")
    frames = _generate(document)
    orders = frames["orders"].dropna(subset=["shipped_at"])
    assert (pd.to_datetime(orders["shipped_at"]) >= pd.to_datetime(orders["order_date"])).all()
    joined = _joined(frames, "orders", "customer_id", "shipped_at", "customers", "created_at")
    assert (joined["_mine"] >= joined["_theirs"]).all()


def test_a_parent_after_keeps_the_column_in_its_stage_chain():
    """`created_at` with a parent's `after` still comes before the row's `updated_at`."""
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "accounts": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "created_at": {"type": "timestamp", "not_null": True},
                }
            },
            "users": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "account_id": {"type": "int", "not_null": True, "references": "accounts.id"},
                    "created_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {"after": "accounts.created_at"},
                    },
                    "updated_at": {"type": "timestamp", "not_null": True},
                }
            },
        },
    }
    frames = _generate(document, rows={"accounts": 30, "users": 500})
    users = frames["users"]
    assert (pd.to_datetime(users["updated_at"]) >= pd.to_datetime(users["created_at"])).all()
    joined = _joined(frames, "users", "account_id", "created_at", "accounts", "created_at")
    assert (joined["_mine"] >= joined["_theirs"]).all()


def test_the_orders_keep_their_own_shape():
    """Late rows take an older parent rather than moving their date: the order
    dates' seasonal peak and working hours are what the column asks for."""
    frames = _generate(SHOP, rows={**ROWS, "orders": 20000})
    moments = pd.to_datetime(frames["orders"]["order_date"])
    gift = ((moments >= "2025-11-28") & (moments <= "2025-12-24")).sum() / 27
    may = ((moments >= "2026-05-01") & (moments <= "2026-05-31")).sum() / 31
    assert gift / may > 2.5
    hours = moments.dt.hour
    assert ((hours >= 8) & (hours < 18)).mean() > 0.7


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
                    "created_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {"after": "users.created_at"},
                    },
                }
            },
        },
    }
    frames = _generate(document, rows={"users": 200, "profiles": 150})
    profiles = frames["profiles"]
    assert profiles["user_id"].is_unique
    joined = _joined(frames, "profiles", "user_id", "created_at", "users", "created_at")
    assert (joined["_mine"] >= joined["_theirs"]).all()
    assert (joined["_mine"] <= MIDNIGHT).all()


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


def test_the_hint_is_deterministic():
    first = _generate(SHOP, seed=11)
    second = _generate(SHOP, seed=11)
    for key, frame in first.items():
        pd.testing.assert_frame_equal(frame, second[key])


def test_an_empty_or_undated_child_is_left_alone():
    document = copy.deepcopy(SHOP)
    document["tables"]["reviews"]["columns"]["review_date"] = {
        "type": "date",
        "generate": {"null_rate": 1.0, "after": "products.launched_on"},
    }
    frames = _generate(document, rows={**ROWS, "orders": 0})
    assert frames["orders"].empty
    assert frames["reviews"]["review_date"].isna().all()


def test_a_date_filled_by_when_still_follows_its_parent():
    """`when` fills a date after the hint ran; the row then moves again."""
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
                    "created_at": {
                        "type": "timestamp",
                        "generate": {"when": {"kind": ["a"]}, "after": "customers.created_at"},
                    },
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
# Validation
# ---------------------------------------------------------------------------
def _with_after(after: Any, *, table: str = "orders", column: str = "order_date") -> dict:
    document = copy.deepcopy(SHOP)
    document["tables"][table]["columns"][column]["generate"]["after"] = after
    return document


@pytest.mark.parametrize(
    ("after", "message"),
    [
        ("shops.opened_at", 'names shops.opened_at, and there is no table "shops"'),
        (
            "customers.signed_up",
            'names customers.signed_up, and customers has no column "signed_up"',
        ),
        ("customers.email", "names customers.email, which is not a date or timestamp column"),
        (
            "products.launched_on",
            "names products.launched_on, and orders has no foreign key to products: `after` "
            "reaches another table only through a foreign key of the row",
        ),
        (
            "orders.shipped_at",
            "names orders.shipped_at, a column of orders itself: name a column of the same row "
            "without its table (`after: shipped_at`)",
        ),
        (["customers.created_at", "placed"], 'names "placed", which is not a column of orders'),
    ],
)
def test_a_wrong_after_says_what_is_wrong(after, message):
    issues = _issues(_with_after(after))
    assert any(message in issue for issue in issues), issues


def test_two_foreign_keys_to_the_parent_are_named():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "users": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "created_at": {"type": "timestamp", "not_null": True},
                }
            },
            "messages": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "sender_id": {"type": "int", "references": "users.id"},
                    "receiver_id": {"type": "int", "references": "users.id"},
                    "sent_at": {"type": "timestamp", "generate": {"after": "users.created_at"}},
                }
            },
        },
    }
    (issue,) = _issues(document)
    assert issue == (
        "tables.messages.columns.sent_at.generate.after: names users.created_at, and messages "
        "reaches users through more than one foreign key (sender_id, receiver_id): `after` "
        "cannot tell which users row it means"
    )


def test_a_reference_onto_no_key_or_a_composite_one_is_refused():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "batches": {
                "keys": [{"pk": ["site", "number"]}],
                "columns": {
                    "site": "int",
                    "number": "int",
                    "code": "varchar",
                    "made_at": {"type": "timestamp"},
                },
            },
            "items": {
                "foreign_keys": [
                    {
                        "columns": ["site", "number"],
                        "references": "batches",
                        "to_columns": ["site", "number"],
                    }
                ],
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "site": "int",
                    "number": "int",
                    "made_at": {"type": "timestamp", "generate": {"after": "batches.made_at"}},
                },
            },
            "labels": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "code": {"type": "varchar", "references": "batches.code"},
                    "printed_at": {"type": "timestamp", "generate": {"after": "batches.made_at"}},
                },
            },
        },
    }
    issues = _issues(document)
    assert any("only through a foreign key of several columns" in i for i in issues), issues
    assert any(
        "code references batches.code, which is not a key: `after` needs the one batches row" in i
        for i in issues
    ), issues


def test_tables_whose_afters_name_each_other_are_a_cycle():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "a": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "b_id": {"type": "int", "references": "b.id"},
                    "at": {"type": "timestamp", "generate": {"after": "b.at"}},
                }
            },
            "b": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "a_id": {"type": "int", "references": "a.id"},
                    "at": {"type": "timestamp", "generate": {"after": "a.at"}},
                }
            },
        },
    }
    issues = _issues(document)
    assert len([i for i in issues if "cannot each follow the other" in i]) == 2, issues


def test_a_same_table_cycle_through_a_list_is_found():
    document = _with_after(["customers.created_at", "shipped_at"])
    assert any(
        "form a cycle: order_date -> shipped_at -> order_date" in i for i in _issues(document)
    )


@pytest.mark.parametrize("after", ["customers.created_at", ["shipped_at"]])
def test_the_new_forms_need_spec_0_5(after):
    document = _with_after(after)
    document["model2data"] = "0.4.0"
    document["tables"]["reviews"]["columns"]["review_date"]["generate"].pop("after")
    issues = _issues(document)
    assert any("is spec 0.5.0" in i and "write `model2data: 0.5.0`" in i for i in issues), issues


def test_the_generator_checks_after_on_bare_tables():
    tables = {
        "customers": TableDef(
            "customers", [ColumnDef("id", "int", {"pk"}), ColumnDef("created_at", "timestamp")]
        ),
        "orders": TableDef(
            "orders",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("order_date", "date", note={"after": "customers.created_at"}),
            ],
        ),
    }
    with pytest.raises(ValueError, match="orders has no foreign key to customers"):
        validate_hints(tables, [])
    tables["orders"].columns[1] = ColumnDef("order_date", "date", note={"after": [3]})
    with pytest.raises(ValueError, match="must be a column name"):
        validate_hints(tables, [])


def test_a_parent_drawn_after_the_child_is_refused_at_generation():
    """A foreign-key cycle broken at the very key the `after` follows."""
    tables = {
        "users": TableDef(
            "users",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("post_id", "int"),
                ColumnDef("created_at", "timestamp", note={"after": "posts.posted_at"}),
            ],
        ),
        "posts": TableDef(
            "posts",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("user_id", "int", {"not null"}),
                ColumnDef("posted_at", "timestamp"),
            ],
        ),
    }
    refs = [
        {
            "source_table": "users",
            "source_column": "post_id",
            "target_table": "posts",
            "target_column": "id",
        },
        {
            "source_table": "posts",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        },
    ]
    with pytest.raises(ValueError, match="posts is generated after users"):
        generate_data_from_dbml(tables, refs, base_rows=5, seed=1, as_of=AS_OF)


# ---------------------------------------------------------------------------
# The warning
# ---------------------------------------------------------------------------
def test_a_child_without_the_hint_is_warned_with_the_fix():
    document = _without(SHOP, "orders", "order_date")
    model = from_dict(document)
    (warning,) = [w for w in model.warnings if "orders" in w.path]
    assert warning.severity == "warning"
    assert warning.path == "tables.orders.columns.order_date"
    assert warning.message == (
        "can fall before customers.created_at (the customers row it points at through "
        "customer_id); add `after: customers.created_at` to keep it after"
    )
    assert warning.suggestion == Suggestion(
        "tables.orders.columns.order_date.generate.after", "customers.created_at"
    )
    assert warning.suggestion.to_dict() == {
        "path": "tables.orders.columns.order_date.generate.after",
        "value": "customers.created_at",
        "spec": None,
    }


def test_the_fix_extends_an_after_and_names_the_spec_an_older_document_needs():
    document = _without(SHOP, "orders", "order_date")
    document["model2data"] = "0.4.0"
    document["tables"]["reviews"]["columns"]["review_date"]["generate"].pop("after")
    model = from_dict(document)
    suggestions = {w.path: w.suggestion for w in model.warnings}
    assert suggestions["tables.orders.columns.order_date"] == Suggestion(
        "tables.orders.columns.order_date.generate.after", "customers.created_at", "0.5.0"
    )
    # reviews has two parents with a date: a warning for each.
    assert len([w for w in model.warnings if w.path.startswith("tables.reviews")]) == 2

    listed = copy.deepcopy(SHOP)
    listed["tables"]["reviews"]["columns"]["review_date"]["generate"]["after"] = [
        "products.launched_on"
    ]
    (warning,) = from_dict(listed).warnings
    assert warning.suggestion is not None
    assert warning.suggestion.value == ("products.launched_on", "customers.created_at")
    assert warning.suggestion.to_dict()["value"] == ["products.launched_on", "customers.created_at"]


def test_no_warning_where_it_would_be_noise():
    document: dict[str, Any] = {
        "model2data": "0.5.0",
        "tables": {
            "teams": {"columns": {"id": {"type": "int", "pk": True}, "name": "word"}},
            "users": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "created_at": {"type": "timestamp"},
                }
            },
            "employees": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "team_id": {"type": "int", "references": "teams.id"},
                    "manager_id": {"type": "int", "references": "employees.id"},
                    "hired_on": {"type": "date"},
                }
            },
            "messages": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "sender_id": {"type": "int", "references": "users.id"},
                    "receiver_id": {"type": "int", "references": "users.id"},
                    "sent_at": {"type": "timestamp"},
                }
            },
        },
    }
    # A parent without a date, a self-reference, two foreign keys to one parent.
    assert from_dict(document).warnings == []
    assert from_dict(SHOP).warnings == []


def test_an_enum_named_like_a_date_is_not_what_the_warning_names():
    document = _without(SHOP, "orders", "order_date")
    document["enums"] = {"date_bucket": ["early", "late"]}
    columns = document["tables"]["orders"]["columns"]
    document["tables"]["orders"]["columns"] = {
        "id": columns["id"],
        "bucket": "date_bucket",
        **{k: v for k, v in columns.items() if k != "id"},
    }
    (warning,) = [w for w in from_dict(document).warnings if "orders" in w.path]
    assert warning.path == "tables.orders.columns.order_date"


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
def test_the_warning_reads_the_created_stage_else_the_first_unstaged_date(columns, expected):
    table = TableDef("t", [ColumnDef(name, kind) for name, kind in columns])
    found = creation_column(table)
    assert (found.name if found else None) == expected


# ---------------------------------------------------------------------------
# Conversion and writing
# ---------------------------------------------------------------------------
def test_dbml_with_a_parent_after_converts_to_0_5():
    model = from_dbml(
        "Table customers {\n  id int [pk]\n  created_at timestamp\n}\n"
        "Table orders {\n  id int [pk]\n  customer_id int [ref: > customers.id]\n"
        '  order_date timestamp [note: \'{"after": "customers.created_at"}\']\n}\n'
    )
    assert str(model.version) == "0.5.0"


def test_a_model_given_a_parent_after_is_written_as_0_5():
    document = _without(SHOP, "orders", "order_date")
    document["model2data"] = "0.4.0"
    document["tables"]["reviews"]["columns"]["review_date"]["generate"].pop("after")
    model = from_dict(document)
    model.tables["orders"].columns["order_date"].generate["after"] = "customers.created_at"
    assert "model2data: 0.5.0" in dump(model)


# ---------------------------------------------------------------------------
# dbt
# ---------------------------------------------------------------------------
def test_dbt_gets_one_test_per_parent_column(tmp_path):
    tables, refs = _engine(SHOP)
    tests = [t for t in hint_tests_for(from_dict(SHOP)) if t.test == "model2data_not_before_parent"]
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
    assert not [t for t in hint_tests_for(tables) if t.test == "model2data_not_before_parent"]

    generate_dbt_yml(tmp_path, tables, refs, hint_tests="warn")
    assert (tmp_path / "macros" / PARENT_MACROS_FILE).exists()
    assert (
        "model2data_not_before_parent" in (tmp_path / "models/staging/stg_orders.yml").read_text()
    )
    plain = tmp_path / "plain"
    generate_dbt_yml(plain, tables, refs, hint_tests="off")
    assert not (plain / "macros" / PARENT_MACROS_FILE).exists()


def test_a_list_writes_a_test_for_each_column_of_the_row_too():
    document = copy.deepcopy(SHOP)
    columns = document["tables"]["orders"]["columns"]
    columns["shipped_at"]["generate"]["after"] = ["order_date", "customers.created_at"]
    names = [(t.column, t.test) for t in hint_tests_for(from_dict(document)) if t.table == "orders"]
    assert ("shipped_at", "model2data_not_before") in names
    assert ("shipped_at", "model2data_not_before_parent") in names


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

    orders_test = next(t for t in tests if t.table == "orders")
    assert not fails(orders_test, {"orders": seeded["orders"]})
    assert not fails(orders_test, {**seeded, "orders": seeded["orders"].iloc[0:0]})

    broken = frames["orders"].copy()
    broken.loc[0, "order_date"] = "2000-01-01 00:00:00"
    seeded["orders"] = as_seeded(broken)
    assert fails(orders_test, seeded)


def test_parent_rules_name_the_followed_columns():
    rules = parent_rules_for(*_engine(SHOP))
    assert [(r.table, r.column) for found in rules.values() for r in found] == [
        ("orders", "order_date"),
        ("reviews", "review_date"),
    ]


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


def _bare(parent_columns: list[ColumnDef], refs: list[dict]) -> tuple[dict, list]:
    tables = {
        "users": TableDef("users", [ColumnDef("id", "int", {"pk"}), *parent_columns]),
        "messages": TableDef(
            "messages",
            [
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("sender_id", "int"),
                ColumnDef("receiver_id", "int"),
                ColumnDef("code", "varchar"),
                ColumnDef("sent_at", "timestamp"),
            ],
        ),
    }
    return tables, refs


def _ref(column: str, target: str = "id") -> dict:
    return {
        "source_table": "messages",
        "source_column": column,
        "target_table": "users",
        "target_column": target,
    }


@pytest.mark.parametrize(
    ("after", "parent_columns", "refs", "message"),
    [
        ("staff.created_at", [], [], "there is no table staff"),
        ("messages.code", [], [], "a column of messages itself"),
        ("users.joined", [], [_ref("sender_id")], "users has no column joined"),
        (
            "users.handle",
            [ColumnDef("handle", "varchar")],
            [_ref("sender_id")],
            "which is not a date or timestamp column",
        ),
        (
            "users.created_at",
            [ColumnDef("created_at", "timestamp")],
            [_ref("sender_id"), _ref("receiver_id")],
            r"more than one foreign key \(sender_id, receiver_id\)",
        ),
        (
            "users.created_at",
            [ColumnDef("created_at", "timestamp"), ColumnDef("handle", "varchar")],
            [_ref("code", "handle")],
            "code references users.handle, which is not a key",
        ),
    ],
)
def test_the_generator_names_what_is_wrong_with_a_parent_after(
    after, parent_columns, refs, message
):
    tables, refs = _bare(parent_columns, refs)
    tables["messages"].columns[4] = ColumnDef("sent_at", "timestamp", note={"after": after})
    with pytest.raises(ValueError, match=message):
        validate_hints(tables, refs)


def test_an_after_that_is_no_name_is_left_to_the_schema():
    issues = _issues(_with_after(["customers.created_at", 3]))
    assert any("generate.after" in issue for issue in issues), issues


def test_the_parent_check_passes_rows_pointing_at_no_parent():
    tables, refs = _engine(SHOP)
    test = next(
        t
        for t in dbt_tests(tables, refs, hint_tests="warn")
        if t.type == "model2data_not_before_parent"
    )
    frames = _generate(SHOP)
    seeded = {key: as_seeded(frame) for key, frame in frames.items()}
    customers = seeded["customers"].copy()
    customers["id"] = [str(10_000 + n) for n in range(len(customers))]
    assert not fails(test, {**seeded, "customers": customers})
