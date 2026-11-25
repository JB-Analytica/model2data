"""A date following a parent the row also copies a value from.

`payments.amount` references `invoices.amount` beside `payments.invoice_id` references
`invoices.id`: the amount is the row's own invoice's, so `invoice_id` alone says which
invoice a payment's `after: invoices.issued_at` means, and an `after` naming a copied date
(`invoice_issued_at`) means the invoice's own.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pandas as pd
import pytest

from model2data.dbt.hint_tests import hint_tests_for
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.generate.hints import validate_hints
from model2data.model import from_dict, to_engine
from model2data.model.errors import ModelError

AS_OF = date(2026, 11, 25)

BILLING: dict[str, Any] = {
    "model2data": "0.9.0",
    "tables": {
        "invoices": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "number": {"type": "varchar", "unique": True, "not_null": True},
                "amount": {"type": "decimal(10,2)", "not_null": True},
                "issued_at": {"type": "timestamp", "not_null": True},
                "due_on": {"type": "date", "not_null": True, "generate": {"after": "issued_at"}},
            }
        },
        "payments": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "invoice_id": {"type": "int", "not_null": True, "references": "invoices.id"},
                "amount": {"type": "decimal(10,2)", "references": "invoices.amount"},
                "paid_at": {
                    "type": "timestamp",
                    "not_null": True,
                    "generate": {"after": "invoices.issued_at"},
                },
            }
        },
    },
}


def _billing(**payments: Any) -> dict[str, Any]:
    """BILLING with columns of payments replaced, added, or (None) taken out."""
    document = copy.deepcopy(BILLING)
    columns = document["tables"]["payments"]["columns"]
    for name, column in payments.items():
        columns.pop(name, None)
        if column is not None:
            columns[name] = column
    return document


def _copied(after: Any = "invoice_issued_at", **column: Any) -> dict[str, Any]:
    return _billing(
        invoice_issued_at={"type": "timestamp", "references": "invoices.issued_at"},
        paid_at={"type": "timestamp", "generate": {"after": after}, "not_null": True, **column},
    )


def _rows(document: dict[str, Any], seed: int = 3, **run: Any) -> dict[str, pd.DataFrame]:
    engine = to_engine(from_dict(document))
    return generate_data_from_dbml(
        engine.tables, engine.refs, base_rows=200, seed=seed, as_of=AS_OF, **run
    )


def _joined(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return data["payments"].merge(
        data["invoices"], left_on="invoice_id", right_on="id", suffixes=("", "_parent")
    )


def _issues(document: dict[str, Any]) -> list[str]:
    try:
        return [str(issue) for issue in from_dict(document).warnings]
    except ModelError as error:
        return [str(issue) for issue in error.issues]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_date_follows_the_parent_a_value_is_copied_from(seed):
    assert _issues(BILLING) == []
    joined = _joined(_rows(BILLING, seed))
    assert len(joined) == 200
    assert (joined["paid_at"] >= joined["issued_at"]).all()
    assert (joined["amount"] == joined["amount_parent"]).all()


def test_a_copy_of_a_unique_column_beside_the_primary_key_is_still_a_copy():
    document = _billing(invoice_number={"type": "varchar", "references": "invoices.number"})
    assert _issues(document) == []
    joined = _joined(_rows(document))
    assert (joined["paid_at"] >= joined["issued_at"]).all()
    assert (joined["invoice_number"] == joined["number"]).all()


def test_the_unique_column_is_the_key_when_nothing_references_the_primary_key():
    document = _billing(
        invoice_id=None, invoice_number={"type": "varchar", "references": "invoices.number"}
    )
    assert _issues(document) == []
    joined = _rows(document)["payments"].merge(
        _rows(document)["invoices"], left_on="invoice_number", right_on="number"
    )
    assert (joined["paid_at"] >= joined["issued_at"]).all()


def test_two_keys_onto_the_parent_are_still_refused_and_the_copy_is_not_named():
    document = _billing(credit_id={"type": "int", "references": "invoices.id"})
    (issue,) = _issues(document)
    assert "through more than one foreign key (invoice_id, credit_id)" in issue


def test_a_copy_through_a_foreign_key_of_several_columns_reaches_no_one_row():
    document = _billing(invoice_id={"type": "int"}, invoice_number={"type": "varchar"})
    document["tables"]["payments"]["foreign_keys"] = [
        {
            "columns": ["invoice_id", "invoice_number"],
            "references": "invoices",
            "to_columns": ["id", "number"],
        }
    ]
    assert any("only through a foreign key of several columns" in i for i in _issues(document))


def test_the_warning_is_raised_beside_a_copy_and_names_the_after():
    document = _billing(paid_at=None, taken_on={"type": "date", "not_null": True})
    (warning,) = _issues(document)
    assert "can fall before invoices.issued_at" in warning
    assert "through invoice_id" in warning


def test_no_warning_asks_a_copied_date_to_move():
    document = _billing(
        paid_at=None, invoice_issued_at={"type": "timestamp", "references": "invoices.issued_at"}
    )
    assert _issues(document) == []


def test_a_copied_date_takes_no_after_on_its_own_parent():
    document = _billing(
        invoice_due_on={
            "type": "date",
            "references": "invoices.due_on",
            "generate": {"after": "invoices.issued_at"},
        }
    )
    expected = "and invoice_due_on is itself copied from the invoices row"
    assert any(expected in issue for issue in _issues(document)), _issues(document)

    engine = to_engine(from_dict(_billing(invoice_due_on={"type": "date"})))
    column = next(c for c in engine.tables["payments"].columns if c.name == "invoice_due_on")
    column.note = {"after": "invoices.issued_at"}
    ref = {
        "source_table": "payments",
        "source_column": "invoice_due_on",
        "target_table": "invoices",
        "target_column": "due_on",
    }
    with pytest.raises(ValueError, match=expected):
        validate_hints(engine.tables, [*engine.refs, ref])


# ---------------------------------------------------------
# `after` naming the copied date itself
# ---------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("not_null", [True, False])
def test_an_after_on_a_copied_date_is_held(seed, not_null):
    document = _copied(not_null=not_null)
    assert _issues(document) == []
    joined = _joined(_rows(document, seed))
    assert (joined["invoice_issued_at"] == joined["issued_at"]).all()
    paid = joined[joined["paid_at"].notna()]
    assert len(paid) == 200 if not_null else 0 < len(paid) < 200
    assert (paid["paid_at"] >= paid["invoice_issued_at"]).all()


def test_a_copied_date_and_a_column_of_the_row_are_both_followed():
    document = _copied(["invoice_issued_at", "seen_at"])
    document["tables"]["payments"]["columns"]["seen_at"] = {"type": "timestamp", "not_null": True}
    joined = _joined(_rows(document))
    assert (joined["paid_at"] >= joined["issued_at"]).all()
    assert (joined["paid_at"] >= joined["seen_at"]).all()


def test_a_later_date_follows_the_one_after_the_copy():
    document = _copied()
    document["tables"]["payments"]["columns"]["settled_at"] = {
        "type": "timestamp",
        "not_null": True,
        "generate": {"after": "paid_at"},
    }
    joined = _joined(_rows(document))
    assert (joined["paid_at"] >= joined["issued_at"]).all()
    assert (joined["settled_at"] >= joined["paid_at"]).all()


def test_a_copied_date_compares_by_day_with_a_timestamp():
    document = _billing(
        invoice_due_on={"type": "date", "references": "invoices.due_on"},
        paid_at={"type": "timestamp", "not_null": True, "generate": {"after": "invoice_due_on"}},
    )
    joined = _joined(_rows(document))
    paid = pd.to_datetime(joined["paid_at"]).dt.date
    assert (paid >= joined["due_on"]).all()


def test_a_one_to_one_keeps_its_parent_and_moves_its_date():
    document = _copied()
    document["tables"]["payments"]["columns"]["invoice_id"]["references"] = {
        "to": "invoices.id",
        "one_to_one": True,
    }
    joined = _joined(_rows(document))
    assert joined["invoice_id"].is_unique
    assert (joined["paid_at"] >= joined["issued_at"]).all()


def test_a_copy_that_is_no_date_of_the_parent_is_left_as_it_was():
    """A date copied from a text column: there is no parent's date to follow."""
    document = _billing(
        noted_at={"type": "timestamp", "references": "invoices.number"},
        paid_at={"type": "timestamp", "not_null": True, "generate": {"after": "noted_at"}},
    )
    assert len(_rows(document)["payments"]) == 200


def test_a_copy_within_the_table_is_not_followed():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "staff": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "manager_id": {"type": "int", "references": "staff.id"},
                    "manager_hired_at": {"type": "timestamp", "references": "staff.hired_at"},
                    "hired_at": {"type": "timestamp", "generate": {"after": "manager_hired_at"}},
                }
            }
        },
    }
    assert len(_rows(document)["staff"]) == 200


def test_the_parent_drawn_after_the_table_is_refused_by_the_generator():
    """A foreign-key cycle broken at the key the copy is read through."""
    document = _copied()
    document["tables"]["payments"]["columns"]["invoice_id"].pop("not_null")
    document["tables"]["invoices"]["columns"]["first_payment_id"] = {
        "type": "int",
        "not_null": True,
        "references": "payments.id",
    }
    with pytest.raises(
        ValueError, match="names invoice_issued_at, a copy of invoices.issued_at, and invoices is"
    ):
        _rows(document)


def test_the_dbt_tests_are_the_ones_the_after_asks_for():
    def names(document):
        tests = hint_tests_for(from_dict(document))
        return [t.test for t in tests if t.table == "payments" and t.column == "paid_at"]

    assert names(BILLING) == ["model2data_not_before_parent"]
    assert names(_copied()) == ["model2data_not_before"]


def test_later_days_keep_a_payment_after_its_invoice():
    document = copy.deepcopy(BILLING)
    document["tables"]["invoices"]["incremental"] = {"new_per_day": 20}
    document["tables"]["payments"]["incremental"] = {"new_per_day": 20}
    results = generate_days(from_dict(document), 3, base_rows=60, seed=5, as_of=AS_OF)
    data = {key: table.state for key, table in results[-1].tables.items()}
    joined = _joined(data)
    assert len(joined) == 120
    assert (joined["paid_at"] >= joined["issued_at"]).all()
    assert (joined["amount"] == joined["amount_parent"]).all()
