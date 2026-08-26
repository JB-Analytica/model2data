import pandas as pd
import yaml

from model2data.cli import main as generate_cli
from model2data.dbt.project import create_project_scaffold
from model2data.dbt.tests import _to_native_value, generate_dbt_yml, generate_unit_tests
from model2data.parse.dbml import ColumnDef, TableDef


def test_dbt_tests_generation(tmp_path, monkeypatch):
    """Test that dbt tests are generated for various column constraints."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    dbml_file = tmp_path / "simple.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
        email varchar [unique]
    }

    Table posts {
        id int [pk]
        user_id int [not null]
        title varchar [not null]
    }

    Ref {
        posts.user_id > users.id}
    """
    )

    # Call generate function
    generate_cli(
        file=dbml_file,
        rows=10,
        seed=42,
        name="test_project",
        force=True,
        adapter="duckdb",
    )

    project_dir = tmp_path / "dbt_test_project"

    # Test users model
    users_yml = project_dir / "models" / "staging" / "stg_users.yml"
    assert users_yml.exists()
    users_content = users_yml.read_text()
    assert "not_null" in users_content
    assert "unique" in users_content

    # Test posts model with foreign key relationship
    posts_yml = project_dir / "models" / "staging" / "stg_posts.yml"
    assert posts_yml.exists()
    posts_content = posts_yml.read_text()

    # Debug: print the actual content
    print("\n=== Posts YML Content ===")
    print(posts_content)
    print("=== End Content ===\n")

    assert "not_null" in posts_content
    # The relationship test should be on user_id column
    assert "user_id" in posts_content
    assert "relationships" in posts_content, (
        f"Expected 'relationships' in content:\n{posts_content}"
    )
    assert "ref('stg_users')" in posts_content


def test_accepted_values_test_generated_for_enum_column(tmp_path):
    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef(
                    "status",
                    "status_enum",
                    {"not null"},
                    enum_values=["active", "inactive", "pending"],
                ),
            ],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    content = (tmp_path / "models" / "staging" / "stg_orders.yml").read_text()
    assert "accepted_values" in content
    assert "'active'" in content
    assert "'inactive'" in content
    assert "'pending'" in content

    # accepted_values' `values` list must be nested under `arguments:`, like
    # the relationships test below it -- dbt-core >= 1.10 deprecates (and a
    # future version will reject) a generic test config with properties
    # given directly instead of under `arguments`.
    parsed = yaml.safe_load(content)
    status_tests = next(c["tests"] for c in parsed["models"][0]["columns"] if c["name"] == "status")
    accepted_values_test = next(
        t["accepted_values"] for t in status_tests if "accepted_values" in t
    )
    assert "arguments" in accepted_values_test
    assert set(accepted_values_test["arguments"]["values"]) == {"active", "inactive", "pending"}


def test_table_and_column_descriptions_round_trip_as_valid_yaml(tmp_path):
    tables = {
        "users": TableDef(
            name="users",
            description="Stores registered users: profile + contact info",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef(
                    "email",
                    "varchar",
                    {"not null"},
                    description="the user's primary email: verified on signup",
                ),
            ],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    sources_text = (tmp_path / "models" / "staging" / "__sources.yml").read_text()
    sources_yaml = yaml.safe_load(sources_text)
    table_entry = sources_yaml["sources"][0]["tables"][0]
    assert table_entry["description"] == "Stores registered users: profile + contact info"

    stg_text = (tmp_path / "models" / "staging" / "stg_users.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)
    email_col = next(c for c in stg_yaml["models"][0]["columns"] if c["name"] == "email")
    assert email_col["description"] == "the user's primary email: verified on signup"


def test_table_without_description_falls_back_to_placeholder(tmp_path):
    tables = {"users": TableDef(name="users", columns=[ColumnDef("id", "int", {"pk"})])}
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    sources_yaml = yaml.safe_load((tmp_path / "models" / "staging" / "__sources.yml").read_text())
    assert sources_yaml["sources"][0]["tables"][0]["description"] == "Table users"


def test_composite_key_singular_test_sql_written(tmp_path):
    tables = {
        "order_items": TableDef(
            name="order_items",
            columns=[
                ColumnDef("order_id", "int", {"not null"}),
                ColumnDef("product_id", "int", {"not null"}),
            ],
            composite_keys=[{"columns": ["order_id", "product_id"], "type": "pk"}],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    test_file = (
        tmp_path / "data-tests" / "unique_combination_stg_order_items_order_id_product_id.sql"
    )
    assert test_file.exists()
    content = test_file.read_text()
    assert "ref('stg_order_items')" in content
    assert "order_id, product_id" in content
    assert "having count(*) > 1" in content


def test_no_composite_keys_writes_no_singular_test(tmp_path):
    tables = {"users": TableDef(name="users", columns=[ColumnDef("id", "int", {"pk"})])}
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    tests_dir = tmp_path / "data-tests"
    sql_files = list(tests_dir.glob("*.sql")) if tests_dir.exists() else []
    assert sql_files == []


def test_generated_file_paths_match_dbt_project_yml_resource_paths(tmp_path):
    """Guard against the class of bug behind Bug #1 and Bug #2: generate_dbt_yml
    and generate_unit_tests writing files to a directory that isn't actually
    one of dbt_project.yml's declared `*-paths`, so dbt silently never
    discovers them even though the files exist on disk. This doesn't invoke
    the dbt CLI (see tests/test_dbt_integration.py for that), so it's cheap
    and always runs, but it directly checks the two sources of truth --
    the rendered `*-paths` config and the directories the generators actually
    write to -- against each other instead of hardcoding either one.
    """
    create_project_scaffold(tmp_path, "proj", "proj_profile")
    project_yml = yaml.safe_load((tmp_path / "dbt_project.yml").read_text())

    model_paths = [(tmp_path / p).resolve() for p in project_yml["model-paths"]]
    test_paths = [(tmp_path / p).resolve() for p in project_yml["test-paths"]]
    seed_paths = [(tmp_path / p).resolve() for p in project_yml["seed-paths"]]

    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("order_id", "int", {"not null"}),
                ColumnDef("customer_id", "int", {"not null"}),
                ColumnDef(
                    "status",
                    "status_enum",
                    {"not null"},
                    enum_values=["open", "closed"],
                ),
            ],
            composite_keys=[{"columns": ["order_id", "customer_id"], "type": "pk"}],
        )
    }
    df = pd.DataFrame({"order_id": [1], "customer_id": [2], "status": ["open"]})

    generate_dbt_yml(tmp_path, tables, [], source_name="shop")
    generate_unit_tests(tmp_path, tables, {"orders": df}, sample_size=1)

    generated_files = [
        p
        for p in tmp_path.rglob("*")
        if p.is_file()
        and p.suffix in (".yml", ".sql")
        and p.name != "dbt_project.yml"
        and "macros" not in p.parts
    ]
    assert generated_files, "expected generate_dbt_yml/generate_unit_tests to write some files"

    for f in generated_files:
        resolved = f.resolve()
        if f.suffix == ".sql":
            assert any(resolved.is_relative_to(root) for root in test_paths), (
                f"{f} is a singular SQL test but is not under any configured "
                f"test-paths dir {test_paths}"
            )
        elif "seeds" in f.parts:
            assert any(resolved.is_relative_to(root) for root in seed_paths), (
                f"{f} is a seed config file but is not under any configured "
                f"seed-paths dir {seed_paths}"
            )
        else:
            assert any(resolved.is_relative_to(root) for root in model_paths), (
                f"{f} is not under any configured model-paths dir {model_paths}"
            )


def test_generate_unit_tests_produces_valid_yaml_with_expected_rows(tmp_path):
    tables = {
        "users": TableDef(
            name="users",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("name", "varchar")],
        )
    }
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"]})

    generate_unit_tests(tmp_path, tables, {"users": df}, sample_size=2)

    yml_file = tmp_path / "models" / "staging" / "ut_stg_users.yml"
    assert yml_file.exists()
    data = yaml.safe_load(yml_file.read_text())

    unit_test = data["unit_tests"][0]
    assert unit_test["model"] == "stg_users"
    assert unit_test["given"][0]["input"] == "source('raw', 'users')"
    assert len(unit_test["given"][0]["rows"]) == 2
    assert unit_test["given"][0]["rows"] == unit_test["expect"]["rows"]
    assert unit_test["given"][0]["rows"][0] == {"id": 1, "name": "Alice"}


def test_generate_unit_tests_handles_nan_and_fewer_rows_than_sample_size(tmp_path):
    tables = {
        "widgets": TableDef(
            name="widgets",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("notes", "varchar")],
        )
    }
    df = pd.DataFrame({"id": [1], "notes": [None]})

    generate_unit_tests(tmp_path, tables, {"widgets": df}, sample_size=5)

    yml_file = tmp_path / "models" / "staging" / "ut_stg_widgets.yml"
    data = yaml.safe_load(yml_file.read_text())

    rows = data["unit_tests"][0]["given"][0]["rows"]
    assert len(rows) == 1
    assert rows[0]["notes"] is None
    assert rows[0]["id"] == 1


def test_generate_unit_tests_converts_dates_and_numpy_scalars(tmp_path):
    import datetime

    import numpy as np

    tables = {
        "events": TableDef(
            name="events",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("event_date", "date", {"not null"}),
                ColumnDef("logged_at", "timestamp", {"not null"}),
            ],
        )
    }
    df = pd.DataFrame(
        {
            "id": np.array([1], dtype="int64"),
            "event_date": [datetime.date(2024, 1, 1)],
            "logged_at": [pd.Timestamp("2024-01-01 12:30:00")],
        }
    )

    generate_unit_tests(tmp_path, tables, {"events": df}, sample_size=1)

    yml_file = tmp_path / "models" / "staging" / "ut_stg_events.yml"
    data = yaml.safe_load(yml_file.read_text())
    row = data["unit_tests"][0]["given"][0]["rows"][0]
    assert row["id"] == 1
    assert row["event_date"] == "2024-01-01"
    assert row["logged_at"] == "2024-01-01 12:30:00"


def test_generate_unit_tests_skips_table_with_no_generated_data(tmp_path):
    tables = {
        "empty": TableDef(name="empty", columns=[ColumnDef("id", "int", {"pk"})]),
    }
    generate_unit_tests(tmp_path, tables, {"empty": pd.DataFrame({"id": []})}, sample_size=2)

    assert not (tmp_path / "models" / "staging" / "ut_stg_empty.yml").exists()


def test_to_native_value_unwraps_raw_numpy_scalars():
    import numpy as np

    assert _to_native_value(np.int64(7)) == 7
    assert isinstance(_to_native_value(np.int64(7)), int)


def test_single_column_index_entry_produces_no_singular_test(tmp_path):
    tables = {
        "users": TableDef(
            name="users",
            columns=[ColumnDef("email", "varchar", {"unique"})],
            composite_keys=[{"columns": ["email"], "type": "unique"}],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    tests_dir = tmp_path / "data-tests"
    sql_files = list(tests_dir.glob("*.sql")) if tests_dir.exists() else []
    assert sql_files == []
