"""Volume and distribution shaping: FK skew and the null_rate/weights/true_rate/
distinct column-note hints.

Every statistical test here uses a fixed seed and a generous tolerance so it
never flakes; the exact bounds were chosen by running the real implementation
and leaving headroom, not derived analytically.
"""

import os

import pandas as pd
import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import ColumnDef, TableDef, parse_dbml

runner = CliRunner()


# ---------------------------------------------------------
# Step 0: pin today's behaviour before touching it
# ---------------------------------------------------------
def _users_orders_schema(parent_id_note=None):
    return {
        "users": TableDef(
            name="users",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("email", "email", {"unique"}),
            ],
        ),
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("user_id", "int", {"not null"}, note=parent_id_note),
                ColumnDef(
                    "status",
                    "order_status",
                    set(),
                    enum_values=["pending", "shipped", "delivered", "cancelled"],
                ),
                ColumnDef("is_paid", "boolean"),
                ColumnDef("total", "numeric"),
                ColumnDef("note", "varchar"),
            ],
        ),
    }


_USERS_ORDERS_REFS = [
    {
        "source_table": "orders",
        "source_column": "user_id",
        "target_table": "users",
        "target_column": "id",
    }
]


def test_uniform_generation_reproduces_pre_1_5_frames():
    """A run that passes no shaping option at all must still produce exactly
    what 1.4.0 produced with the same seed -- this is the guard rail every
    later change in this module works against."""
    data = generate_data_from_dbml(
        _users_orders_schema(), _USERS_ORDERS_REFS, base_rows=12, seed=2024
    )
    orders = data["orders"]

    assert orders["id"].tolist() == [67, 7, 12, 23, 83, 48, 85, 42, 74, 72, 13, 87]
    assert orders["user_id"].tolist() == [41, 82, 31, 21, 48, 39, 99, 82, 21, 0, 48, 41]
    assert orders["status"].tolist() == [
        "delivered",
        "cancelled",
        "pending",
        "delivered",
        "cancelled",
        "delivered",
        "pending",
        "cancelled",
        "pending",
        "pending",
        "pending",
        "shipped",
    ]
    assert orders["is_paid"].tolist() == [
        False,
        True,
        None,
        False,
        False,
        None,
        False,
        True,
        True,
        False,
        False,
        False,
    ]
    assert pd.isna(orders["total"].tolist()[1])
    assert pd.isna(orders["total"].tolist()[9])
    non_null_totals = [v for v in orders["total"].tolist() if not pd.isna(v)]
    assert non_null_totals == [
        2435.11,
        1746.91,
        370.85,
        1610.47,
        6398.42,
        1936.29,
        2677.74,
        4708.56,
        3946.17,
        1678.0,
    ]
    assert orders["note"].tolist() == [
        "Argue we pretty.",
        "One big indicate.",
        None,
        "Raise west hotel.",
        "Agreement pattern yet.",
        "Radio trip administration.",
        "Machine tell big.",
        "Sort audience during.",
        "More song education.",
        "Probably student.",
        None,
        "Company also.",
    ]


# ---------------------------------------------------------
# Feature 1: skew on foreign-key columns
# ---------------------------------------------------------
def _parents_children_schema(row_skew_hint=None):
    return {
        "parents": TableDef(name="parents", columns=[ColumnDef("id", "int", {"pk"})]),
        "children": TableDef(
            name="children",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int", {"not null"}, note=row_skew_hint),
            ],
        ),
    }


_PARENT_CHILD_REFS = [
    {
        "source_table": "children",
        "source_column": "parent_id",
        "target_table": "parents",
        "target_column": "id",
    }
]

_PARENT_CHILD_ROWS = {"parents": 20, "children": 5000}


def _top_20_share(children: pd.DataFrame) -> float:
    """Share of child rows held by the top 20% of *all* parents (20 of them),
    not just the ones that happened to get at least one child -- a heavily
    skewed draw can easily leave a couple of parents with zero children, and
    that must shrink the denominator here, not the size of "the top 20%"."""
    n_top = max(1, _PARENT_CHILD_ROWS["parents"] // 5)
    counts = children["parent_id"].value_counts().sort_values(ascending=False)
    return counts.iloc[:n_top].sum() / counts.sum()


def test_skew_zero_matches_the_pinned_uniform_frame():
    bare = generate_data_from_dbml(
        _users_orders_schema(), _USERS_ORDERS_REFS, base_rows=12, seed=2024
    )
    explicit = generate_data_from_dbml(
        _users_orders_schema(), _USERS_ORDERS_REFS, base_rows=12, seed=2024, skew=0.0
    )
    assert bare["orders"].equals(explicit["orders"])


@pytest.mark.parametrize(
    "skew, low, high",
    [
        (0.0, 0.0, 0.35),
        (0.8, 0.55, 0.85),
        (1.0, 0.75, 0.95),
    ],
)
def test_skew_concentrates_children_over_the_top_parents(skew, low, high):
    data = generate_data_from_dbml(
        _parents_children_schema(),
        _PARENT_CHILD_REFS,
        base_rows=1,
        seed=99,
        skew=skew,
        row_overrides=_PARENT_CHILD_ROWS,
    )
    share = _top_20_share(data["children"])
    assert low <= share <= high


def test_skew_one_is_at_least_as_concentrated_as_skew_point_eight():
    def share_for(skew):
        data = generate_data_from_dbml(
            _parents_children_schema(),
            _PARENT_CHILD_REFS,
            base_rows=1,
            seed=99,
            skew=skew,
            row_overrides=_PARENT_CHILD_ROWS,
        )
        return _top_20_share(data["children"])

    assert share_for(1.0) >= share_for(0.8)


def test_column_skew_hint_overrides_the_run_level_skew():
    # Run-level skew off, column hint cranked up: still concentrated.
    hinted = generate_data_from_dbml(
        _parents_children_schema(row_skew_hint={"skew": 1.0}),
        _PARENT_CHILD_REFS,
        base_rows=1,
        seed=99,
        skew=0.0,
        row_overrides=_PARENT_CHILD_ROWS,
    )
    assert _top_20_share(hinted["children"]) >= 0.55

    # Run-level skew cranked up, column hint off: back to uniform.
    overridden_off = generate_data_from_dbml(
        _parents_children_schema(row_skew_hint={"skew": 0.0}),
        _PARENT_CHILD_REFS,
        base_rows=1,
        seed=99,
        skew=1.0,
        row_overrides=_PARENT_CHILD_ROWS,
    )
    assert _top_20_share(overridden_off["children"]) <= 0.35


def test_skew_is_deterministic_under_the_same_seed():
    first = generate_data_from_dbml(
        _parents_children_schema(),
        _PARENT_CHILD_REFS,
        base_rows=1,
        seed=99,
        skew=0.8,
        row_overrides=_PARENT_CHILD_ROWS,
    )
    second = generate_data_from_dbml(
        _parents_children_schema(),
        _PARENT_CHILD_REFS,
        base_rows=1,
        seed=99,
        skew=0.8,
        row_overrides=_PARENT_CHILD_ROWS,
    )
    assert first["children"].equals(second["children"])


# ---------------------------------------------------------
# Feature 2: column note hints
# ---------------------------------------------------------
def _single_column_table(column: ColumnDef, table_name: str = "t") -> dict[str, TableDef]:
    return {table_name: TableDef(name=table_name, columns=[ColumnDef("id", "int", {"pk"}), column])}


def test_null_rate_hint_replaces_the_default_null_fraction():
    tables = _single_column_table(ColumnDef("label", "varchar", note={"null_rate": 0.6}))
    data = generate_data_from_dbml(tables, [], base_rows=1000, seed=1)
    null_share = data["t"]["label"].isna().mean()
    assert 0.5 <= null_share <= 0.7


def test_null_rate_zero_means_no_nulls():
    tables = _single_column_table(ColumnDef("label", "varchar", note={"null_rate": 0.0}))
    data = generate_data_from_dbml(tables, [], base_rows=200, seed=1)
    assert data["t"]["label"].notna().all()


def test_null_rate_fills_the_declared_default():
    tables = _single_column_table(
        ColumnDef("label", "varchar", note={"null_rate": 1.0}, default="n/a")
    )
    data = generate_data_from_dbml(tables, [], base_rows=50, seed=1)
    assert (data["t"]["label"] == "n/a").all()


def test_weights_hint_shapes_the_enum_distribution():
    tables = _single_column_table(
        ColumnDef(
            "status",
            "order_status",
            note={"weights": {"delivered": 20}},
            enum_values=["pending", "shipped", "delivered", "cancelled"],
        )
    )
    data = generate_data_from_dbml(tables, [], base_rows=5000, seed=1)
    counts = data["t"]["status"].value_counts(normalize=True)

    # delivered: weight 20 against 1+1+1 for the rest -> 20/23.
    assert abs(counts["delivered"] - 20 / 23) <= 0.08
    # Every value the hint didn't mention still shows up.
    assert set(counts.index) == {"pending", "shipped", "delivered", "cancelled"}


def test_weights_hint_rejects_an_unknown_enum_value():
    tables = _single_column_table(
        ColumnDef(
            "status",
            "order_status",
            note={"weights": {"delivred": 20}},
            enum_values=["pending", "shipped", "delivered", "cancelled"],
        )
    )
    with pytest.raises(ValueError, match=r"t\.status"):
        generate_data_from_dbml(tables, [], base_rows=10, seed=1)


def test_true_rate_hint_shapes_the_boolean_distribution():
    tables = _single_column_table(
        ColumnDef("is_paid", "boolean", {"not null"}, note={"true_rate": 0.9})
    )
    data = generate_data_from_dbml(tables, [], base_rows=2000, seed=1)
    values = data["t"]["is_paid"].tolist()
    true_share = sum(values) / len(values)
    assert 0.82 <= true_share <= 0.98


def test_distinct_hint_caps_the_pool_size():
    tables = _single_column_table(ColumnDef("city", "varchar", {"not null"}, note={"distinct": 12}))
    data = generate_data_from_dbml(tables, [], base_rows=500, seed=1)
    values = data["t"]["city"]
    assert values.notna().all()
    assert values.nunique() <= 12


# ---------------------------------------------------------
# Contradictions raise, plain text is ignored
# ---------------------------------------------------------
def test_null_rate_on_a_not_null_column_is_rejected():
    tables = _single_column_table(
        ColumnDef("label", "varchar", {"not null"}, note={"null_rate": 0.5})
    )
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_null_rate_on_a_pk_column_is_rejected():
    tables = {
        "t": TableDef(
            name="t",
            columns=[ColumnDef("id", "int", {"pk"}, note={"null_rate": 0.5})],
        )
    }
    with pytest.raises(ValueError, match=r"t\.id"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_weights_on_a_non_enum_column_is_rejected():
    tables = _single_column_table(ColumnDef("label", "varchar", note={"weights": {"a": 2}}))
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_true_rate_on_a_non_boolean_column_is_rejected():
    tables = _single_column_table(ColumnDef("label", "varchar", note={"true_rate": 0.5}))
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


@pytest.mark.parametrize(
    "column",
    [
        ColumnDef("ref_id", "int", {"pk"}, note={"distinct": 3}),
        ColumnDef("ref_id", "int", {"unique"}, note={"distinct": 3}),
        ColumnDef("status", "status_enum", note={"distinct": 3}, enum_values=["a", "b"]),
    ],
)
def test_distinct_on_pk_unique_or_enum_column_is_rejected(column):
    tables = _single_column_table(column)
    with pytest.raises(ValueError, match=rf"t\.{column.name}"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_distinct_on_a_foreign_key_column_is_rejected():
    tables = {
        "parents": TableDef(name="parents", columns=[ColumnDef("id", "int", {"pk"})]),
        "children": TableDef(
            name="children",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("parent_id", "int", note={"distinct": 3}),
            ],
        ),
    }
    with pytest.raises(ValueError, match=r"children\.parent_id"):
        generate_data_from_dbml(tables, _PARENT_CHILD_REFS, base_rows=5, seed=1)


def test_skew_on_a_non_fk_column_is_rejected():
    tables = _single_column_table(ColumnDef("label", "int", note={"skew": 0.5}))
    with pytest.raises(ValueError, match=r"t\.label"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_after_naming_a_non_temporal_column_is_rejected():
    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("label", "varchar"),
                ColumnDef("happened_at", "timestamp", note={"after": "label"}),
            ],
        )
    }
    with pytest.raises(ValueError, match=r"events\.happened_at"):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1)


def test_plain_text_notes_are_ignored(tmp_path):
    """A note that fails to parse as JSON is kept as a plain-text description
    by the DBML parser, and `note` itself stays None -- exactly like a column
    with no note at all -- so it can never trip a hint contradiction."""
    dbml_file = tmp_path / "schema.dbml"
    dbml_file.write_text(
        """
        Table t {
            id int [pk, note: 'not a null_rate in sight']
        }
        """
    )
    tables, refs = parse_dbml(dbml_file)
    assert tables["t"].columns[0].note is None

    data = generate_data_from_dbml(tables, refs, base_rows=5, seed=1)
    assert len(data["t"]) == 5


# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------
SKEW_SCHEMA = """
Table users {
    id int [pk]
    email email [unique]
}
Table orders {
    id int [pk]
    user_id int [not null]
}
Ref: orders.user_id > users.id
"""


def _run(tmp_path, name, schema, *extra_args):
    dbml_file = tmp_path / "shop.dbml"
    dbml_file.write_text(schema)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(
            app, ["--file", str(dbml_file), "--rows", "20", "--name", name, *extra_args]
        )
    finally:
        os.chdir(cwd)


def test_cli_skew_runs_and_echoes_the_skew_line(tmp_path):
    result = _run(tmp_path, "skewed", SKEW_SCHEMA, "--seed", "1", "--skew", "0.8")
    assert result.exit_code == 0, result.output
    assert "Skewing child rows over their parents: 80%" in result.output


def test_cli_skew_outside_zero_to_one_is_rejected_by_typer(tmp_path):
    result = _run(tmp_path, "bad_skew", SKEW_SCHEMA, "--skew", "2")
    assert result.exit_code != 0


BAD_HINT_SCHEMA = """
Table t {
    id int [pk]
    label varchar [note: '{"weights": {"a": 2}}']
}
"""


def test_cli_prints_a_readable_error_on_a_bad_hint(tmp_path):
    result = _run(tmp_path, "bad_hint", BAD_HINT_SCHEMA)
    assert result.exit_code == 1
    assert "❌" in result.output
    assert "t.label" in result.output
