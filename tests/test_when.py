"""`when`: a column set only on the rows another column allows (completed_at when status is done)."""

from __future__ import annotations

import copy
import json
import random
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from test_model import _doc, _issues
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.hint_tests import HintTest, hint_tests_for, write_hint_macros
from model2data.dbt.tests import DbtTest
from model2data.defects.checks import as_seeded, fails
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.generate.hints import validate_hints
from model2data.generate.when import apply_when, own_stream, update_when
from model2data.model import dump, from_dbml, from_dict, load, to_engine
from model2data.parse.dbml import ColumnDef, TableDef

runner = CliRunner()
AS_OF = date(2026, 3, 1)
MIDNIGHT = datetime(2026, 3, 1)

TASKS: dict[str, Any] = {
    "model2data": "0.4.0",
    "enums": {"task_status": ["todo", "doing", "done"]},
    "tables": {
        "tasks": {
            "incremental": {"new_per_day": 15, "update_rate": 0.4, "updated_at": "updated_at"},
            "columns": {
                "id": {"type": "int", "pk": True},
                "status": {
                    "type": "task_status",
                    "not_null": True,
                    "generate": {
                        "transitions": {
                            "todo": ["doing"],
                            "doing": ["done", "todo"],
                            "done": ["doing"],
                        }
                    },
                },
                "created_at": {"type": "timestamp", "not_null": True},
                "updated_at": {"type": "timestamp", "not_null": True},
                "completed_at": {
                    "type": "timestamp",
                    "generate": {"after": "created_at", "when": {"status": ["done"]}},
                },
                "title": "varchar",
            },
        }
    },
}

SUBSCRIPTIONS: dict[str, Any] = {
    "model2data": "0.4.0",
    "enums": {"sub_status": ["trial", "active", "cancelled"]},
    "tables": {
        "subscriptions": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "status": "sub_status",
                "started_on": {"type": "date", "not_null": True},
                "end_date": {
                    "type": "date",
                    "generate": {
                        "after": "started_on",
                        "null_rate": 0.25,
                        "when": {"status": ["cancelled"]},
                    },
                },
            }
        }
    },
}

ORDERS: dict[str, Any] = {
    "model2data": "0.4.0",
    "enums": {"order_status": ["pending", "paid", "shipped", "delivered", "cancelled"]},
    "tables": {
        "customers": {"columns": {"id": {"type": "bigint", "pk": True}, "name": "first_name"}},
        "orders": {
            "incremental": {"new_per_day": 25, "update_rate": 0.3, "updated_at": "updated_at"},
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "customer_id": {"type": "bigint", "not_null": True, "references": "customers.id"},
                "order_date": {"type": "timestamp", "not_null": True},
                "shipped_at": {
                    "type": "timestamp",
                    "generate": {
                        "after": "order_date",
                        "when": {"status": ["shipped", "delivered"]},
                    },
                },
                "total": {"type": "numeric", "generate": {"min": 5, "max": 90}},
                "status": {
                    "type": "order_status",
                    "not_null": True,
                    "generate": {
                        "weights": {"delivered": 20, "cancelled": 1},
                        "transitions": {
                            "pending": ["paid", "cancelled"],
                            "paid": ["shipped", "cancelled"],
                            "shipped": ["delivered"],
                        },
                    },
                },
                "updated_at": {
                    "type": "timestamp",
                    "not_null": True,
                    "generate": {"after": "order_date"},
                },
            },
        },
        "order_items": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "order_id": {"type": "bigint", "not_null": True, "references": "orders.id"},
                "qty": {"type": "int", "generate": {"min": 1, "max": 5}},
            }
        },
    },
}


def _without_when(document: dict[str, Any]) -> dict[str, Any]:
    """The same document with every `when` taken out."""
    plain = copy.deepcopy(document)
    for table in plain["tables"].values():
        for column in table["columns"].values():
            if isinstance(column, dict):
                column.get("generate", {}).pop("when", None)
    return plain


def _days(document: dict, days: int, rows: int = 120, seed: int = 5):
    return generate_days(from_dict(document), days, base_rows=rows, seed=seed, as_of=AS_OF)


def _assert_when(frame: pd.DataFrame, column: str, condition: pd.Series, *, nulls_allowed=False):
    """`column` is null on every row outside `condition`, and on none inside it (unless allowed)."""
    assert frame.loc[~condition, column].isna().all()
    if not nulls_allowed:
        assert frame.loc[condition, column].notna().all()


# ---------------------------------------------------------------------------
# Generation, the first day
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_completed_at_is_set_exactly_on_done_tasks(seed):
    frame = _days(TASKS, 0, rows=200, seed=seed)[0].tables["tasks"].state
    done = frame["status"] == "done"
    assert 0 < done.sum() < len(frame)
    _assert_when(frame, "completed_at", done)
    completed = frame.loc[done]
    assert (
        pd.to_datetime(completed["completed_at"]) >= pd.to_datetime(completed["created_at"])
    ).all()
    assert (pd.to_datetime(completed["completed_at"]) <= pd.Timestamp(MIDNIGHT)).all()


def test_null_rate_counts_only_the_matching_rows():
    frame = _days(SUBSCRIPTIONS, 0, rows=400)[0].tables["subscriptions"].state
    cancelled = frame["status"] == "cancelled"
    _assert_when(frame, "end_date", cancelled, nulls_allowed=True)
    # Exactly floor(m * null_rate) of the m cancelled rows are null.
    assert frame.loc[cancelled, "end_date"].isna().sum() == int(cancelled.sum() * 0.25)
    ended = frame.loc[cancelled & frame["end_date"].notna()]
    assert (ended["end_date"] >= ended["started_on"]).all()
    assert all(isinstance(value, date) for value in ended["end_date"])


def test_a_null_condition_never_matches():
    # `status` is nullable here, so some subscriptions have none: no end_date there either.
    frame = _days(SUBSCRIPTIONS, 0, rows=400)[0].tables["subscriptions"].state
    assert frame["status"].isna().any()
    assert frame.loc[frame["status"].isna(), "end_date"].isna().all()


def test_shipped_at_follows_order_date_on_shipped_and_delivered_orders():
    frame = _days(ORDERS, 0, rows=300)[0].tables["orders"].state
    shipped = frame["status"].isin(["shipped", "delivered"])
    _assert_when(frame, "shipped_at", shipped)
    rows = frame.loc[shipped]
    assert (pd.to_datetime(rows["shipped_at"]) >= pd.to_datetime(rows["order_date"])).all()


def test_several_columns_must_all_match_and_any_type_of_column_can_carry_when():
    document = {
        "model2data": "0.4.0",
        "enums": {"level": ["1", "2", "3"]},
        "tables": {
            "tickets": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "escalated": {"type": "boolean", "not_null": True},
                    "priority": {"type": "int", "not_null": True, "generate": {"max": 3}},
                    "tier": {"type": "level", "not_null": True},
                    "queue": {"type": "varchar", "not_null": True, "generate": {"distinct": 3}},
                    "escalation_note": {
                        "type": "text",
                        "generate": {"when": {"escalated": [True], "priority": [2, 3]}},
                    },
                    "tier_bonus": {
                        "type": "numeric",
                        "generate": {"min": 1, "max": 9, "when": {"tier": [3]}},
                    },
                    "queue_owner": {
                        "type": "varchar",
                        "generate": {"distinct": 2, "when": {"queue": ["nobody-queue"]}},
                    },
                },
            }
        },
    }
    frame = _days(document, 0, rows=300)[0].tables["tickets"].state
    escalated = (frame["escalated"] == True) & frame["priority"].isin([2, 3])  # noqa: E712
    _assert_when(frame, "escalation_note", escalated)
    _assert_when(frame, "tier_bonus", frame["tier"] == "3")
    assert frame["tier_bonus"].dropna().between(1, 9).all()
    # No queue holds the value listed: the column is null on every row.
    assert frame["queue_owner"].isna().all()


def test_a_filled_value_is_one_the_column_holds():
    """A non-temporal column filled where the null pass emptied a matching row keeps its shape."""
    document: dict[str, Any] = copy.deepcopy(TASKS)
    document["tables"]["tasks"]["columns"]["owner"] = {
        "type": "varchar",
        "generate": {"distinct": 3, "when": {"status": ["doing", "done"]}},
    }
    frame = _days(document, 0, rows=300)[0].tables["tasks"].state
    _assert_when(frame, "owner", frame["status"].isin(["doing", "done"]))
    assert frame["owner"].dropna().nunique() <= 3


def test_a_filled_timestamp_stays_before_what_follows_it():
    document: dict[str, Any] = copy.deepcopy(TASKS)
    columns = document["tables"]["tasks"]["columns"]
    columns["archived_at"] = {"type": "timestamp", "generate": {"after": "completed_at"}}
    frame = _days(document, 0, rows=300)[0].tables["tasks"].state
    both = frame[["completed_at", "archived_at"]].dropna()
    assert len(both)
    assert (pd.to_datetime(both["archived_at"]) >= pd.to_datetime(both["completed_at"])).all()


def test_a_column_with_no_dependency_is_filled_from_its_own_window():
    document: dict[str, Any] = copy.deepcopy(TASKS)
    document["tables"]["tasks"]["columns"]["reviewed_on"] = {
        "type": "date",
        "generate": {"when": {"status": ["done"]}},
    }
    frame = _days(document, 0, rows=300)[0].tables["tasks"].state
    _assert_when(frame, "reviewed_on", frame["status"] == "done")


def test_the_dbml_form_reads_when_from_the_note():
    model = from_dbml(
        """
        Enum task_status {
          todo
          doing
          done
        }
        Table tasks {
          id int [pk]
          status task_status [not null]
          created_at timestamp [not null]
          completed_at timestamp [note: '{"after": "created_at", "when": {"status": ["done"]}}']
        }
        """
    )
    assert model.tables["tasks"].columns["completed_at"].generate == {
        "after": "created_at",
        "when": {"status": ["done"]},
    }
    inputs = to_engine(model)
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, 200, seed=3, as_of=AS_OF)
    frame = frames["tasks"]
    _assert_when(frame, "completed_at", frame["status"] == "done")


# ---------------------------------------------------------------------------
# Days after the first
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "document, table, column, values",
    [
        (TASKS, "tasks", "completed_at", ["done"]),
        (ORDERS, "orders", "shipped_at", ["shipped", "delivered"]),
    ],
)
def test_every_day_keeps_the_column_in_step_with_its_status(document, table, column, values):
    days = _days(document, 5)
    for day in days:
        state = day.tables[table].state
        _assert_when(state, column, state["status"].isin(values))
        for part in (day.tables[table].inserted, day.tables[table].updated):
            _assert_when(part, column, part["status"].isin(values))


def test_a_row_moving_into_a_status_gets_its_value_that_day():
    days = _days(TASKS, 5)
    entered = left = 0
    for before, day in zip(days, days[1:], strict=False):
        table = day.tables["tasks"]
        old = before.tables["tasks"].state
        for position, (_, row) in zip(
            table.updated_positions, table.updated.iterrows(), strict=True
        ):
            was = old.iloc[position]
            if row["status"] == "done" and was["status"] != "done":
                entered += 1
                # Completed by the update that made it done: on the day, at its updated_at.
                assert row["completed_at"] == row["updated_at"]
                assert row["completed_at"].startswith(day.date.isoformat())
            elif was["status"] == "done" and row["status"] != "done":
                left += 1
                assert pd.isna(row["completed_at"])
            elif was["status"] == "done":
                assert row["completed_at"] == was["completed_at"]
    assert entered and left


def test_inserted_rows_that_match_get_a_value_on_the_day():
    # Every member is an initial state here (a cycle), so new rows can start done.
    document: dict[str, Any] = copy.deepcopy(TASKS)
    status = document["tables"]["tasks"]["columns"]["status"]["generate"]
    status["transitions"] = {"todo": ["doing", "done"], "doing": ["done"], "done": ["todo"]}
    days = _days(document, 3)
    for day in days[1:]:
        inserted = day.tables["tasks"].inserted
        done = inserted["status"] == "done"
        assert done.any()
        _assert_when(inserted, "completed_at", done)
        stamps = pd.to_datetime(inserted.loc[done, "completed_at"])
        assert (stamps.dt.date == day.date).all()
        assert (stamps >= pd.to_datetime(inserted.loc[done, "created_at"])).all()


def test_a_redrawn_column_and_a_date_column_on_later_days():
    document: dict[str, Any] = copy.deepcopy(TASKS)
    table = document["tables"]["tasks"]
    table["incremental"]["changes"] = ["status", "completed_at", "closed_on", "outcome"]
    table["columns"]["closed_on"] = {"type": "date", "generate": {"when": {"status": ["done"]}}}
    table["columns"]["outcome"] = {
        "type": "varchar",
        "generate": {"null_rate": 0.5, "when": {"status": ["done"]}},
    }
    days = _days(document, 4)
    for day in days:
        state = day.tables["tasks"].state
        done = state["status"] == "done"
        _assert_when(state, "completed_at", done)
        _assert_when(state, "closed_on", done)
        assert state.loc[~done, "outcome"].isna().all()
    for day in days[1:]:
        updated = day.tables["tasks"].updated
        moved = updated["closed_on"].notna() & (updated["closed_on"] != None)  # noqa: E711
        assert all(
            value == day.date or value < day.date for value in updated.loc[moved, "closed_on"]
        )


def test_without_updated_at_a_row_moving_in_gets_a_time_of_the_day():
    document: dict[str, Any] = copy.deepcopy(TASKS)
    table = document["tables"]["tasks"]
    del table["incremental"]["updated_at"]
    days = _days(document, 3)
    for before, day in zip(days, days[1:], strict=False):
        old = before.tables["tasks"].state
        table_day = day.tables["tasks"]
        for position, (_, row) in zip(
            table_day.updated_positions, table_day.updated.iterrows(), strict=True
        ):
            if row["status"] == "done" and old.iloc[position]["status"] != "done":
                assert row["completed_at"].startswith(day.date.isoformat())


# ---------------------------------------------------------------------------
# Determinism: `when` draws nothing from the shared streams
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "document", [TASKS, SUBSCRIPTIONS, ORDERS], ids=["tasks", "subs", "orders"]
)
def test_every_other_column_is_what_the_model_without_when_generates(document):
    with_when = _days(document, 4)
    without = _days(_without_when(document), 4)
    when_columns = {
        (key, name)
        for key, table in document["tables"].items()
        for name, column in table["columns"].items()
        if isinstance(column, dict) and "when" in column.get("generate", {})
    }
    for a, b in zip(with_when, without, strict=True):
        for key in a.tables:
            dropped = [name for table, name in when_columns if table == key]
            for part in ("state", "inserted", "updated"):
                left = getattr(a.tables[key], part).drop(columns=dropped)
                right = getattr(b.tables[key], part).drop(columns=dropped)
                pd.testing.assert_frame_equal(left, right)
            assert a.tables[key].updated_positions == b.tables[key].updated_positions


def test_the_same_seed_gives_the_same_when_columns():
    first = _days(ORDERS, 3)
    second = _days(ORDERS, 3)
    for a, b in zip(first, second, strict=True):
        pd.testing.assert_frame_equal(a.tables["orders"].state, b.tables["orders"].state)


def test_without_a_seed_the_column_still_follows_its_status():
    days = generate_days(from_dict(TASKS), 2, base_rows=80, as_of=AS_OF)
    for day in days:
        state = day.tables["tasks"].state
        _assert_when(state, "completed_at", state["status"] == "done")


def test_own_stream_puts_the_shared_streams_back():
    import faker.generator

    random.seed(11)
    faker.generator.random.seed(12)
    expected = (random.random(), faker.generator.random.random())
    random.seed(11)
    faker.generator.random.seed(12)
    with own_stream(99):
        random.random()
        faker.generator.random.random()
    assert (random.random(), faker.generator.random.random()) == expected


# ---------------------------------------------------------------------------
# The helpers, on hand-built frames
# ---------------------------------------------------------------------------
STATUS = ColumnDef(name="status", data_type="st", settings=set(), enum_values=["a", "b"])


def _table(*columns: ColumnDef) -> TableDef:
    return TableDef(name="t", columns=[STATUS, *columns])


def test_apply_when_draws_fresh_values_when_the_column_holds_none():
    label = ColumnDef(
        name="label", data_type="int", settings=set(), note={"when": {"status": ["a"]}}
    )
    frame = pd.DataFrame({"status": ["a", "a", "b"], "label": [None, None, None]})
    apply_when(frame, _table(label), cap=MIDNIGHT, seed_for=lambda _: 1)
    assert pd.isna(frame["label"].tolist()[2])
    # Integers, drawn fresh (the frame holds them as floats beside a null, as core's do).
    assert all(float(value).is_integer() for value in frame["label"].tolist()[:2])


def test_apply_when_without_a_held_moment_takes_the_latest_it_may():
    stamp = ColumnDef(
        name="seen_at", data_type="timestamp", settings=set(), note={"when": {"status": ["a"]}}
    )
    frame = pd.DataFrame({"status": ["a", "b"], "seen_at": [None, None]})
    apply_when(frame, _table(stamp), cap=MIDNIGHT, seed_for=lambda _: 1)
    assert frame["seen_at"].tolist() == ["2026-03-01 00:00:00", None]


def test_apply_when_on_a_later_day_fills_within_the_day():
    stamp = ColumnDef(
        name="seen_at", data_type="timestamp", settings=set(), note={"when": {"status": ["a"]}}
    )
    frame = pd.DataFrame({"status": ["a"] * 20, "seen_at": [None] * 20})
    start = datetime(2026, 3, 4)
    apply_when(
        frame,
        _table(stamp),
        cap=datetime(2026, 3, 4, 23, 59, 59),
        floor=start,
        seed_for=lambda _: 1,
    )
    assert all(value.startswith("2026-03-04 ") for value in frame["seen_at"])


def test_update_when_without_held_values_draws_fresh_ones():
    label = ColumnDef(
        name="label", data_type="int", settings=set(), note={"when": {"status": ["a"]}}
    )
    table = _table(label)
    before = pd.DataFrame({"status": ["b", "b"], "label": [None, None]}, index=pd.Index([4, 7]))
    changes = pd.DataFrame({"status": ["a", "b"]}, index=pd.Index([4, 7]))
    update_when(
        table,
        before,
        changes,
        day_start=datetime(2026, 3, 4),
        updated_at=None,
        seed_for=lambda _: 1,
        held=lambda _: [],
    )
    assert isinstance(changes.loc[4, "label"], int)
    assert changes.loc[7, "label"] is None


def test_update_when_never_sets_a_value_before_what_it_follows():
    seen = ColumnDef(
        name="seen_at",
        data_type="timestamp",
        settings=set(),
        note={"after": "start_at", "when": {"status": ["a"]}},
    )
    start = ColumnDef(name="start_at", data_type="timestamp", settings=set())
    before = pd.DataFrame(
        {"status": ["b"], "start_at": ["2026-03-10 08:00:00"], "seen_at": [None]},
        index=pd.Index([2]),
    )
    changes = pd.DataFrame({"status": ["a"]}, index=pd.Index([2]))
    update_when(
        _table(start, seen),
        before,
        changes,
        day_start=datetime(2026, 3, 4),
        updated_at=None,
        seed_for=lambda _: 1,
        held=lambda _: [],
    )
    assert changes.loc[2, "seen_at"] == "2026-03-10 08:00:00"


def test_update_when_leaves_a_table_alone_when_nothing_it_reads_changes():
    label = ColumnDef(
        name="label", data_type="int", settings=set(), note={"when": {"status": ["a"]}}
    )
    before = pd.DataFrame({"status": ["a"], "label": [3], "other": [1]})
    changes = pd.DataFrame({"other": [2]})
    update_when(
        _table(label, ColumnDef(name="other", data_type="int", settings=set())),
        before,
        changes,
        day_start=datetime(2026, 3, 4),
        updated_at=None,
        seed_for=lambda _: None,
        held=lambda _: [],
    )
    assert list(changes.columns) == ["other"]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
ENUM = {"st": ["todo", "done"]}
BASE = {
    "id": {"type": "int", "pk": True},
    "status": "st",
    "created_at": "timestamp",
    "priority": "int",
    "score": "numeric",
    "flag": "boolean",
    "code": "varchar",
    "parent_id": {"type": "int", "references": "t.id"},
}


def _when_doc(when, **column) -> dict:
    return _doc(
        {
            "t": {
                "columns": {
                    **BASE,
                    "x": {"type": "timestamp", "generate": {"when": when}, **column},
                }
            }
        },
        enums=ENUM,
        model2data="0.4.0",
    )


def test_a_valid_when_has_no_issue():
    when = {"status": ["done"], "priority": [1, 2.0], "score": [1.5], "flag": [True], "code": ["A"]}
    assert _issues(_when_doc(when)) == []


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            _when_doc({"stat": ["done"]}),
            'tables.t.columns.x.generate.when.stat: names "stat", which is not a column of t '
            "(did you mean status?)",
            id="did-you-mean",
        ),
        pytest.param(
            _when_doc({"zzz": ["done"]}),
            'tables.t.columns.x.generate.when.zzz: names "zzz", which is not a column of t',
            id="unknown-column",
        ),
        pytest.param(
            _when_doc({"x": ["done"]}),
            "tables.t.columns.x.generate.when.x: names the column itself: `when` names another "
            "column",
            id="itself",
        ),
        pytest.param(
            _when_doc({"status": ["finished"]}),
            'tables.t.columns.x.generate.when.status: lists "finished", which is not a member of '
            "st (members: todo, done)",
            id="not-a-member",
        ),
        pytest.param(
            _when_doc({"flag": ["yes"]}),
            'tables.t.columns.x.generate.when.flag: lists "yes", and flag (boolean) holds true or '
            "false",
            id="boolean",
        ),
        pytest.param(
            _when_doc({"priority": [1.5]}),
            "tables.t.columns.x.generate.when.priority: lists 1.5, and priority (int) holds a "
            "whole number",
            id="integer",
        ),
        pytest.param(
            _when_doc({"priority": ["high"]}),
            'tables.t.columns.x.generate.when.priority: lists "high", and priority (int) holds a '
            "whole number",
            id="integer-text",
        ),
        pytest.param(
            _when_doc({"score": ["high"]}),
            'tables.t.columns.x.generate.when.score: lists "high", and score (numeric) holds a '
            "number",
            id="number",
        ),
        pytest.param(
            _when_doc({"code": [7]}),
            "tables.t.columns.x.generate.when.code: lists 7, and code (varchar) holds text: write "
            'it as "7"',
            id="text",
        ),
        pytest.param(
            _when_doc({"created_at": ["2026-01-01"]}),
            "tables.t.columns.x.generate.when.created_at: names created_at, a date or timestamp "
            "column: `when` matches listed values, so name an enum, boolean, number or text column",
            id="temporal",
        ),
        pytest.param(
            _when_doc({"parent_id": [1]}),
            "tables.t.columns.x.generate.when.parent_id: names parent_id, a foreign key: its "
            "values are the parent's keys, drawn as the parent's rows come out, not values a "
            "model can list",
            id="foreign-key-condition",
        ),
        pytest.param(
            _when_doc({"status": ["done"]}, not_null=True),
            "tables.t.columns.x.generate.when: needs a nullable column, and this one is not_null: "
            "`when` leaves the rows it does not match null",
            id="not-null",
        ),
        pytest.param(
            _when_doc({"status": ["done"]}, unique=True),
            "tables.t.columns.x.generate.when: cannot sit on a column that is unique or in a key: "
            "a key's values are drawn distinct row by row, and `when` nulls some rows and fills "
            "others",
            id="unique",
        ),
        pytest.param(
            _when_doc({"status": ["done"]}, default="2026-01-01 00:00:00"),
            "tables.t.columns.x.generate.when: leaves the rows it does not match null, and a "
            "column with a `default` holds the default instead of null: drop the default, or "
            "the `when`",
            id="default",
        ),
        pytest.param(
            _when_doc({"status": ["done"]}, references="t.id", type="int"),
            "tables.t.columns.x.generate.when: cannot sit on a foreign key: its values are drawn "
            "from the parent's rows, and a row's parent does not depend on another column of it",
            id="foreign-key",
        ),
        pytest.param(
            _when_doc({"status": []}),
            "tables.t.columns.x.generate.when.status: must not be empty",
            id="empty-list",
        ),
    ],
)
def test_a_when_issue_is_reported_in_words(document, expected):
    assert expected in _issues(document)


def test_when_cannot_name_a_column_with_a_when_of_its_own():
    document = _when_doc({"code": ["A"]})
    document["tables"]["t"]["columns"]["code"] = {
        "type": "varchar",
        "generate": {"when": {"status": ["done"]}},
    }
    assert (
        "tables.t.columns.x.generate.when.code: names code, which has a `when` of its own: a "
        "condition names a column generated without one"
    ) in _issues(document)


def test_when_cannot_sit_on_the_updated_at_column():
    document = _when_doc({"status": ["done"]})
    document["tables"]["t"]["incremental"] = {"update_rate": 0.1, "updated_at": "x"}
    assert (
        "tables.t.columns.x.generate.when: cannot sit on x, the incremental.updated_at of t: it "
        "is set on every row a day inserts or updates"
    ) in _issues(document)


def test_an_integer_enum_member_matches_its_text():
    document = _doc(
        {
            "t": {
                "columns": {
                    "id": {"type": "int", "pk": True},
                    "level": {"type": "lvl", "not_null": True},
                    "x": {"type": "varchar", "generate": {"when": {"level": [2]}}},
                }
            }
        },
        enums={"lvl": [1, 2, 3]},
        model2data="0.4.0",
    )
    assert _issues(document) == []
    frame = _days(document, 0, rows=200)[0].tables["t"].state
    _assert_when(frame, "x", frame["level"] == "2")
    (test,) = [t for t in hint_tests_for(from_dict(document)) if t.hint == "when"]
    assert test.arguments == {"conditions": {"level": ["2"]}}


@pytest.mark.parametrize(
    ("note", "message"),
    [
        ({"when": {"status": ["a"]}}, None),
        ({"when": {"nope": ["a"]}}, "which is not another column of t"),
        ({"when": {"status": "a"}}, "must list the values"),
        ({"when": []}, "must map another column"),
    ],
)
def test_validate_hints_checks_a_hand_built_when(note, message):
    column = ColumnDef(name="x", data_type="text", settings=set(), note=note)
    tables = {"t": _table(column)}
    if message is None:
        validate_hints(tables, [])
    else:
        with pytest.raises(ValueError, match=message):
            validate_hints(tables, [])


def test_validate_hints_refuses_when_on_a_key():
    column = ColumnDef(
        name="x", data_type="text", settings={"unique"}, note={"when": {"status": ["a"]}}
    )
    with pytest.raises(ValueError, match='"when" needs a nullable column'):
        validate_hints({"t": _table(column)}, [])


# ---------------------------------------------------------------------------
# dbt tests
# ---------------------------------------------------------------------------
def test_when_writes_a_test_and_null_rate_is_scoped_to_the_matching_rows():
    tests = hint_tests_for(from_dict(TASKS)) + hint_tests_for(from_dict(SUBSCRIPTIONS))
    assert (
        HintTest(
            "tasks", "completed_at", "model2data_when", {"conditions": {"status": ["done"]}}, "when"
        )
        in tests
    )
    assert (
        HintTest(
            "subscriptions",
            "end_date",
            "model2data_when",
            {"conditions": {"status": ["cancelled"]}, "required": False},
            "when",
        )
        in tests
    )
    assert (
        HintTest(
            "subscriptions",
            "end_date",
            "model2data_when_max_null_share",
            {"conditions": {"status": ["cancelled"]}, "max_share": 0.35},
            "null_rate",
        )
        in tests
    )
    assert not any(t.test == "model2data_max_null_share" for t in tests)


def test_the_when_macros_are_written_only_for_a_model_with_when(tmp_path):
    write_hint_macros(tmp_path / "plain")
    assert not (tmp_path / "plain/macros/model2data_when_tests.sql").exists()
    write_hint_macros(tmp_path / "when", when=True)
    sql = (tmp_path / "when/macros/model2data_when_tests.sql").read_text()
    for name in ("model2data_when", "model2data_when_max_null_share"):
        assert f"{{% test {name}(" in sql


def _check(test_type: str, arguments: dict, frame: pd.DataFrame) -> bool:
    test = DbtTest("t", "t", "x", test_type, arguments)
    return fails(test, {"t": as_seeded(frame)})


def test_the_python_check_reads_the_condition_as_the_seed_holds_it():
    conditions = {"status": ["done"], "flag": [True], "n": [2]}
    frame = pd.DataFrame(
        {
            "status": ["done", "done", "todo", None],
            "flag": [True, True, True, True],
            "n": [2, 2.0, 2, 2],
            "x": ["a", "b", None, None],
        }
    )
    assert not _check("model2data_when", {"conditions": conditions}, frame)
    frame.loc[2, "x"] = "set on a todo row"
    assert _check("model2data_when", {"conditions": conditions}, frame)
    frame.loc[2, "x"] = None
    frame.loc[1, "x"] = None
    assert _check("model2data_when", {"conditions": conditions}, frame)
    assert not _check("model2data_when", {"conditions": conditions, "required": False}, frame)


def test_the_python_null_share_counts_only_the_matching_rows():
    frame = pd.DataFrame({"status": ["done", "done", "todo", "todo"], "x": ["a", None, None, None]})
    arguments = {"conditions": {"status": ["done"]}, "max_share": 0.5}
    assert not _check("model2data_when_max_null_share", arguments, frame)
    frame.loc[0, "x"] = None
    assert _check("model2data_when_max_null_share", arguments, frame)
    nothing = {"conditions": {"status": ["gone"]}, "max_share": 0.0}
    assert not _check("model2data_when_max_null_share", nothing, frame)


DBT = shutil.which("dbt")
needs_dbt = pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")


def _dbt(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DBT or "dbt", *args, "--profiles-dir", "."],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=240,
    )


def _generate(tmp_path: Path, document: dict, name: str, *args: str) -> Path:
    path = tmp_path / f"{name}.model2data.yml"
    path.write_text(yaml.safe_dump(document))
    result = runner.invoke(
        app,
        ["--file", str(path), "--rows", "150", "--seed", "4", "--name", name]
        + ["--hint-tests", "error", *args],
    )
    assert result.exit_code == 0, result.output
    return tmp_path / f"dbt_{name}"


@needs_dbt
@pytest.mark.parametrize("args", [(), ("--days", "3")], ids=["one-day", "days"])
def test_a_when_model_passes_its_own_tests_in_dbt(tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)
    document: dict[str, Any] = copy.deepcopy(ORDERS)
    document["tables"].update(SUBSCRIPTIONS["tables"])
    document["enums"].update(SUBSCRIPTIONS["enums"])
    project = _generate(tmp_path, document, "own", *args)
    nodes = _dbt(project, "ls", "--resource-type", "test").stdout
    assert "model2data_when_stg_orders_shipped_at" in nodes
    assert "model2data_when_max_null_share_stg_subscriptions_end_date" in nodes
    build = _dbt(project, "build")
    assert build.returncode == 0, build.stdout + build.stderr
    assert "ERROR=0" in build.stdout and "WARN=0" in build.stdout


@needs_dbt
def test_the_when_test_fails_on_a_row_that_breaks_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = _generate(tmp_path, ORDERS, "bad")
    seed = project / "seeds" / "raw" / "orders.csv"
    frame = pd.read_csv(seed, dtype=str, keep_default_na=False)
    pending = frame.index[frame["status"] == "pending"][0]
    frame.loc[pending, "shipped_at"] = "2026-01-01 10:00:00"
    frame.to_csv(seed, index=False)
    build = _dbt(project, "build")
    assert build.returncode != 0
    assert any(
        "model2data_when" in line and "FAIL 1" in line for line in build.stdout.splitlines()
    ), build.stdout


@needs_dbt
@pytest.mark.parametrize("preset", ["training", "messy"])
def test_defects_report_exactly_the_when_tests_dbt_fails(tmp_path, monkeypatch, preset):
    """A defect on the status (an invalid value) can break `when`; the report says so if it does."""
    monkeypatch.chdir(tmp_path)
    document: dict[str, Any] = copy.deepcopy(ORDERS)
    project = _generate(tmp_path, document, preset, "--defects", preset)
    build = _dbt(project, "build")
    expected = {
        f["test"]
        for f in json.loads((project / "defects_report.json").read_text())["expected_failures"]
    }
    results = json.loads((project / "target" / "run_results.json").read_text())["results"]
    failed = {r["unique_id"].split(".")[2] for r in results if r["status"] in ("fail", "warn")}
    assert failed == expected, build.stdout
    assert not any(r["status"] == "error" for r in results), build.stdout


# ---------------------------------------------------------------------------
# Spec 0.4.0
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("version", ["0.2.0", "0.3.0", 0.3, "0.3"])
def test_when_in_an_older_document_says_to_write_0_4(version):
    document = copy.deepcopy(TASKS)
    document["model2data"] = version
    minor = "0.2" if str(version).startswith("0.2") else "0.3"
    assert _issues(document) == [
        "tables.tasks.columns.completed_at.generate.when: `when` is spec 0.4.0, and the "
        f"document is written against {minor}: write `model2data: 0.4.0`"
    ]


@pytest.mark.parametrize("version", ["0.4.0", "0.4", 0.4])
def test_a_0_4_document_is_read(version):
    document = copy.deepcopy(TASKS)
    document["model2data"] = version
    assert _issues(document) == []


def test_a_0_4_document_without_when_generates_what_the_same_0_3_one_does():
    plain = _without_when(TASKS)
    newer = copy.deepcopy(plain)
    plain["model2data"] = "0.3.0"
    for a, b in zip(_days(plain, 3), _days(newer, 3), strict=True):
        pd.testing.assert_frame_equal(a.tables["tasks"].state, b.tables["tasks"].state)


def test_the_writer_keeps_the_version_and_moves_to_0_4_for_when(tmp_path):
    older = from_dict(_without_when(TASKS) | {"model2data": "0.3.0"})
    assert dump(older).splitlines()[:2] == [
        "# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.3.0/"
        "model.schema.json",
        "model2data: 0.3.0",
    ]
    model = from_dict(TASKS)
    model.version = "0.2.0"  # read as 0.2, then given a `when`
    text = dump(model)
    assert text.splitlines()[:2] == [
        "# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.4.0/"
        "model.schema.json",
        "model2data: 0.4.0",
    ]
    path = tmp_path / "t.model2data.yml"
    path.write_text(text)
    assert load(path) == from_dict(TASKS)


def test_dbml_with_a_when_note_converts_to_0_4():
    without = from_dbml("Table t {\n  id int [pk]\n  s varchar\n}\n")
    assert without.version == "0.2.0"
    model = from_dbml(
        "Table t {\n  id int [pk]\n  s varchar [not null]\n"
        '  x varchar [note: \'{"when": {"s": ["a"]}}\']\n}\n'
    )
    assert model.version == "0.4.0"
