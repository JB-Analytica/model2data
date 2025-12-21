import pandas as pd

from model2data.generate.core import _topological_table_order, generate_data_from_dbml
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

    assert children["parent_id"].isin(parents["id"]).all()


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
