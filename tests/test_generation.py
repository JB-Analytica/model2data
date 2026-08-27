import pandas as pd

from model2data.generate.core import (
    _topological_table_order,
    generate_data_from_dbml,
    get_cyclic_tables,
)
from model2data.parse.dbml import ColumnDef, TableDef, parse_dbml


def test_generation_is_deterministic_with_seed():
    tables = {
        "users": TableDef(
            name="users",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("name", "varchar"),
            ],
        )
    }

    refs = []

    data1 = generate_data_from_dbml(tables, refs, base_rows=10, seed=123)
    data2 = generate_data_from_dbml(tables, refs, base_rows=10, seed=123)

    assert data1["users"].equals(data2["users"])


def test_parent_child_fk_generation_and_ordering():
    tables = {
        "parents": TableDef(
            name="parents",
            columns=[ColumnDef("id", "int", {"pk"})],
        ),
        "children": TableDef(
            name="children",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        ),
    }

    refs = [
        {
            "source_table": "children",
            "source_column": "parent_id",
            "target_table": "parents",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=20, seed=1)

    children = data["children"]
    parents = data["parents"]

    # parent_id is nullable (no `not null`/`pk`), so some rows can
    # legitimately come back with no parent -- only non-null values need to
    # resolve to a real parent row.
    assert children["parent_id"].dropna().isin(parents["id"]).all()


def test_attribute_reference_mirroring():
    tables = {
        "users": TableDef(
            name="users",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("country", "varchar"),
            ],
        ),
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("user_id", "int"),
                ColumnDef("user_country", "varchar"),
            ],
        ),
    }

    refs = [
        # FK ref
        {
            "source_table": "orders",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        },
        # Attribute ref
        {
            "source_table": "orders",
            "source_column": "user_country",
            "target_table": "users",
            "target_column": "country",
        },
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=30, seed=7)

    orders = data["orders"]
    users = data["users"]

    lookup = users.groupby("id")["country"].first().to_dict()
    expected = orders["user_id"].map(lookup)

    assert orders["user_country"].equals(expected)


def test_fk_target_column_missing_is_ignored():
    tables = {
        "parent": TableDef(
            name="parent",
            columns=[ColumnDef("id", "int", {"pk"})],
        ),
        "child": TableDef(
            name="child",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        ),
    }

    # FK points to NON-existing column
    refs = [
        {
            "source_table": "child",
            "source_column": "parent_id",
            "target_table": "parent",
            "target_column": "missing_col",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=10, seed=1)

    assert "child" in data
    assert "parent_id" in data["child"].columns


def test_attribute_ref_without_fk_is_skipped():
    tables = {
        "parent": TableDef(
            name="parent",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("name", "varchar"),
            ],
        ),
        "child": TableDef(
            name="child",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_name", "varchar"),
            ],
        ),
    }

    # Attribute ref, but NO FK ref exists
    refs = [
        {
            "source_table": "child",
            "source_column": "parent_name",
            "target_table": "parent",
            "target_column": "name",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=10, seed=2)

    # Column exists but is NOT mirrored
    assert "parent_name" in data["child"].columns


def test_ref_with_reverse_operator_is_normalized(tmp_path):
    dbml = tmp_path / "reverse_ref.dbml"
    dbml.write_text(
        """
        Table parent {
          id int
        }

        Table child {
          id int
          parent_id int
        }

        Ref {
          parent.id < child.parent_id
        }
        """
    )

    tables, refs = parse_dbml(dbml)

    assert len(refs) == 1
    ref = refs[0]

    assert ref["source_table"] == "child"
    assert ref["source_column"] == "parent_id"
    assert ref["target_table"] == "parent"
    assert ref["target_column"] == "id"


def test_inline_ref_produces_fk_aware_values(tmp_path):
    """A column-level `[ref: > table.column]` FK gets FK-aware generation,
    the same way a standalone `Ref {}` block does."""
    dbml = tmp_path / "inline_ref.dbml"
    dbml.write_text(
        """
        Table users {
          id int [pk]
        }

        Table orders {
          id int [pk]
          user_id int [ref: > users.id]
        }
        """
    )

    tables, refs = parse_dbml(dbml)
    data = generate_data_from_dbml(tables, refs, base_rows=20, seed=3)

    orders = data["orders"]
    users = data["users"]

    # user_id is nullable here, so only non-null values need to resolve.
    assert orders["user_id"].dropna().isin(users["id"]).all()


def test_disconnected_tables_are_generated():
    tables = {
        "a": TableDef("a", [ColumnDef("id", "int", {"pk"})]),
        "b": TableDef("b", [ColumnDef("id", "int", {"pk"})]),
    }

    refs = []

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=0)

    assert set(data.keys()) == {"a", "b"}
    assert len(data["a"]) == 5
    assert len(data["b"]) == 5


def test_self_referencing_fk_does_not_break_generation():
    tables = {
        "categories": TableDef(
            name="categories",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        )
    }

    refs = [
        {
            "source_table": "categories",
            "source_column": "parent_id",
            "target_table": "categories",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=10, seed=5)

    assert "categories" in data
    assert len(data["categories"]) == 10


def test_self_referencing_fk_values_resolve_to_own_pk_column():
    """Regression test: a self-referencing FK (e.g. employees.manager_id ->
    employees.id) used to fall through to unrelated random values because the
    table's own DataFrame wasn't in `generated` yet while it was being built.
    Every non-null value in the FK column must now exist in the table's own
    PK column."""
    tables = {
        "employees": TableDef(
            name="employees",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("manager_id", "int"),
            ],
        )
    }

    refs = [
        {
            "source_table": "employees",
            "source_column": "manager_id",
            "target_table": "employees",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=150, seed=42)
    df = data["employees"]

    valid_ids = set(df["id"])
    manager_ids = df["manager_id"].dropna()

    assert not manager_ids.empty
    assert set(manager_ids).issubset(valid_ids)


def test_missing_parent_table_in_refs_is_ignored():
    tables = {
        "child": TableDef(
            name="child",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        )
    }

    refs = [
        {
            "source_table": "child",
            "source_column": "parent_id",
            "target_table": "missing_parent",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=10, seed=3)

    assert "child" in data
    assert len(data["child"]) == 10
    assert "parent_id" in data["child"].columns


def test_table_with_no_columns_is_handled():
    tables = {
        "empty": TableDef(name="empty", columns=[]),
    }

    refs = []

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=1)

    df = data["empty"]
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_fk_lookup_key_mismatch_is_ignored():
    tables = {
        "parent": TableDef(
            name="parent",
            columns=[ColumnDef("id", "int", {"pk"})],
        ),
        "child": TableDef(
            name="child",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        ),
    }

    # FK refers to a DIFFERENT column name than exists
    refs = [
        {
            "source_table": "child",
            "source_column": "wrong_column",
            "target_table": "parent",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=1)

    assert "child" in data
    assert "parent_id" in data["child"].columns


def test_fk_parent_column_missing_is_skipped():
    tables = {
        "parent": TableDef(
            name="parent",
            columns=[ColumnDef("id", "int", {"pk"})],
        ),
        "child": TableDef(
            name="child",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int"),
            ],
        ),
    }

    refs = [
        {
            "source_table": "child",
            "source_column": "parent_id",
            "target_table": "parent",
            "target_column": "missing_column",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=2)

    # parent_id still generated, but no FK applied
    assert "parent_id" in data["child"].columns


def test_duplicate_fk_refs_do_not_double_count_indegree():
    tables = {
        "parent": TableDef("parent", [ColumnDef("id", "int", {"pk"})]),
        "child": TableDef("child", [ColumnDef("id", "int", {"pk"})]),
    }

    refs = [
        {
            "source_table": "child",
            "source_column": "id",
            "target_table": "parent",
            "target_column": "id",
        },
        {
            # duplicate edge
            "source_table": "child",
            "source_column": "id",
            "target_table": "parent",
            "target_column": "id",
        },
    ]

    order = _topological_table_order(tables, refs)

    assert order.index("parent") < order.index("child")


def test_composite_unique_key_deduplicates_generated_rows():
    # Small value space (5 x 5 = 25 combos) with 20 rows requested forces
    # collisions that the composite-key dedup pass must resolve.
    tables = {
        "pairs": TableDef(
            name="pairs",
            columns=[
                ColumnDef("a", "int", {"not null"}, note={"min": 0, "max": 4}),
                ColumnDef("b", "int", {"not null"}, note={"min": 0, "max": 4}),
            ],
            composite_keys=[{"columns": ["a", "b"], "type": "unique"}],
        )
    }

    df = generate_data_from_dbml(tables, [], base_rows=20, seed=7)["pairs"]

    combos = list(zip(df["a"], df["b"], strict=True))
    assert len(combos) == len(set(combos)) == 20


def test_composite_pk_key_deduplicates_generated_rows():
    tables = {
        "assignments": TableDef(
            name="assignments",
            columns=[
                ColumnDef("project_id", "int", {"not null"}, note={"min": 0, "max": 5}),
                ColumnDef("employee_id", "int", {"not null"}, note={"min": 0, "max": 5}),
            ],
            composite_keys=[{"columns": ["project_id", "employee_id"], "type": "pk"}],
        )
    }

    df = generate_data_from_dbml(tables, [], base_rows=15, seed=3)["assignments"]

    combos = list(zip(df["project_id"], df["employee_id"], strict=True))
    assert len(combos) == len(set(combos)) == 15


def test_unique_non_pk_column_is_actually_deduplicated():
    # Regression test: a `[unique]` column gets exactly the same dbt
    # `unique` schema test as a `[pk]` column (see dbt/tests.py), but
    # generation only ever passed `ensure_unique=True` for `pk` columns --
    # so a "unique" column (e.g. a promo code, VIN, or email) had no actual
    # uniqueness guarantee and could non-deterministically fail its own
    # generated dbt test on a chance collision in the fallback-text
    # generator's small effective value space.
    tables = {
        "promotions": TableDef(
            name="promotions",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                # note forces a range small enough that a collision is
                # near-certain across 50 rows if not deduplicated, but still
                # comfortably large enough (201 possible values) that 50
                # unique values are actually achievable.
                # "not null" keeps this test focused purely on the
                # uniqueness guarantee (dbt's own `unique` test already
                # excludes nulls, same reasoning as `.dropna()` would give).
                ColumnDef("code", "int", {"unique", "not null"}, note={"min": 0, "max": 200}),
            ],
        )
    }

    df = generate_data_from_dbml(tables, [], base_rows=50, seed=1)["promotions"]

    codes = df["code"].tolist()
    assert len(codes) == len(set(codes))


def test_nullable_fk_column_can_actually_be_null():
    # Regression test: generate_column_values used to early-return as soon
    # as an fk_series was supplied, before the nullability pass at the
    # bottom of the function ever ran. That meant a nullable FK column
    # (no `not null`/`pk`) could never come back null, no matter how many
    # rows were generated -- contradicting the documented behavior (LLMS.md:
    # a self-referencing FK "can still have no parent") and, more broadly,
    # any ordinary nullable FK (e.g. an order with no customer).
    tables = {
        "customers": TableDef(name="customers", columns=[ColumnDef("id", "int", {"pk"})]),
        "orders": TableDef(
            name="orders",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("customer_id", "int")],
        ),
    }
    refs = [
        {
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=300, seed=1)

    customer_ids = data["orders"]["customer_id"]
    assert customer_ids.isna().any()
    assert customer_ids.dropna().isin(data["customers"]["id"]).all()


def test_not_null_fk_column_is_never_null():
    tables = {
        "customers": TableDef(name="customers", columns=[ColumnDef("id", "int", {"pk"})]),
        "orders": TableDef(
            name="orders",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("customer_id", "int", {"not null"})],
        ),
    }
    refs = [
        {
            "source_table": "orders",
            "source_column": "customer_id",
            "target_table": "customers",
            "target_column": "id",
        }
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=300, seed=1)

    assert not data["orders"]["customer_id"].isna().any()


def test_enum_column_whose_name_contains_int_does_not_crash_dtype_coercion():
    # Regression test: an enum's *name* becomes the column's declared
    # data_type in DBML (e.g. `status maintenance_type`), and that name can
    # innocently contain "int" as a substring ("ma-int-enance_type").
    # _coerce_integer_dtypes used to substring-match on the raw type string
    # with no enum guard, so it mistook the column for an integer column and
    # crashed trying to cast its real string values ("repair", "cleaning",
    # ...) to Int64.
    tables = {
        "maintenance_records": TableDef(
            name="maintenance_records",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef(
                    "maintenance_type",
                    "maintenance_type",
                    {"not null"},
                    enum_values=["oil_change", "repair", "cleaning"],
                ),
            ],
        )
    }

    df = generate_data_from_dbml(tables, [], base_rows=10, seed=1)["maintenance_records"]

    assert set(df["maintenance_type"]) <= {"oil_change", "repair", "cleaning"}


def test_composite_key_dedup_retry_preserves_fk_validity(monkeypatch):
    # Regression test: a join/bridge table's composite key is almost always
    # built from FK columns (posts/tags -> post_tags is the canonical
    # example). When two parent tables are small relative to the bridge
    # table's row count, the dedup retry loop fires constantly -- and it
    # used to regenerate colliding FK columns via the column's own
    # type-based generator instead of resampling from the real parent id
    # pool, silently producing post_id/tag_id values that referenced no
    # real parent row at all.
    import model2data.generate.core as core

    tables = {
        "posts": TableDef(name="posts", columns=[ColumnDef("id", "int", {"pk"})]),
        "tags": TableDef(name="tags", columns=[ColumnDef("id", "int", {"pk"})]),
        "post_tags": TableDef(
            name="post_tags",
            columns=[
                ColumnDef("post_id", "int", {"not null"}),
                ColumnDef("tag_id", "int", {"not null"}),
            ],
            composite_keys=[{"columns": ["post_id", "tag_id"], "type": "pk"}],
        ),
    }
    refs = [
        {
            "source_table": "post_tags",
            "source_column": "post_id",
            "target_table": "posts",
            "target_column": "id",
        },
        {
            "source_table": "post_tags",
            "source_column": "tag_id",
            "target_table": "tags",
            "target_column": "id",
        },
    ]

    # Force posts/tags to 5 rows each (25 possible combos) while post_tags
    # gets 200 rows, guaranteeing heavy dedup-retry activity.
    def small_parents_large_bridge(table_name, base_rows):
        return 5 if table_name in ("posts", "tags") else base_rows

    monkeypatch.setattr(core, "_determine_row_count", small_parents_large_bridge)

    data = generate_data_from_dbml(tables, refs, base_rows=200, seed=123)

    post_ids = set(data["posts"]["id"].tolist())
    tag_ids = set(data["tags"]["id"].tolist())
    post_tags = data["post_tags"]

    assert set(post_tags["post_id"].tolist()) <= post_ids
    assert set(post_tags["tag_id"].tolist()) <= tag_ids


def test_composite_key_with_unsupported_type_is_ignored():
    tables = {
        "pairs": TableDef(
            name="pairs",
            columns=[ColumnDef("a", "int"), ColumnDef("b", "int")],
            composite_keys=[{"columns": ["a", "b"], "type": "index"}],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=10, seed=1)["pairs"]
    assert len(df) == 10


def test_composite_key_referencing_missing_column_is_ignored():
    tables = {
        "pairs": TableDef(
            name="pairs",
            columns=[ColumnDef("a", "int"), ColumnDef("b", "int")],
            composite_keys=[{"columns": ["a", "does_not_exist"], "type": "unique"}],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=10, seed=1)["pairs"]
    assert len(df) == 10


def test_no_composite_keys_leaves_generation_unaffected():
    tables = {
        "users": TableDef(
            name="users",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("name", "varchar")],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=10, seed=1)["users"]
    assert len(df) == 10


def test_two_table_fk_cycle_completes_and_is_flagged():
    """A → B and B → A is a genuine structural cycle: neither table's
    indegree ever reaches 0, so the old 'safety net' catches both silently.
    Generation must still complete (not crash/hang), and the new
    get_cyclic_tables() helper must report both tables.
    """
    tables = {
        "a": TableDef(
            name="a",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("b_id", "int")],
        ),
        "b": TableDef(
            name="b",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("a_id", "int")],
        ),
    }
    refs = [
        {"source_table": "a", "source_column": "b_id", "target_table": "b", "target_column": "id"},
        {"source_table": "b", "source_column": "a_id", "target_table": "a", "target_column": "id"},
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=0)

    assert set(data.keys()) == {"a", "b"}
    assert len(data["a"]) == 5
    assert len(data["b"]) == 5
    assert set(get_cyclic_tables()) == {"a", "b"}


def test_three_table_fk_cycle_is_flagged():
    tables = {
        "a": TableDef(name="a", columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("c_id", "int")]),
        "b": TableDef(name="b", columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("a_id", "int")]),
        "c": TableDef(name="c", columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("b_id", "int")]),
    }
    refs = [
        {"source_table": "a", "source_column": "c_id", "target_table": "c", "target_column": "id"},
        {"source_table": "b", "source_column": "a_id", "target_table": "a", "target_column": "id"},
        {"source_table": "c", "source_column": "b_id", "target_table": "b", "target_column": "id"},
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=0)

    assert set(data.keys()) == {"a", "b", "c"}
    assert set(get_cyclic_tables()) == {"a", "b", "c"}


def test_composite_ref_produces_fk_aware_values_for_both_columns():
    """A composite Ref expands into two independent single-column refs (see
    parse.dbml's _COMPOSITE_REF_RE handling), so each child column should
    independently reference real values in its own target column -- even
    though the *combination* of the two child columns isn't guaranteed to
    match a real combination in the parent (documented limitation).
    """
    tables = {
        "order_variants": TableDef(
            name="order_variants",
            columns=[ColumnDef("order_id", "int", {"pk"}), ColumnDef("variant_id", "int", {"pk"})],
        ),
        "order_items": TableDef(
            name="order_items",
            columns=[ColumnDef("order_id", "int"), ColumnDef("variant_id", "int")],
        ),
    }
    refs = [
        {
            "source_table": "order_items",
            "source_column": "order_id",
            "target_table": "order_variants",
            "target_column": "order_id",
        },
        {
            "source_table": "order_items",
            "source_column": "variant_id",
            "target_table": "order_variants",
            "target_column": "variant_id",
        },
    ]

    data = generate_data_from_dbml(tables, refs, base_rows=10, seed=1)

    parent_order_ids = set(data["order_variants"]["order_id"])
    parent_variant_ids = set(data["order_variants"]["variant_id"])
    # Both FK columns are nullable here, so only non-null values need to
    # resolve to a real parent row.
    assert set(data["order_items"]["order_id"].dropna()).issubset(parent_order_ids)
    assert set(data["order_items"]["variant_id"].dropna()).issubset(parent_variant_ids)


def test_disconnected_table_does_not_trigger_cycle_warning():
    tables = {
        "a": TableDef("a", [ColumnDef("id", "int", {"pk"})]),
        "b": TableDef("b", [ColumnDef("id", "int", {"pk"})]),
    }

    generate_data_from_dbml(tables, [], base_rows=5, seed=0)

    assert get_cyclic_tables() == []
