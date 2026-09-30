"""Spec 0.2.0's modelling and incremental vocabulary: read, written, checked.

`grain`, `incremental` and `measure: <aggregation>` on tables and columns, and
`transitions` among a column's hints. The behaviour built on them -- tests
from the model, the next generated day, the semantic layer -- has tests of its
own; these hold the document side: what reads, what round-trips through
`dump`, and what a conforming document may not say.
"""

import pytest

from model2data.model import Incremental, ModelError, dump, from_dict, load

DOCUMENT = {
    "model2data": "0.2.0",
    "enums": {"order_status": ["pending", "paid", "shipped", "cancelled"]},
    "tables": {
        "orders": {
            "role": "fact",
            "grain": ["id"],
            "incremental": {
                "new_per_day": 40,
                "update_rate": 0.05,
                "changes": ["status"],
                "updated_at": "updated_at",
            },
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "status": {
                    "type": "order_status",
                    "not_null": True,
                    "generate": {
                        "transitions": {"pending": ["paid", "cancelled"], "paid": ["shipped"]}
                    },
                },
                "total": {"type": "numeric", "measure": "average"},
                "quantity": {"type": "int", "measure": True},
                "customer_ref": {"type": "text", "measure": "count_distinct"},
                "updated_at": {"type": "timestamp"},
            },
        }
    },
}


def test_the_vocabulary_reads_into_the_model():
    orders = from_dict(DOCUMENT).tables["orders"]
    assert orders.grain == ["id"]
    assert orders.incremental == Incremental(
        new_per_day=40, update_rate=0.05, changes=["status"], updated_at="updated_at"
    )
    assert orders.columns["total"].measure == "average"
    assert orders.columns["quantity"].measure is True
    assert orders.columns["status"].generate["transitions"]["pending"] == ["paid", "cancelled"]


def test_it_round_trips_through_yaml():
    model = from_dict(DOCUMENT)
    text = dump(model)
    assert "grain: [id]" in text
    assert load(text) == model
    assert dump(load(text)) == text


def test_new_per_day_zero_is_kept():
    document = {
        "model2data": "0.2.0",
        "tables": {"t": {"incremental": {"new_per_day": 0}, "columns": {"id": "int"}}},
    }
    model = from_dict(document)
    assert model.tables["t"].incremental.new_per_day == 0
    assert load(dump(model)) == model


def _refused(change) -> str:
    document = {
        "model2data": "0.2.0",
        "enums": {"s": ["a", "b"]},
        "tables": {
            "t": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "state": {"type": "s"},
                    "note": {"type": "text"},
                    "at": {"type": "timestamp"},
                }
            }
        },
    }
    change(document["tables"]["t"])
    with pytest.raises(ModelError) as raised:
        from_dict(document)
    return str(raised.value)


@pytest.mark.parametrize(
    "change, path, words",
    [
        (lambda t: t.update(grain=["nope"]), "tables.t.grain.0", "not a column of t"),
        (
            lambda t: t.update(incremental={"changes": ["nope"]}),
            "tables.t.incremental.changes.0",
            "not a column of t",
        ),
        (
            lambda t: t.update(incremental={"updated_at": "note"}),
            "tables.t.incremental.updated_at",
            "not a date or timestamp column",
        ),
        (
            lambda t: t["columns"]["state"].update(generate={"transitions": {"a": ["z"]}}),
            "tables.t.columns.state.generate.transitions",
            "not a member of s",
        ),
        (
            lambda t: t["columns"]["note"].update(generate={"transitions": {"a": ["b"]}}),
            "tables.t.columns.note.generate.transitions",
            "",
        ),
        (
            lambda t: t["columns"]["note"].update(measure="sum"),
            "tables.t.columns.note.measure",
            "needs a numeric column",
        ),
        (
            lambda t: t.update(incremental={"update_rate": 1.5}),
            "tables.t.incremental.update_rate",
            "",
        ),
    ],
)
def test_what_a_conforming_document_may_not_say(change, path, words):
    message = _refused(change)
    assert path in message
    assert words in message


def test_count_measures_apply_to_any_column():
    document = {
        "model2data": "0.2.0",
        "tables": {"t": {"columns": {"code": {"type": "text", "measure": "count_distinct"}}}},
    }
    assert from_dict(document).tables["t"].columns["code"].measure == "count_distinct"
