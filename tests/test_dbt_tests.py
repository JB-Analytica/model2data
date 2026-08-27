import pandas as pd
import yaml

from model2data.cli import main as generate_cli
from model2data.dbt.project import create_project_scaffold
from model2data.dbt.tests import (
    _dbt_column_ref,
    _to_native_value,
    generate_dbt_yml,
    generate_unit_tests,
)
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
    assert "active" in content
    assert "inactive" in content
    assert "pending" in content

    # Flat (non-`arguments:`-nested) generic test config, matching the
    # `relationships` test below it -- this project's dbt-core floor
    # (>=1.8, see pyproject.toml) predates dbt-core's `arguments:` nesting
    # support entirely (verified: it hard-errors below dbt-core 1.10.5), so
    # generated tests intentionally use the older, universally-compatible
    # flat config shape. Current dbt-core versions still accept it (with a
    # soft deprecation warning, not a failure).
    parsed = yaml.safe_load(content)
    status_tests = next(c["tests"] for c in parsed["models"][0]["columns"] if c["name"] == "status")
    accepted_values_test = next(
        t["accepted_values"] for t in status_tests if "accepted_values" in t
    )
    assert set(accepted_values_test["values"]) == {"active", "inactive", "pending"}


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

    seeds_text = (tmp_path / "seeds" / "raw" / "__seed_config.yml").read_text()
    seeds_yaml = yaml.safe_load(seeds_text)
    seed_entry = seeds_yaml["seeds"][0]
    assert seed_entry["name"] == "users"
    assert seed_entry["description"] == "Stores registered users: profile + contact info"

    stg_text = (tmp_path / "models" / "staging" / "stg_users.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)
    email_col = next(c for c in stg_yaml["models"][0]["columns"] if c["name"] == "email")
    assert email_col["description"] == "the user's primary email: verified on signup"


def test_table_without_description_falls_back_to_placeholder(tmp_path):
    """A table with no DBML `Note` and no free-text columns still gets a seed
    entry -- the entry is emitted for a description *or* column_types, and
    `config` is omitted when there are no free-text columns to override.
    """
    tables = {"users": TableDef(name="users", columns=[ColumnDef("id", "int", {"pk"})])}
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    seeds_yaml = yaml.safe_load((tmp_path / "seeds" / "raw" / "__seed_config.yml").read_text())
    seed_entry = seeds_yaml["seeds"][0]
    assert seed_entry["name"] == "users"
    assert seed_entry["description"] == "Table users"
    assert "config" not in seed_entry


def test_no_sources_yml_is_written(tmp_path):
    """The generated project must declare no dbt `sources:` at all.

    A dbt source is an assertion that a relation already exists; it carries no
    DAG edge to the seed that materializes it. While staging models read from
    `source('raw', ...)`, a single-pass `dbt build` on a fresh database could
    schedule a staging model before its seed had been loaded, failing with
    "Table with name <seed> does not exist". Staging models `ref()` the seeds
    instead, and the sources block is gone.
    """
    tables = {
        "users": TableDef(
            name="users",
            description="Registered users",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("name", "varchar")],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    assert not (tmp_path / "models" / "staging" / "__sources.yml").exists()
    for yml_file in tmp_path.rglob("*.yml"):
        assert "sources:" not in yml_file.read_text(), (
            f"{yml_file} still declares a dbt sources block"
        )


def test_staging_sql_refs_the_seed_not_a_source(tmp_path, monkeypatch):
    """End-to-end through the CLI: every generated staging model must build
    its DAG edge with `ref('<seed>')`, and no `__sources.yml` may be left
    behind.
    """
    monkeypatch.chdir(tmp_path)

    dbml_file = tmp_path / "shop.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    generate_cli(
        file=dbml_file,
        rows=5,
        seed=1,
        name="refcheck",
        force=True,
        adapter="duckdb",
    )

    project_dir = tmp_path / "dbt_refcheck"
    sql = (project_dir / "models" / "staging" / "stg_users.sql").read_text()
    assert "ref('users')" in sql
    assert "source(" not in sql
    assert not (project_dir / "models" / "staging" / "__sources.yml").exists()


def test_seed_description_emitted_without_any_free_text_columns(tmp_path):
    """A table with a DBML description but no free-text columns still needs a
    seed entry. `_generate_seed_properties` used to `continue` past any table
    with no `column_types`, which would silently drop that table's
    documentation now that descriptions live on the seed node.
    """
    tables = {
        "counters": TableDef(
            name="counters",
            description="Numeric counters only: no text columns at all",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("hits", "int", {"not null"})],
        ),
        "articles": TableDef(
            name="articles",
            description="Articles with a text body",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("body", "text", {"not null"})],
        ),
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    seeds_yaml = yaml.safe_load((tmp_path / "seeds" / "raw" / "__seed_config.yml").read_text())
    entries = {entry["name"]: entry for entry in seeds_yaml["seeds"]}

    assert entries["counters"]["description"] == "Numeric counters only: no text columns at all"
    assert "config" not in entries["counters"]

    assert entries["articles"]["description"] == "Articles with a text body"
    assert entries["articles"]["config"]["column_types"] == {"body": "varchar"}


def test_no_tables_writes_no_seed_properties_file(tmp_path):
    """With no tables at all there is nothing to document or configure, so the
    seeds properties YAML is skipped rather than written empty.
    """
    generate_dbt_yml(tmp_path, {}, [], source_name="shop")

    assert not (tmp_path / "seeds" / "raw" / "__seed_config.yml").exists()


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
    assert '"order_id", "product_id"' in content
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
    assert unit_test["given"][0]["input"] == "ref('users')"
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


def test_generate_dbt_yml_handles_identifier_with_space(tmp_path):
    tables = {
        "my weird table": TableDef(
            name="my weird table",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("a column", "varchar", {"not null"}),
            ],
            composite_keys=[{"columns": ["id", "a column"], "type": "pk"}],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    seeds_text = (tmp_path / "seeds" / "raw" / "__seed_config.yml").read_text()
    seeds_yaml = yaml.safe_load(seeds_text)
    assert seeds_yaml["seeds"][0]["name"] == "my weird table"

    stg_text = (tmp_path / "models" / "staging" / "stg_my weird table.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)
    col_names = {c["name"] for c in stg_yaml["models"][0]["columns"]}
    # dbt's built-in generic tests (not_null, unique, ...) splice the YAML
    # `name:` value directly into compiled SQL with no quoting of their own
    # (`select {{ column_name }} ...`); a real `dbt build` against an
    # unquoted "a column" fails with a SQL parser error. The generated YAML
    # must pre-quote any column name that isn't a bare SQL identifier.
    assert '"a column"' in col_names

    test_files = list((tmp_path / "data-tests").glob("*.sql"))
    assert len(test_files) == 1
    sql = test_files[0].read_text()
    assert '"id", "a column"' in sql


def test_generate_dbt_yml_handles_identifier_with_colon(tmp_path):
    tables = {
        "orders": TableDef(
            name="orders",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("status: code", "varchar", {"not null"}),
            ],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    stg_text = (tmp_path / "models" / "staging" / "stg_orders.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)  # raises if the colon broke YAML syntax
    col_names = {c["name"] for c in stg_yaml["models"][0]["columns"]}
    # Quoted for the same reason as the space case above: an unquoted colon
    # in a generic test's compiled SQL is a syntax error.
    assert '"status: code"' in col_names


def test_generate_dbt_yml_handles_identifier_with_single_quote(tmp_path):
    tables = {
        "users": TableDef(
            name="users",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("user's name", "varchar", {"not null"}),
            ],
        )
    }
    generate_dbt_yml(tmp_path, tables, [], source_name="shop")

    stg_text = (tmp_path / "models" / "staging" / "stg_users.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)
    col_names = {c["name"] for c in stg_yaml["models"][0]["columns"]}
    # ANSI double-quoted (not escaped for YAML/Jinja): a bare "user's name"
    # in a generic test's compiled SQL is a syntax error (see the space/
    # colon cases above); the embedded apostrophe is safe inside the outer
    # double quotes and needs no further escaping.
    assert '"user\'s name"' in col_names


def test_generate_unit_tests_handles_identifier_with_single_quote(tmp_path):
    tables = {
        "it's a table": TableDef(
            name="it's a table",
            columns=[ColumnDef("id", "int", {"pk"})],
        )
    }
    df = pd.DataFrame({"id": [1, 2]})
    generate_unit_tests(tmp_path, tables, {"it's a table": df}, sample_size=2)

    yml_file = tmp_path / "models" / "staging" / "ut_stg_it's a table.yml"
    data = yaml.safe_load(yml_file.read_text())
    given_input = data["unit_tests"][0]["given"][0]["input"]
    assert given_input == "ref('it\\'s a table')"


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


def test_dbt_column_ref_quotes_only_when_needed():
    """Regression test: dbt's generic tests (not_null, unique,
    relationships, accepted_values, ...) splice the schema YAML's
    `name`/`field` value directly into compiled SQL with no quoting of
    their own. A real `dbt build` against a generated project with a
    space/colon/special-char column name failed with a SQL parser error
    until `_dbt_column_ref` started pre-quoting non-bare identifiers.
    """
    assert _dbt_column_ref("id") == "id"
    assert _dbt_column_ref("user_id") == "user_id"
    assert _dbt_column_ref("display name") == '"display name"'
    assert _dbt_column_ref("status:code") == '"status:code"'
    assert _dbt_column_ref('has"quote') == '"has""quote"'


def test_relationships_test_field_is_quoted_for_special_target_column(tmp_path):
    """The `field:` of a generated `relationships` test is the *target*
    (parent) column name -- also spliced raw into compiled SQL by dbt's
    relationships test macro, so it needs the same quoting as a table's own
    `columns: - name:` entries.
    """
    tables = {
        "accounts": TableDef(
            name="accounts",
            columns=[ColumnDef("account id", "int", {"pk"})],
        ),
        "orders": TableDef(
            name="orders",
            columns=[ColumnDef("id", "int", {"pk"}), ColumnDef("account_id", "int", set())],
        ),
    }
    refs = [
        {
            "source_table": "orders",
            "source_column": "account_id",
            "target_table": "accounts",
            "target_column": "account id",
        }
    ]
    generate_dbt_yml(tmp_path, tables, refs, source_name="shop")

    stg_text = (tmp_path / "models" / "staging" / "stg_orders.yml").read_text()
    stg_yaml = yaml.safe_load(stg_text)
    orders_col = next(c for c in stg_yaml["models"][0]["columns"] if c["name"] == "account_id")
    relationship_test = next(
        t["relationships"]
        for t in orders_col["tests"]
        if isinstance(t, dict) and "relationships" in t
    )
    assert relationship_test["field"] == '"account id"'
