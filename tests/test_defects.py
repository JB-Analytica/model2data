"""Defects: planned from a model, applied to generated data, and reported honestly."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from model2data.defects import (
    apply_defects,
    default_column,
    defect_stream_seed,
    planned_defects,
    preset_defects,
    requested_rows,
)
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days, write_batches
from model2data.model import Defect, dump, from_dict, load, to_dict, to_engine
from model2data.model.validate import schema_url

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
AS_OF = date(2026, 1, 1)

SHOP = """\
model2data: 0.3.0
name: shop

enums:
  tier: [bronze, silver, gold]
  status: [pending, paid, shipped]

tables:
  customers:
    incremental: {new_per_day: 4}
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, unique: true, not_null: true}
      name: {type: first_name, not_null: true}
      nickname: word
      segment:
        type: word
        generate: {distinct: 4}
      tier: tier
      created_at: {type: timestamp, not_null: true}

  orders:
    incremental: {new_per_day: 8, update_rate: 0.3, changes: [status], updated_at: updated_at}
    columns:
      id: {type: bigint, pk: true}
      customer_id: {type: bigint, not_null: true, references: customers.id}
      status:
        type: status
        generate:
          transitions: {pending: [paid], paid: [shipped]}
      amount:
        type: numeric
        not_null: true
        generate: {min: 1, max: 500}
      ordered_at: {type: timestamp, not_null: true}
      updated_at:
        type: timestamp
        not_null: true
        generate: {after: ordered_at}

  lines:
    columns:
      order_id: {type: bigint, not_null: true, references: orders.id}
      line: {type: int, not_null: true, generate: {min: 1, max: 100000}}
      quantity: {type: int, not_null: true, generate: {min: 1, max: 9}}
    keys:
      - {pk: [order_id, line]}

  notes:
    columns:
      body: text
      noted_on: date

run:
  rows: 40
  seed: 11
  as_of: 2026-01-01
"""


@pytest.fixture(scope="module")
def shop():
    return load(SHOP)


@pytest.fixture(scope="module")
def clean(shop):
    inputs = to_engine(shop)
    return generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=40, seed=11, as_of=AS_OF)


@pytest.fixture(scope="module")
def days(shop):
    return generate_days(shop, 3, seed=11, as_of=AS_OF)


def _apply(shop, data, plan, **kwargs):
    kwargs.setdefault("seed", 11)
    return apply_defects(shop, data, plan, **kwargs)


def _failures(report) -> list[str]:
    return [failure.test for failure in report.expected_failures]


# ---------------------------------------------------------------------------
# The model
# ---------------------------------------------------------------------------
WITH_DEFECTS = SHOP.replace(
    "  notes:\n",
    "    defects:\n"
    "      - {type: nulls, column: order_id, count: 2}\n"
    "      - {type: duplicate_keys, share: 0.05}\n\n"
    "  notes:\n",
).replace("  as_of: 2026-01-01\n", "  as_of: 2026-01-01\n  defects: training\n")


def test_defects_round_trip_through_the_document():
    model = load(WITH_DEFECTS)
    assert model.tables["lines"].defects == [
        Defect("nulls", column="order_id", count=2),
        Defect("duplicate_keys", share=0.05),
    ]
    assert model.run is not None and model.run.defects == "training"
    assert from_dict(to_dict(model)) == model
    text = dump(model)
    assert load(text) == model
    assert "    defects:\n      - {type: nulls, column: order_id, count: 2}\n" in text
    assert text.splitlines()[0].endswith("/spec/0.3.0/model.schema.json")
    assert "\n  defects: training\n" in text


def test_an_empty_defects_list_is_kept():
    model = load(SHOP.replace("      noted_on: date\n", "      noted_on: date\n    defects: []\n"))
    assert model.tables["notes"].defects == []
    assert "    defects: []\n" in dump(model)
    assert load(dump(model)) == model


def test_a_0_2_model_given_defects_is_written_as_0_3():
    model = load((EXAMPLES / "ecommerce.model2data.yml").read_text())
    assert model.version == "0.2.0"
    assert to_dict(model)["model2data"] == "0.2.0"
    assert dump(model).splitlines()[0].endswith("/spec/0.2.0/model.schema.json")
    model.tables["orders"].defects = [Defect("nulls", column="order_date", count=1)]
    assert to_dict(model)["model2data"] == "0.3.0"
    assert load(dump(model)).tables["orders"].defects == model.tables["orders"].defects


def test_the_version_as_a_yaml_number_0_3_is_read():
    model = load("model2data: 0.3\ntables:\n  t:\n    columns:\n      a: int\n")
    assert model.version == 0.3
    assert schema_url(0.3).endswith("/0.3.0/model.schema.json")
    assert schema_url(0.2).endswith("/0.2.0/model.schema.json")
    # A version it does not name points at the current spec.
    assert schema_url(0.4).endswith("/0.4.0/model.schema.json")
    assert schema_url(None).endswith("/0.5.0/model.schema.json")
    assert schema_url(0.4).endswith("/0.4.0/model.schema.json")


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------
def test_training_gives_each_kind_of_test_one_defect():
    model = load(EXAMPLES / "ecommerce.model2data.yml")
    assert preset_defects(model, "training") == {
        "customers": [Defect("nulls", column="first_name", count=2)],
        "orders": [
            Defect("orphan_foreign_keys", column="customer_id", count=2),
            Defect("invalid_values", column="status", count=2),
        ],
        # The duplicate keys go to a table no foreign key points at.
        "order_items": [Defect("duplicate_keys", column="id", count=2)],
    }


def test_training_adds_the_late_kinds_on_a_run_of_days():
    model = load(EXAMPLES / "ecommerce_daily.model2data.yml")
    expanded = preset_defects(model, "training", days=2)
    assert expanded["customers"][-1] == Defect("late_arriving", count=2)
    assert expanded["orders"][-1] == Defect("late_updates", count=2)
    assert "late_arriving" not in str(preset_defects(model, "training"))


def test_training_skips_a_kind_the_model_has_no_test_for(shop):
    model = load(EXAMPLES / "hackernews.model2data.yml")
    kinds_given = {
        d.type for entries in preset_defects(model, "training").values() for d in entries
    }
    assert "invalid_values" not in kinds_given


def test_messy_gives_every_table_a_mix():
    model = load(EXAMPLES / "ecommerce.model2data.yml")
    expanded = preset_defects(model, "messy")
    assert expanded["orders"] == [
        Defect("duplicate_keys", share=0.01),
        Defect("orphan_foreign_keys", column="customer_id", share=0.02),
        Defect("nulls", column="order_date", share=0.02),
        Defect("invalid_values", column="status", share=0.02),
    ]
    assert expanded["customers"] == [
        Defect("duplicate_keys", share=0.01),
        Defect("nulls", column="first_name", share=0.02),
        Defect("messy_text", column="last_name", share=0.05),
    ]
    assert expanded["product_reviews"][-1] == Defect("messy_text", column="comment", share=0.05)


def test_messy_text_avoids_keys_references_and_distinct_columns(shop):
    expanded = preset_defects(shop, "messy", days=1)
    assert Defect("messy_text", column="nickname", share=0.05) in expanded["customers"]
    assert expanded["orders"][-2:] == [
        Defect("late_arriving", share=0.02),
        Defect("late_updates", share=0.02),
    ]
    assert "notes" in expanded  # messy text in body; no key, so no duplicates
    assert expanded["notes"] == [Defect("messy_text", column="body", share=0.05)]


def test_clean_is_nothing_and_an_unknown_preset_is_refused(shop):
    assert preset_defects(shop, "clean") == {}
    with pytest.raises(ValueError, match="defects preset must be one of"):
        preset_defects(shop, "chaos")


def test_a_table_entry_overrides_the_preset():
    model = load(WITH_DEFECTS)
    model.tables["customers"].defects = [
        Defect("duplicate_keys", column="id", count=5),  # the preset's, by its default column
        Defect("invalid_values", column="tier", count=0),  # switched off
        Defect("messy_text", column="nickname", count=1),  # added
    ]
    model.tables["orders"].defects = []
    planned = planned_defects(model)  # run.defects: training
    assert planned["customers"] == [
        Defect("duplicate_keys", column="id", count=5),
        Defect("messy_text", column="nickname", count=1),
    ]
    assert "orders" not in planned
    assert planned["lines"] == [
        Defect("orphan_foreign_keys", column="order_id", count=2),
        Defect("nulls", column="order_id", count=2),
        Defect("duplicate_keys", share=0.05),
    ]
    assert planned_defects(model, "clean")["customers"] == [
        Defect("duplicate_keys", column="id", count=5),
        Defect("messy_text", column="nickname", count=1),
    ]


def test_without_a_run_the_preset_is_clean(shop):
    assert planned_defects(shop) == {}


def test_default_columns(shop):
    customers, orders, lines = (shop.tables[k] for k in ("customers", "orders", "lines"))
    assert default_column(shop, customers, "duplicate_keys") == "id"
    assert default_column(shop, lines, "duplicate_keys") is None
    assert default_column(shop, orders, "late_arriving") == "updated_at"
    assert default_column(shop, customers, "late_arriving") == "created_at"
    assert default_column(shop, orders, "late_updates") == "updated_at"
    assert default_column(shop, customers, "late_updates") is None
    assert default_column(shop, customers, "nulls") is None


def test_requested_rows():
    assert requested_rows(Defect("nulls", count=3), 100) == 3
    assert requested_rows(Defect("nulls", share=0.025), 100) == 3
    assert requested_rows(Defect("nulls", share=0.001), 100) == 1
    assert requested_rows(Defect("nulls", share=0.0), 100) == 0
    assert requested_rows(Defect("nulls"), 100) == 0


# ---------------------------------------------------------------------------
# Applying them, one day
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("table", "defect", "failures"),
    [
        pytest.param(
            "customers", Defect("duplicate_keys", count=3), ["unique_stg_customers_id"], id="pk"
        ),
        pytest.param(
            "customers",
            Defect("duplicate_keys", column="email", count=2),
            ["unique_stg_customers_email"],
            id="unique-column",
        ),
        pytest.param(
            "lines",
            Defect("duplicate_keys", count=2),
            ["unique_combination_stg_lines_order_id_line"],
            id="composite-pk",
        ),
        pytest.param(
            "orders",
            Defect("orphan_foreign_keys", column="customer_id", count=3),
            ["relationships_stg_orders_customer_id__id__ref_stg_customers_"],
            id="orphans",
        ),
        pytest.param(
            "customers",
            Defect("nulls", column="name", count=2),
            ["not_null_stg_customers_name"],
            id="nulls",
        ),
        pytest.param(
            "customers",
            Defect("invalid_values", column="tier", count=4),
            ["accepted_values_stg_customers_tier__bronze__silver__gold"],
            id="invalid",
        ),
        pytest.param("customers", Defect("messy_text", column="nickname", count=5), [], id="messy"),
        pytest.param(
            "customers",
            Defect("messy_text", column="segment", count=5),
            ["model2data_max_distinct_stg_customers_segment__4"],
            id="messy-breaks-a-hint-test",
        ),
    ],
)
def test_a_defect_breaks_its_rows_and_names_its_tests(shop, clean, table, defect, failures):
    broken, report = _apply(shop, clean, {table: [defect]})
    (applied,) = report.defects
    assert applied.applied == defect.count
    assert len(applied.rows) == defect.count
    assert applied.expected_failures == failures
    assert _failures(report) == failures
    assert report.failing_without_defects == []
    for key, frame in clean.items():
        assert len(broken[key]) == len(frame)
        if key != table:
            pd.testing.assert_frame_equal(broken[key], frame)
    changed = (broken[table].astype(str) != clean[table].astype(str)).any(axis=1).sum()
    assert changed == defect.count


def test_the_report_names_the_tests_and_the_rows(shop, clean):
    _, report = _apply(
        shop,
        clean,
        {"customers": [Defect("duplicate_keys", count=2)]},
        preset="training",
    )
    data = report.to_dict()
    assert list(data) == [
        "model2data",
        "seed",
        "preset",
        "defects",
        "expected_failures",
        "failing_without_defects",
    ]
    assert data["seed"] == 11 and data["preset"] == "training"
    (entry,) = data["defects"]
    assert list(entry) == [
        "table",
        "defect",
        "column",
        "requested",
        "applied",
        "rows",
        "row_key",
        "row_numbers",
        "expected_failures",
    ]
    assert entry["requested"] == {"count": 2} and entry["row_key"] == ["id"]
    assert data["expected_failures"] == [
        {
            "test": "unique_stg_customers_id",
            "table": "customers",
            "column": "id",
            "type": "unique",
            "severity": "error",
            "defects": ["duplicate_keys"],
        }
    ]
    assert json.loads(report.to_json()) == data


def test_a_duplicate_repeats_an_existing_key_on_an_unreferenced_row(shop, clean):
    broken, report = _apply(shop, clean, {"customers": [Defect("duplicate_keys", count=3)]})
    ids = broken["customers"]["id"]
    referenced = set(clean["orders"]["customer_id"])
    for key in report.defects[0].rows:
        assert (ids == key).sum() == 2
    gone = set(clean["customers"]["id"]) - set(ids)
    assert len(gone) == 3 and not gone & referenced


def test_a_composite_duplicate_reports_its_key_as_lists(shop, clean):
    _, report = _apply(shop, clean, {"lines": [Defect("duplicate_keys", count=1)]})
    (applied,) = report.defects
    assert applied.column == ["order_id", "line"]
    assert applied.row_key == ["order_id", "line"]
    assert isinstance(applied.rows[0], list) and len(applied.rows[0]) == 2


def test_a_table_without_a_key_is_reported_by_row_number(shop, clean):
    broken, report = _apply(shop, clean, {"notes": [Defect("messy_text", column="body", count=2)]})
    (applied,) = report.defects
    assert applied.row_key is None
    for number in applied.rows:
        assert broken["notes"]["body"].iloc[number - 1] != clean["notes"]["body"].iloc[number - 1]


def test_nulls_in_the_primary_key_are_reported_by_row_number(shop, clean):
    broken, report = _apply(
        shop, clean, {"notes": [], "customers": [Defect("nulls", column="id", count=1)]}
    )
    (applied,) = report.defects
    assert applied.row_key == ["id"] and applied.rows == [None]
    assert pd.isna(broken["customers"]["id"].iloc[applied.row_numbers[0] - 1])
    assert applied.expected_failures == ["not_null_stg_customers_id"]


def test_messy_text_never_makes_a_value_the_column_holds(shop, clean):
    broken, report = _apply(
        shop, clean, {"customers": [Defect("messy_text", column="email", count=40)]}
    )
    assert report.defects[0].applied == 40
    assert broken["customers"]["email"].is_unique
    assert report.expected_failures == []


def test_invalid_values_are_never_members(shop, clean):
    broken, _ = _apply(
        shop, clean, {"customers": [Defect("invalid_values", column="tier", count=40)]}
    )
    assert not set(broken["customers"]["tier"]) & {"bronze", "silver", "gold"}


def test_too_many_rows_are_capped_and_said_so(shop, clean):
    _, report = _apply(
        shop,
        clean,
        {
            "customers": [
                Defect("duplicate_keys", count=500),
                Defect("nulls", column="id", count=500),
            ]
        },
    )
    duplicates, nulls = report.defects
    assert duplicates.applied == 39
    assert duplicates.note is not None and "at most 39" in duplicates.note
    assert "referenced by child rows" in duplicates.note
    # Repeating keys the orders point at leaves those orders without their customer:
    # the report says so rather than only naming the unique test.
    assert "relationships_stg_orders_customer_id__id__ref_stg_customers_" in _failures(report)
    assert "relationships_stg_orders_customer_id__id__ref_stg_customers_" in (
        duplicates.expected_failures
    )
    # The rows the duplicates broke are left alone by the nulls.
    # Every key is a duplicate's or its source's: the nulls leave them be.
    assert nulls.applied == 0
    assert nulls.note is not None and nulls.note.startswith("asked for 500 rows, applied 0")


def test_two_defects_on_one_table_never_take_the_same_cell(shop, clean):
    broken, report = _apply(
        shop,
        clean,
        {
            "customers": [
                Defect("nulls", column="name", count=20),
                Defect("messy_text", column="name", count=20),
            ]
        },
    )
    nulls, messy = report.defects
    assert not set(nulls.rows) & set(messy.rows)
    assert broken["customers"]["name"].isna().sum() == 20


def test_a_defect_the_data_cannot_take_is_reported_not_applied(shop, clean):
    plan = {
        "customers": [
            Defect("orphan_foreign_keys", column="name", count=1),
            Defect("invalid_values", column="name", count=1),
            Defect("late_arriving", count=1),
            Defect("late_updates", count=1),
        ],
        "notes": [Defect("duplicate_keys", count=1)],
    }
    broken, report = _apply(shop, clean, plan)
    assert [d.applied for d in report.defects] == [0, 0, 0, 0, 0]
    assert [d.note for d in report.defects] == [
        "name references no table of the model",
        "name has no allowed set",
        "not applied: late_arriving needs a run of several days (--days), and this run "
        "generated one",
        "not applied: late_updates needs a run of several days (--days), and this run "
        "generated one",
        "notes has no primary key to repeat",
    ]
    assert report.expected_failures == []
    for key, frame in clean.items():
        pd.testing.assert_frame_equal(broken[key], frame)


def test_an_unknown_table_is_refused(shop, clean):
    with pytest.raises(ValueError, match="not in the model"):
        _apply(shop, clean, {"nope": [Defect("nulls", column="a", count=1)]})


def test_the_input_frames_are_not_modified(shop, clean):
    before = {key: frame.copy() for key, frame in clean.items()}
    _apply(shop, clean, {"customers": [Defect("nulls", column="name", count=3)]})
    for key, frame in clean.items():
        pd.testing.assert_frame_equal(frame, before[key])


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
PLAN = {
    "customers": [
        Defect("duplicate_keys", count=2),
        Defect("messy_text", column="nickname", count=3),
    ],
    "orders": [Defect("orphan_foreign_keys", column="customer_id", share=0.1)],
}


def _csv(frames) -> str:
    return "".join(f"== {k}\n{frames[k].to_csv(index=False)}" for k in sorted(frames))


def test_the_same_seed_gives_the_same_bytes(shop, clean):
    first, first_report = _apply(shop, clean, PLAN)
    second, second_report = _apply(shop, clean, PLAN)
    assert _csv(first) == _csv(second)
    assert first_report.to_json() == second_report.to_json()
    third, _ = _apply(shop, clean, PLAN, seed=12)
    assert _csv(third) != _csv(first)


def test_a_defect_on_another_table_moves_nothing_here(shop, clean):
    alone, alone_report = _apply(shop, clean, {"orders": PLAN["orders"]})
    both, both_report = _apply(shop, clean, PLAN)
    pd.testing.assert_frame_equal(alone["orders"], both["orders"])
    assert alone_report.defects[0].rows == both_report.defects[-1].rows


def test_the_stream_depends_on_the_defect():
    one = defect_stream_seed(1, "t", Defect("nulls", column="a"))
    assert one == defect_stream_seed(1, "t", Defect("nulls", column="a", count=9))
    assert one != defect_stream_seed(1, "t", Defect("nulls", column="b"))
    assert one != defect_stream_seed(1, "u", Defect("nulls", column="a"))


def test_without_a_seed_the_report_has_none(shop, clean):
    _, report = apply_defects(shop, clean, {"customers": [Defect("nulls", column="name", count=1)]})
    assert report.seed is None and report.defects[0].applied == 1


# ---------------------------------------------------------------------------
# Several days
# ---------------------------------------------------------------------------
def test_late_arriving_rows_arrive_after_their_cutoff(shop, days):
    broken, report = _apply(shop, days, {"orders": [Defect("late_arriving", count=4)]})
    (applied,) = report.defects
    assert applied.applied == 4 and applied.column == "updated_at"
    assert applied.days is not None and min(applied.days) >= 1
    assert applied.expected_failures == [] and report.expected_failures == []
    assert applied.note is not None and "no generic test fails" in applied.note
    final = broken[-1].tables["orders"].state
    for key, day in zip(applied.rows, applied.days, strict=True):
        row = final[final["id"] == key].iloc[0]
        cutoff = pd.to_datetime(broken[day - 1].tables["orders"].state["updated_at"]).max()
        assert pd.Timestamp(row["updated_at"]) < cutoff
        assert pd.Timestamp(row["ordered_at"]) <= pd.Timestamp(row["updated_at"])
        # Delivered with the day it arrived on, not before.
        inserted = broken[day].tables["orders"].inserted
        assert inserted[inserted["id"] == key].iloc[0]["updated_at"] == row["updated_at"]
        assert key not in set(broken[day - 1].tables["orders"].state["id"])


def test_late_updates_keep_the_timestamp_of_the_version_they_replace(shop, days):
    broken, report = _apply(shop, days, {"orders": [Defect("late_updates", count=3)]})
    (applied,) = report.defects
    assert applied.applied == 3 and applied.days is not None
    assert report.expected_failures == []
    for key, day in zip(applied.rows, applied.days, strict=True):
        before = days[day - 1].tables["orders"].state
        now = broken[-1].tables["orders"].state
        old = before[before["id"] == key].iloc[0]
        new = now[now["id"] == key].iloc[0]
        assert new["updated_at"] == old["updated_at"]
        assert new["status"] != old["status"]
        updated = broken[day].tables["orders"].updated
        assert updated[updated["id"] == key].iloc[0]["updated_at"] == old["updated_at"]


def test_a_key_is_broken_from_the_day_its_row_was_inserted(shop, days, tmp_path):
    broken, report = _apply(shop, days, {"orders": [Defect("duplicate_keys", count=5)]})
    final = broken[-1].tables["orders"].state
    first = broken[0].tables["orders"]
    old_final = days[-1].tables["orders"].state
    changed = [p for p in range(len(final)) if final["id"].iloc[p] != old_final["id"].iloc[p]]
    assert len(changed) == 5
    for position in changed:
        if position < len(first.state):
            assert first.inserted["id"].iloc[position] == final["id"].iloc[position]
    assert [r.tables["orders"].updated_positions for r in broken] == [
        r.tables["orders"].updated_positions for r in days
    ]
    write_batches(tmp_path, broken, {key: key for key in shop.tables})
    assert (tmp_path / "days" / "orders" / "day_003.csv").exists()


def test_a_value_is_broken_from_the_day_its_row_was_last_delivered(shop, days):
    broken, report = _apply(shop, days, {"orders": [Defect("nulls", column="amount", count=30)]})
    final = broken[-1].tables["orders"].state
    nulled = [p for p in range(len(final)) if pd.isna(final["amount"].iloc[p])]
    assert len(nulled) == 30
    updated_on = {
        p: [r.day for r in days[1:] if p in r.tables["orders"].updated_positions] for p in nulled
    }
    earlier = 0
    for position, update_days in updated_on.items():
        for day in update_days:
            table_day = broken[day].tables["orders"]
            row = table_day.updated_positions.index(position)
            value = table_day.updated["amount"].iloc[row]
            # Only the last delivery carries the null; an earlier update held the clean value.
            assert pd.isna(value) == (day == max(update_days))
            earlier += day != max(update_days)
        if update_days and position < len(broken[0].tables["orders"].state):
            assert not pd.isna(broken[0].tables["orders"].state["amount"].iloc[position])
    assert any(updated_on.values())


def test_the_late_kinds_need_their_columns(shop, days):
    plan = {
        "lines": [Defect("late_arriving", count=1), Defect("late_updates", count=1)],
    }
    _, report = _apply(shop, days, plan)
    assert [d.note for d in report.defects] == [
        "lines has no date or timestamp column",
        "lines has no incremental.updated_at",
    ]


def test_days_are_deterministic_too(shop, days):
    plan = {"orders": [Defect("late_arriving", count=2), Defect("late_updates", count=2)]}
    first, first_report = _apply(shop, days, plan)
    second, second_report = _apply(shop, days, plan)
    assert first_report.to_json() == second_report.to_json()
    for a, b in zip(first, second, strict=True):
        assert _csv(a.state) == _csv(b.state)


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------
EDGES = """\
model2data: 0.3.0
tables:
  codes:
    incremental: {new_per_day: 3, update_rate: 0.5, changes: [label], updated_at: seen_at}
    columns:
      code: {type: uuid, pk: true}
      ref: {type: text, not_null: true}
      label: word
      seen_at:
        type: timestamp
        generate: {null_rate: 0.6}
      closed_at:
        type: timestamp
        generate: {null_rate: 0.9}
  uses:
    columns:
      id: {type: int, pk: true}
      code_ref: {type: text, references: codes.ref}
      a: {type: int, not_null: true}
      b: {type: int, not_null: true}
    keys:
      - {unique: [a, b]}
  pairs:
    columns:
      a: int
      b: int
    foreign_keys:
      - {columns: [a, b], references: uses, to_columns: [a, b]}
  bare:
    columns:
      n: int
"""


def test_a_text_key_is_reported_as_text():
    model = load(EDGES)
    frames = generate_days(model, 0, base_rows=12, seed=3, as_of=AS_OF)[0].state
    _, report = apply_defects(
        model, frames, {"codes": [Defect("messy_text", column="label", count=2)]}, seed=3
    )
    assert all(isinstance(row, str) and len(row) == 36 for row in report.defects[0].rows)


def test_late_kinds_leave_nulls_null_and_skip_updates_of_untimed_rows():
    model = load(EDGES)
    days = generate_days(model, 2, base_rows=12, seed=3, as_of=AS_OF)
    plan = {"codes": [Defect("late_arriving", column="ref", count=1)]}
    plan_updates = {"codes": [Defect("late_updates", count=30)]}
    broken, report = apply_defects(model, days, plan, seed=3)
    assert report.defects[0].applied == 0
    assert report.defects[0].note == "ref is not a date or timestamp column"
    broken, report = apply_defects(
        model, days, {"codes": [Defect("late_arriving", count=3)]}, seed=3
    )
    final = broken[-1].tables["codes"].state
    clean = days[-1].tables["codes"].state
    for key in report.defects[0].rows:
        row, was = final[final["code"] == key].iloc[0], clean[clean["code"] == key].iloc[0]
        assert pd.isna(row["closed_at"]) == pd.isna(was["closed_at"])
    _, report = apply_defects(model, days, plan_updates, seed=3)
    (applied,) = report.defects
    assert 0 < applied.applied < 30
    for key, day in zip(applied.rows, applied.days or [], strict=True):
        before = days[day - 1].tables["codes"].state
        assert not pd.isna(before[before["code"] == key].iloc[0]["seen_at"])


def test_presets_on_a_model_of_odd_tables():
    model = load(EDGES)
    messy = preset_defects(model, "messy", days=1)
    assert "bare" not in messy  # nothing in it any defect suits
    assert messy["codes"][0] == Defect("duplicate_keys", share=0.01)
    # `ref` is not null but referenced: nulls in it would orphan `uses`.
    assert not any(d.type == "nulls" for d in messy["codes"])
    training = preset_defects(model, "training", days=1)
    assert not any(d.type.startswith("late") for d in training.get("uses", []))
    assert preset_defects(load(SHOP.replace("incremental:", "x-incremental:")), "training", days=1)


def test_a_value_the_column_dtype_cannot_hold_widens_it():
    from model2data.defects.apply import _set

    frame = pd.DataFrame({"n": pd.array([1, 2], dtype="Int64")})
    _set(frame, 0, "n", "orphan-1")
    assert frame["n"].tolist() == ["orphan-1", 2]


def test_a_fresh_value_never_repeats_a_taken_one():
    import random

    from model2data.defects.apply import _fresh_values

    first = f"orphan-{random.Random(5).getrandbits(32):08x}"
    values = _fresh_values(random.Random(5), {first, "abc"}, 1)
    assert values != [first] and values[0].startswith("orphan-")


def test_a_test_only_the_defects_together_break_names_each_of_them():
    from model2data.dbt.tests import DbtTest
    from model2data.defects.apply import _culprits

    test = DbtTest("r", "stg_c", "p", "relationships", parent=("stg_p", "id"))
    keys = {"stg_c": "c", "stg_p": "p"}
    tables = [("c", True), ("p", True), ("x", True), ("c", False)]
    assert _culprits(test, [set(), set(), set(), set()], tables, keys) == [0, 1]
    assert _culprits(test, [set(), {"r"}, set(), set()], tables, keys) == [1]
    lone = DbtTest("n", "stg_c", "p", "not_null")
    assert _culprits(lone, [set(), set(), set(), set()], tables, keys) == [0]
