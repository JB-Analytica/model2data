"""The days after the first: spec 0.2.0, "Days after the first"."""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days, iter_days
from model2data.generate.options import TimeProfile
from model2data.generate.relationships import classify_refs
from model2data.model import Incremental, ModelError, dump, from_dict, load, to_dict, to_engine

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
DAILY = EXAMPLES / "ecommerce_daily.model2data.yml"
AS_OF = date(2026, 1, 31)
TRANSITIONS = {
    "pending": ["processing", "cancelled"],
    "processing": ["shipped", "cancelled"],
    "shipped": ["delivered"],
}
DOCUMENTS = sorted(EXAMPLES.glob("*.model2data.yml"))


def _days(days: int = 5, rows: int = 120, **kwargs):
    kwargs.setdefault("seed", 7)
    return generate_days(load(DAILY), days, base_rows=rows, as_of=AS_OF, **kwargs)


def _csv(frames: dict[str, pd.DataFrame]) -> str:
    return "".join(f"== {k}\n" + frames[k].to_csv(index=False) for k in sorted(frames))


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_day_zero_is_the_run_without_days(document: Path):
    inputs = to_engine(load(document))
    plain = generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=40, seed=11, as_of=datetime(2026, 1, 1)
    )
    with_days = generate_days(inputs, 2, base_rows=40, seed=11, as_of=datetime(2026, 1, 1))
    assert _csv(with_days[0].state) == _csv(plain)
    assert [d.day for d in with_days] == [0, 1, 2]
    assert all(len(t.updated) == 0 for t in with_days[0].tables.values())


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_days_hold_on_every_example_with_every_table_moving(document: Path):
    inputs = to_engine(load(document))
    inputs.incremental = {k: Incremental(new_per_day=6, update_rate=0.2) for k in inputs.tables}
    days = generate_days(inputs, 4, base_rows=50, seed=3, as_of=AS_OF)
    fk_refs, _ = classify_refs(inputs.tables, inputs.refs)
    last = days[-1].state
    for ref in fk_refs:
        child = last[ref["source_table"]][ref["source_column"]].dropna()
        parents = set(last[ref["target_table"]][ref["target_column"]].dropna())
        assert child.isin(parents).all(), ref
    for key, table in inputs.tables.items():
        for column in table.columns:
            if "pk" in column.settings:
                assert last[key][column.name].is_unique, (key, column.name)
        assert list(last[key].columns) == [c.name for c in table.columns]


def test_reading_the_days_twice_gives_the_same_bytes():
    a, b = _days(), _days()
    for x, y in zip(a, b, strict=True):
        assert _csv(x.state) == _csv(y.state)
        assert _csv({k: t.updated for k, t in x.tables.items()}) == _csv(
            {k: t.updated for k, t in y.tables.items()}
        )


def test_generating_more_days_does_not_change_the_earlier_ones():
    short, long = _days(2), _days(5)
    for day in range(3):
        assert _csv(short[day].state) == _csv(long[day].state)


def test_iter_days_is_generate_days():
    lazy = list(iter_days(load(DAILY), 3, base_rows=60, seed=2, as_of=AS_OF))
    eager = generate_days(load(DAILY), 3, base_rows=60, seed=2, as_of=AS_OF)
    assert [_csv(d.state) for d in lazy] == [_csv(d.state) for d in eager]


def test_inserted_rows_fall_on_their_day_and_a_row_count_is_new_per_day():
    days = _days()
    for result in days[1:]:
        day = result.date
        assert day == date.fromordinal(AS_OF.toordinal() + result.day)
        for key, column, count in (
            ("customers", "created_at", 5),
            ("orders", "order_date", 20),
            ("orders", "updated_at", 20),
        ):
            inserted = result.tables[key].inserted
            assert len(inserted) == count
            assert {pd.Timestamp(v).date() for v in inserted[column]} == {day}
        assert len(result.tables["order_items"].inserted) == 50
    day_zero = days[0].tables["orders"].state["order_date"]
    assert pd.to_datetime(day_zero).max() < pd.Timestamp(AS_OF)


def test_timestamps_span_the_day_and_business_hours_shape_them():
    many = generate_days(
        load(DAILY), 1, base_rows=50, seed=1, as_of=AS_OF, row_overrides={"customers": 50}
    )
    inputs = to_engine(load(DAILY))
    inputs.incremental["customers"] = Incremental(new_per_day=2000)
    flat = generate_days(inputs, 1, base_rows=50, seed=1, as_of=AS_OF)[1]
    hours = pd.to_datetime(flat.tables["customers"].inserted["created_at"]).dt.hour
    assert hours.nunique() == 24
    shaped = generate_days(
        inputs, 1, base_rows=50, seed=1, as_of=AS_OF, time_profile=TimeProfile(business_hours=True)
    )[1]
    shaped_hours = pd.to_datetime(shaped.tables["customers"].inserted["created_at"]).dt.hour
    assert shaped_hours.between(9, 17).mean() > hours.between(9, 17).mean() + 0.2
    assert many[1].day == 1


def test_keys_are_unique_across_days_and_continue_after_the_existing_ones():
    days = _days()
    for key in ("customers", "orders", "order_items"):
        state = days[-1].tables[key].state
        assert state["id"].is_unique
        before = days[0].tables[key].state["id"]
        assert state["id"].iloc[: len(before)].tolist() == before.tolist()
        assert days[1].tables[key].inserted["id"].min() == before.max() + 1
    assert days[-1].tables["customers"].state["email"].is_unique


def test_foreign_keys_always_resolve_and_same_day_parents_are_eligible():
    days = _days(rows=30)
    for result in days:
        s = result.state
        assert s["orders"]["customer_id"].isin(s["customers"]["id"]).all()
        assert s["order_items"]["order_id"].isin(s["orders"]["id"]).all()
        assert s["order_items"]["product_id"].isin(s["products"]["id"]).all()
    # A few hundred new items pick among ~60+ orders: some land on orders of the same day.
    inputs = to_engine(load(DAILY))
    inputs.incremental["order_items"] = Incremental(new_per_day=400)
    inputs.incremental["orders"] = Incremental(new_per_day=50, update_rate=0.0)
    day1 = generate_days(inputs, 1, base_rows=10, seed=5, as_of=AS_OF)[1]
    new_orders = set(day1.tables["orders"].inserted["id"])
    assert day1.tables["order_items"].inserted["order_id"].isin(new_orders).any()


def test_transitions_only_follow_allowed_edges():
    days = _days(6, rows=200)
    changed = 0
    for before, after in zip(days, days[1:], strict=False):
        old = before.tables["orders"].state.set_index("id")["status"]
        for _, row in after.tables["orders"].updated.iterrows():
            previous, current = old[row["id"]], row["status"]
            if previous == current:
                continue
            assert current in TRANSITIONS.get(previous, []), (previous, current)
            changed += 1
    assert changed > 50


def test_new_rows_start_in_a_state_no_transition_leads_into():
    # Only `pending` is never a target, so a new order starts there, never
    # already delivered. Day 0 is the state rows have reached, so it has them all.
    days = _days(4, rows=200)
    assert set(days[0].tables["orders"].state["status"].dropna()) - {"pending"}
    for day in days[1:]:
        inserted = day.tables["orders"].inserted["status"].dropna()
        assert len(inserted) > 0
        assert set(inserted) == {"pending"}


def test_a_transition_cycle_has_no_start_so_every_member_may_begin():
    document = to_dict(load(DAILY))
    status = document["tables"]["orders"]["columns"]["status"]
    members = document["enums"][status["type"]]
    status["generate"]["transitions"] = {
        m: [members[(i + 1) % len(members)]] for i, m in enumerate(members)
    }
    days = generate_days(from_dict(document), 3, base_rows=200, seed=7, as_of=AS_OF)
    starts = set().union(*(set(d.tables["orders"].inserted["status"].dropna()) for d in days[1:]))
    assert len(starts) > 1


def test_update_counts_follow_update_rate_and_only_changes_columns_change():
    days = _days(4)
    for before, after in zip(days, days[1:], strict=False):
        existing = len(before.tables["orders"].state)
        updated = after.tables["orders"].updated
        assert len(updated) == math.floor(0.1 * existing + 0.5)
        old = before.tables["orders"].state.set_index("id")
        for _, row in updated.iterrows():
            base = old.loc[row["id"]]
            for column in ("customer_id", "order_date", "total_amount"):
                assert base[column] == row[column]
        # the state is the old state with the updates applied, then the inserts
        new_state = after.tables["orders"].state
        assert len(new_state) == existing + 20
        by_id = new_state.set_index("id")
        for _, row in updated.iterrows():
            assert by_id.loc[row["id"], "status"] == row["status"]
            assert by_id.loc[row["id"], "updated_at"] == row["updated_at"]


def test_updated_at_is_on_the_day_for_inserted_and_updated_rows_and_never_goes_back():
    days = _days(4)
    for after in days[1:]:
        day = after.date
        for frame in (after.tables["orders"].inserted, after.tables["orders"].updated):
            assert {pd.Timestamp(v).date() for v in frame["updated_at"]} == {day}
        inserted = after.tables["orders"].inserted
        assert (
            pd.to_datetime(inserted["updated_at"]) >= pd.to_datetime(inserted["order_date"])
        ).all()
    # rows nobody touched keep the updated_at they had
    touched = set()
    for result in days[1:]:
        touched |= set(result.tables["orders"].updated["id"])
    first = days[0].tables["orders"].state.set_index("id")["updated_at"]
    last = days[-1].tables["orders"].state.set_index("id")["updated_at"]
    untouched = [i for i in first.index if i not in touched]
    assert untouched and (first[untouched] == last[untouched]).all()


def test_keys_of_updated_rows_are_unchanged_and_tables_without_incremental_never_change():
    days = _days()
    for result in days[1:]:
        for key in ("products", "product_reviews"):
            table = result.tables[key]
            assert len(table.inserted) == 0 and len(table.updated) == 0
            assert table.state.equals(days[0].tables[key].state)
    orders = days[-1].tables["orders"].state
    assert orders["id"].iloc[:120].tolist() == days[0].tables["orders"].state["id"].tolist()


def test_a_table_added_to_the_model_does_not_move_another_tables_days():
    base = load(DAILY)
    data = to_dict(base)
    data["tables"]["audit_log"] = {
        "incremental": {"new_per_day": 9},
        "columns": {
            "id": {"type": "bigint", "pk": True, "not_null": True},
            "at": {"type": "timestamp", "not_null": True},
            "customer_id": {"type": "bigint", "references": "customers.id"},
        },
    }
    bigger = from_dict(data)
    a = generate_days(base, 3, base_rows=80, seed=9, as_of=AS_OF)
    b = generate_days(bigger, 3, base_rows=80, seed=9, as_of=AS_OF)
    for x, y in zip(a, b, strict=True):
        for key in x.tables:
            assert _csv({key: x.tables[key].state}) == _csv({key: y.tables[key].state}), key
            assert x.tables[key].updated.equals(y.tables[key].updated)
    assert len(b[-1].tables["audit_log"].state) == 80 + 27


def test_a_table_seed_rerolls_that_tables_days_only():
    a = _days(3)
    b = _days(3, table_seeds={"orders": 4})
    assert _csv({"customers": a[-1].tables["customers"].state}) == _csv(
        {"customers": b[-1].tables["customers"].state}
    )
    assert not a[-1].tables["orders"].state.equals(b[-1].tables["orders"].state)


def test_a_different_seed_or_as_of_changes_the_days():
    a = _days(2)
    assert not a[2].tables["orders"].state.equals(_days(2, seed=8)[2].tables["orders"].state)
    moved = generate_days(load(DAILY), 2, base_rows=120, seed=7, as_of=date(2026, 3, 1))
    assert moved[1].tables["orders"].inserted["order_date"].iloc[0].startswith("2026-03-02")


def test_changes_can_redraw_a_non_transition_column_and_keep_its_bounds():
    data = to_dict(load(DAILY))
    data["tables"]["orders"]["incremental"]["changes"] = ["status", "total_amount"]
    days = generate_days(from_dict(data), 3, base_rows=100, seed=1, as_of=AS_OF)
    for before, after in zip(days, days[1:], strict=False):
        old = before.tables["orders"].state.set_index("id")["total_amount"]
        updated = after.tables["orders"].updated
        assert (updated["total_amount"].between(10, 5000)).all()
        assert (updated["total_amount"].to_numpy() != old[updated["id"]].to_numpy()).mean() > 0.8


def test_a_changed_temporal_column_is_set_to_the_day():
    data = to_dict(load(DAILY))
    data["tables"]["orders"]["columns"]["shipped_at"] = {"type": "timestamp"}
    data["tables"]["orders"]["incremental"]["changes"] = ["status", "shipped_at"]
    days = generate_days(from_dict(data), 2, base_rows=100, seed=1, as_of=AS_OF)
    updated = days[1].tables["orders"].updated
    shipped = updated["shipped_at"].dropna()
    assert len(shipped) > 0
    assert {pd.Timestamp(v).date() for v in shipped} == {days[1].date}
    has = updated["shipped_at"].notna()
    assert (
        pd.to_datetime(updated.loc[has, "updated_at"])
        >= pd.to_datetime(updated.loc[has, "shipped_at"])
    ).all()


def test_changes_naming_a_key_is_invalid():
    data = to_dict(load(DAILY))
    data["tables"]["orders"]["incremental"]["changes"] = ["id"]
    with pytest.raises(ModelError, match="keys never change"):
        from_dict(data)
    inputs = to_engine(load(DAILY))
    inputs.incremental["orders"] = Incremental(changes=["id"])
    with pytest.raises(ValueError, match="keys never change"):
        generate_days(inputs, 1, base_rows=20, seed=1, as_of=AS_OF)


@pytest.mark.parametrize(
    ("incremental", "message"),
    [
        (Incremental(changes=["colour"]), "names 'colour', not a column"),
        (Incremental(updated_at="nowhere"), "names 'nowhere', not a column"),
        (Incremental(updated_at="total_amount"), "'total_amount' is not temporal"),
    ],
)
def test_hand_built_incremental_inputs_are_checked(incremental, message):
    inputs = to_engine(load(DAILY))
    inputs.incremental["orders"] = incremental
    with pytest.raises(ValueError, match=message):
        generate_days(inputs, 1, base_rows=20, seed=1, as_of=AS_OF)


def test_as_of_defaults_to_the_models_run():
    data = to_dict(load(DAILY))
    data["run"] = {**data.get("run", {}), "as_of": AS_OF.isoformat()}
    from_run = generate_days(from_dict(data), 2, base_rows=30, seed=7)
    given = generate_days(load(DAILY), 2, base_rows=30, seed=7, as_of=AS_OF)
    assert [_csv(d.state) for d in from_run] == [_csv(d.state) for d in given]


def test_a_unique_foreign_key_runs_out_of_parents_and_says_so():
    data = {
        "model2data": "0.2.0",
        "tables": {
            "people": {"columns": {"id": {"type": "bigint", "pk": True}}},
            "passports": {
                "incremental": {"new_per_day": 10},
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "person_id": {
                        "type": "bigint",
                        "references": {"to": "people.id", "one_to_one": True},
                    },
                },
            },
        },
    }
    days = generate_days(
        from_dict(data), 1, base_rows=12, row_overrides={"passports": 10}, seed=1, as_of=AS_OF
    )
    used = days[0].tables["passports"].state["person_id"].dropna()
    assert len(days[1].tables["passports"].inserted) == 12 - len(used) < 10
    assert days[1].warnings
    assert days[1].tables["passports"].state["person_id"].dropna().is_unique


def test_the_model_roundtrips_with_incremental():
    model = load(DAILY)
    assert model.tables["orders"].incremental == Incremental(
        new_per_day=20, update_rate=0.1, updated_at="updated_at"
    )
    assert load(dump(model)) == model


def test_zero_days_and_negative_days():
    assert len(generate_days(load(DAILY), 0, base_rows=20, seed=1, as_of=AS_OF)) == 1
    with pytest.raises(ValueError):
        generate_days(load(DAILY), -1)


# -- the CLI -----------------------------------------------------------------
runner = CliRunner()


def _run(tmp_path: Path, *args: str):
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(
            app,
            ["--file", str(DAILY), "--rows", "50", "--seed", "4", "--as-of", "2026-01-31", *args],
        )
    finally:
        os.chdir(cwd)


def test_cli_without_days_writes_no_day_files(tmp_path: Path):
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_ecommerce_daily"
    assert not (project / "days").exists() and not (project / "changelog").exists()


def test_cli_day_zero_files_are_the_seeds_of_a_run_without_days(tmp_path: Path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "days").mkdir()
    assert _run(tmp_path / "plain").exit_code == 0
    assert _run(tmp_path / "days", "--days", "2").exit_code == 0
    seeds = tmp_path / "plain" / "dbt_ecommerce_daily" / "seeds" / "raw"
    day_zero = tmp_path / "days" / "dbt_ecommerce_daily" / "days"
    for seed in seeds.glob("*.csv"):
        assert seed.read_bytes() == (day_zero / seed.stem / "day_000.csv").read_bytes()


def test_cli_batches_layout_and_final_seeds(tmp_path: Path):
    result = _run(tmp_path, "--days", "3")
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_ecommerce_daily"
    expected = generate_days(load(DAILY), 3, base_rows=50, seed=4, as_of=AS_OF)
    orders = project / "days" / "orders"
    assert sorted(p.name for p in orders.iterdir()) == [f"day_{n:03d}.csv" for n in range(4)]
    assert not (project / "days" / "products" / "day_001.csv").exists()
    assert (project / "days" / "products" / "day_000.csv").exists()
    day0 = pd.read_csv(orders / "day_000.csv")
    assert len(day0) == 50
    day2 = pd.read_csv(orders / "day_002.csv")
    assert len(day2) == len(expected[2].tables["orders"].inserted) + len(
        expected[2].tables["orders"].updated
    )
    seeds = pd.read_csv(project / "seeds" / "raw" / "orders.csv")
    assert len(seeds) == len(expected[3].tables["orders"].state) == 110
    assert seeds["id"].tolist() == expected[3].tables["orders"].state["id"].tolist()
    assert not list((project / "seeds").rglob("day_*.csv"))


def test_cli_changelog_layout(tmp_path: Path):
    result = _run(tmp_path, "--next", "--days-format", "changelog")
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_ecommerce_daily"
    log = pd.read_csv(project / "changelog" / "orders.csv")
    assert list(log.columns[:2]) == ["_day", "_op"]
    counts = log.groupby(["_day", "_op"]).size().to_dict()
    assert counts == {(0, "insert"): 50, (1, "insert"): 20, (1, "update"): 5}
    products = pd.read_csv(project / "changelog" / "products.csv")
    assert set(products["_day"]) == {0} and set(products["_op"]) == {"insert"}
    assert not (project / "days").exists()
    assert len(pd.read_csv(project / "seeds" / "raw" / "orders.csv")) == 70


def test_cli_final_format_writes_only_the_seeds(tmp_path: Path):
    result = _run(tmp_path, "--days", "2", "--days-format", "final")
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_ecommerce_daily"
    assert not (project / "days").exists() and not (project / "changelog").exists()
    assert len(pd.read_csv(project / "seeds" / "raw" / "orders.csv")) == 90


def test_cli_days_and_next_together_and_a_bad_format_are_refused(tmp_path: Path):
    assert _run(tmp_path, "--days", "2", "--next").exit_code != 0
    assert _run(tmp_path, "--days", "2", "--days-format", "zip").exit_code != 0


def test_cli_days_output_is_reproducible(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _run(tmp_path / "a", "--days", "3")
    _run(tmp_path / "b", "--days", "3")
    first = sorted((tmp_path / "a").rglob("*.csv"))
    assert first
    for path in first:
        other = tmp_path / "b" / path.relative_to(tmp_path / "a")
        assert path.read_bytes() == other.read_bytes()
