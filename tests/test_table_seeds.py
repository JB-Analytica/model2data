"""Re-rolling one table must leave the others exactly where they were.

One RNG stream for the whole run meant a table's values depended on every table
generated before it, so "I like these customers, give me different orders" was
not a thing that could be asked. Each table now draws from its own stream,
derived from the run seed and its own name. These tests hold the property that
buys: change one table's entry in `table_seeds` and only that table -- plus the
foreign keys that have to follow it -- moves.
"""

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from model2data.generate.core import _table_stream_seed, generate_data_from_dbml
from model2data.parse.dbml import ColumnDef, TableDef

# customers <- orders <- order_items, plus a products table connected to
# nothing, so the suite covers a parent, a middle table, a grandchild and a
# bystander in one schema.
REFS = [
    {
        "source_table": "orders",
        "source_column": "customer_id",
        "target_table": "customers",
        "target_column": "id",
    },
    {
        "source_table": "order_items",
        "source_column": "order_id",
        "target_table": "orders",
        "target_column": "id",
    },
]


def _schema() -> dict[str, TableDef]:
    return {
        "customers": TableDef(
            name="customers",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("email", "varchar", {"not null"}),
            ],
        ),
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("customer_id", "int", {"not null"}),
                ColumnDef("total", "numeric", {"not null"}),
            ],
        ),
        "order_items": TableDef(
            name="order_items",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("order_id", "int", {"not null"}),
                ColumnDef("quantity", "int", {"not null"}),
            ],
        ),
        "products": TableDef(
            name="products",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("title", "varchar", {"not null"}),
            ],
        ),
    }


def _generate(**kwargs) -> dict[str, pd.DataFrame]:
    return generate_data_from_dbml(_schema(), REFS, base_rows=40, seed=11, **kwargs)


class TestReRollingOneTable:
    def test_only_the_named_table_and_its_descendants_move(self):
        baseline = _generate()
        rolled = _generate(table_seeds={"orders": 7})

        assert not rolled["orders"].equals(baseline["orders"]), (
            "the whole point of the override is that this table changes"
        )
        assert_frame_equal(rolled["customers"], baseline["customers"])
        assert_frame_equal(rolled["products"], baseline["products"])

    def test_a_child_of_an_untouched_table_is_byte_identical(self):
        """Re-rolling `products` reaches nothing: `orders` and its child stay put.

        `order_items` is the interesting one -- it is two hops downstream of a
        table nobody touched, and under a single shared stream it would have
        moved anyway simply for being generated later in the run.
        """
        baseline = _generate()
        rolled = _generate(table_seeds={"products": 5})

        assert not rolled["products"].equals(baseline["products"])
        assert_frame_equal(rolled["customers"], baseline["customers"])
        assert_frame_equal(rolled["orders"], baseline["orders"])
        assert_frame_equal(rolled["order_items"], baseline["order_items"])

    def test_a_childs_own_columns_survive_its_parent_being_re_rolled(self):
        """Only the FK column of a child may follow its parent."""
        baseline = _generate()
        rolled = _generate(table_seeds={"orders": 7})

        assert_frame_equal(
            rolled["order_items"].drop(columns=["order_id"]),
            baseline["order_items"].drop(columns=["order_id"]),
        )

    def test_referential_integrity_holds_against_the_new_parent(self):
        rolled = _generate(table_seeds={"orders": 7})

        assert set(rolled["orders"]["customer_id"]) <= set(rolled["customers"]["id"])
        assert set(rolled["order_items"]["order_id"]) <= set(rolled["orders"]["id"])

    def test_the_old_parent_rows_are_genuinely_gone(self):
        """A child that still pointed at the pre-roll ids would be dangling."""
        baseline = _generate()
        rolled = _generate(table_seeds={"orders": 7})

        stale = set(baseline["orders"]["id"]) - set(rolled["orders"]["id"])
        assert stale, "the re-rolled parent has to hand out at least some new ids"
        assert not set(rolled["order_items"]["order_id"]) & stale

    def test_two_tables_can_be_re_rolled_at_once(self):
        baseline = _generate()
        rolled = _generate(table_seeds={"customers": 2, "products": 5})

        assert not rolled["customers"].equals(baseline["customers"])
        assert not rolled["products"].equals(baseline["products"])

    def test_re_rolling_is_itself_reproducible(self):
        first = _generate(table_seeds={"orders": 7})
        second = _generate(table_seeds={"orders": 7})

        for name in first:
            assert_frame_equal(first[name], second[name])

    def test_a_different_override_value_gives_a_different_table(self):
        seven = _generate(table_seeds={"orders": 7})
        eight = _generate(table_seeds={"orders": 8})

        assert not seven["orders"].equals(eight["orders"])
        assert_frame_equal(seven["customers"], eight["customers"])

    def test_an_empty_mapping_changes_nothing(self):
        assert_frame_equal(_generate(table_seeds={})["orders"], _generate()["orders"])


class TestValidation:
    def test_an_unknown_table_name_is_an_error(self):
        with pytest.raises(ValueError, match="No table named 'ordres'"):
            _generate(table_seeds={"ordres": 7})

    def test_the_message_lists_the_tables_that_do_exist(self):
        with pytest.raises(ValueError, match="customers, order_items, orders, products"):
            _generate(table_seeds={"nope": 1})

    def test_several_unknown_names_are_reported_together(self):
        with pytest.raises(ValueError, match="No tables named 'a', 'b'"):
            _generate(table_seeds={"b": 1, "a": 2})

    def test_it_needs_a_seed_to_re_roll_out_of(self):
        with pytest.raises(ValueError, match="table_seeds needs a seed"):
            generate_data_from_dbml(
                _schema(), REFS, base_rows=10, seed=None, table_seeds={"orders": 7}
            )

    def test_no_seed_and_no_overrides_is_still_fine(self):
        frames = generate_data_from_dbml(_schema(), REFS, base_rows=10, seed=None)

        assert len(frames["orders"]) == 10

    def test_validation_runs_before_anything_is_generated(self):
        """A typo should not cost a full generation pass first."""
        with pytest.raises(ValueError):
            generate_data_from_dbml(
                _schema(), REFS, base_rows=2_000_000, seed=1, table_seeds={"typo": 1}
            )


class TestTheDerivedSeed:
    def test_it_is_stable_across_processes(self):
        """Pinned literally: `hash()` is salted per process and would not be."""
        assert _table_stream_seed(11, "orders", None) == 11237587294246756286
        assert _table_stream_seed(11, "orders", 7) == 9635424189123890928

    def test_each_table_gets_a_different_stream(self):
        assert _table_stream_seed(11, "orders", None) != _table_stream_seed(11, "customers", None)

    def test_each_run_seed_gets_a_different_stream(self):
        assert _table_stream_seed(11, "orders", None) != _table_stream_seed(12, "orders", None)

    def test_no_override_is_not_the_same_as_an_override_of_zero(self):
        assert _table_stream_seed(11, "orders", None) != _table_stream_seed(11, "orders", 0)


class TestOtherOptionsStillCompose:
    def test_row_counts_are_untouched_by_a_re_roll(self):
        rolled = _generate(row_overrides={"orders": 120}, table_seeds={"orders": 7})

        assert len(rolled["orders"]) == 120
        assert len(rolled["customers"]) == 40

    def test_a_pinned_anchor_and_a_re_roll_work_together(self):
        from datetime import date

        tables = {
            "events": TableDef(
                name="events",
                columns=[
                    ColumnDef("id", "int", {"pk"}),
                    ColumnDef("happened_on", "date", {"not null"}),
                ],
            )
        }
        kwargs = {"base_rows": 20, "seed": 4, "as_of": date(2024, 3, 15)}

        first = generate_data_from_dbml(tables, [], table_seeds={"events": 1}, **kwargs)["events"]
        second = generate_data_from_dbml(tables, [], table_seeds={"events": 1}, **kwargs)["events"]

        assert_frame_equal(first, second)
        assert first["happened_on"].max() <= date(2024, 3, 15)
