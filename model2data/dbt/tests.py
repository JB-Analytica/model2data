import datetime
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Union

import pandas as pd
import yaml

from model2data.generate.faker import is_free_text_type
from model2data.generate.relationships import classify_refs


def _dump_yaml(data: dict) -> str:
    """Dump a plain Python structure to YAML, safely escaping every value.

    Every table/column/description string that ends up in generated YAML
    goes through this single choke point instead of being hand-interpolated
    into f-string lines, so an arbitrary (but valid) DBML identifier --
    containing a space, colon, quote, etc. -- can never produce invalid or
    silently-misparsed YAML.
    """
    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


def generate_dbt_yml(dest: Path, tables: dict, refs: list[dict], source_name: str = "hackernews"):
    """
    Generate:
      1) __sources.yml with all raw_* seeds (no tests)
      2) One .yml per staging model (stg_*) with tests
      3) One singular SQL test per composite key (indexes block pk/unique)
    Table and column names are used exactly as in DBML.
    """

    staging_path = dest / "models" / "staging"
    staging_path.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Build foreign key map
    # -------------------------
    # Only emit a `relationships` test for refs the generator actually makes
    # FK-aware: direct FK refs (target column is a pk/"id"), plus attribute
    # refs that ride along an existing FK between the same two tables (see
    # generate.core's attribute-mirroring pass). An attribute ref with no
    # accompanying FK is left as unrelated random data by the generator, so
    # testing it against the parent table would be a guaranteed false
    # failure.
    fk_refs_classified, attribute_refs_classified = classify_refs(tables, refs)
    fk_table_pairs = {(fk["source_table"], fk["target_table"]) for fk in fk_refs_classified}
    eligible_refs = list(fk_refs_classified) + [
        ref
        for ref in attribute_refs_classified
        if (ref["source_table"], ref["target_table"]) in fk_table_pairs
    ]

    fk_map = defaultdict(list)
    for ref in eligible_refs:
        fk_map[(ref["source_table"], ref["source_column"])].append(ref)

    # -------------------------
    # Generate __sources.yml
    # -------------------------
    source_tables = []
    for table in tables.values():
        seed_name = table.name  # keep exact name
        table_desc = getattr(table, "description", None) or f"Table {seed_name}"
        source_tables.append({"name": seed_name, "description": table_desc})

    sources_doc = {
        "version": 2,
        "sources": [
            {
                "name": "raw",
                "schema": "raw",
                "description": f"{source_name.capitalize()} raw seed data",
                "tables": source_tables,
            }
        ],
    }
    sources_file = staging_path / "__sources.yml"
    sources_file.write_text(_dump_yaml(sources_doc))

    # -------------------------
    # Generate individual staging model YAMLs
    # -------------------------
    for table in tables.values():
        stg_name = f"stg_{table.name}"  # staging model names are prefixed, columns unchanged
        model_columns = []

        for col in table.columns:
            tests: list[Union[str, dict[str, dict[str, Any]]]] = []
            settings = col.settings or set()

            if "not null" in settings or "pk" in settings:
                tests.append("not_null")
            if "unique" in settings or "pk" in settings:
                tests.append("unique")
            if getattr(col, "enum_values", None):
                tests.append({"accepted_values": {"values": list(col.enum_values)}})

            fk_refs = fk_map.get((table.name, col.name), [])
            for fk in fk_refs:
                tests.append(
                    {
                        "relationships": {
                            "to": f"ref('stg_{fk['target_table']}')",
                            "field": _dbt_column_ref(fk["target_column"]),
                        }
                    }
                )

            col_doc: dict[str, Any] = {"name": _dbt_column_ref(col.name)}
            description = getattr(col, "description", None)
            if description:
                col_doc["description"] = description
            if tests:
                col_doc["tests"] = tests
            model_columns.append(col_doc)

        model_doc = {
            "version": 2,
            "models": [{"name": stg_name, "columns": model_columns}],
        }

        # Write YAML to same folder as SQL model
        yml_file = staging_path / f"{stg_name}.yml"
        yml_file.write_text(_dump_yaml(model_doc))

    # -------------------------
    # Composite key singular tests
    # -------------------------
    _generate_composite_key_tests(dest, tables)

    # -------------------------
    # Seed column-type overrides
    # -------------------------
    _generate_seed_config(dest, tables)


def _generate_seed_config(dest: Path, tables: dict) -> None:
    """
    Force every free-text column (see `is_free_text_type`) to VARCHAR in
    the seed loader config, instead of letting dbt/duckdb sniff the type
    from CSV content. Some generated text is all-digit (EAN13 barcodes,
    zero-padded postcodes, ...) and would otherwise be silently loaded as
    an integer, overflowing or dropping leading zeros.
    """
    seed_entries = []

    for table in tables.values():
        column_types = {
            col.name: "varchar" for col in table.columns if is_free_text_type(col.data_type)
        }
        if not column_types:
            continue

        seed_entries.append({"name": table.name, "config": {"column_types": column_types}})

    if not seed_entries:
        return

    seeds_doc = {"version": 2, "seeds": seed_entries}

    seed_raw_path = dest / "seeds" / "raw"
    seed_raw_path.mkdir(parents=True, exist_ok=True)
    (seed_raw_path / "__seed_config.yml").write_text(_dump_yaml(seeds_doc))


def _quote_sql_identifier(name: str) -> str:
    """ANSI double-quote a raw column identifier for use in generated SQL.

    Both supported adapters (DuckDB and Postgres) accept ANSI double-quoting,
    which is required once a DBML identifier contains a space, colon, or
    other character that would otherwise break an unquoted `select`/`group
    by` clause. A literal `"` inside the identifier is escaped by doubling,
    the standard ANSI SQL convention.
    """
    return '"' + name.replace('"', '""') + '"'


_BARE_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _dbt_column_ref(name: str) -> str:
    """Return a column identifier the way it should appear in generated dbt
    schema YAML (a `columns:` entry's `name:`, or a `relationships` test's
    `field:`).

    dbt's built-in generic tests (`not_null`, `unique`, `relationships`, ...)
    interpolate that YAML value directly into compiled SQL as-is (e.g.
    `select {{ column_name }} as ...`), with no quoting of their own. A
    DBML identifier that needed quoting to contain a space or other special
    character (`"display name"`) therefore breaks the compiled SQL unless
    the YAML value is pre-quoted here -- dbt's own documented workaround for
    exactly this case.
    """
    return name if _BARE_SQL_IDENTIFIER_RE.match(name) else _quote_sql_identifier(name)


def _generate_composite_key_tests(dest: Path, tables: dict) -> None:
    tests_path = dest / "data-tests"
    tests_path.mkdir(parents=True, exist_ok=True)

    for table in tables.values():
        composite_keys = getattr(table, "composite_keys", None) or []
        stg_name = f"stg_{table.name}"

        for key in composite_keys:
            columns = key.get("columns") or []
            if len(columns) < 2:
                continue

            quoted_columns = [_quote_sql_identifier(c) for c in columns]
            columns_csv = ", ".join(quoted_columns)
            test_name = "unique_combination_" + "_".join([stg_name, *columns])
            sql = (
                f"select {columns_csv}, count(*) as n\n"
                f"from {{{{ ref('{stg_name}') }}}}\n"
                f"group by {columns_csv}\n"
                f"having count(*) > 1\n"
            )
            (tests_path / f"{test_name}.sql").write_text(sql)


def generate_unit_tests(
    dest: Path,
    tables: dict,
    generated_data: dict[str, pd.DataFrame],
    sample_size: int = 2,
) -> None:
    """
    Generate dbt unit test YAML fixtures (requires dbt-core >= 1.8).

    Since staging models are pure `select * from {{ source(...) }}` passthroughs,
    a handful of already-generated rows can serve as both `given` and `expect`.

    Written alongside each staging model (under `model-paths`, which is where
    dbt actually parses unit tests from) as `ut_stg_<table>.yml`, parallel to
    the `stg_<table>.yml` schema file generated by `generate_dbt_yml`.
    """
    unit_tests_path = dest / "models" / "staging"
    unit_tests_path.mkdir(parents=True, exist_ok=True)

    for table in tables.values():
        df = generated_data.get(table.name)
        if df is None or df.empty:
            continue

        sample_rows = _rows_as_native_dicts(df.head(sample_size))
        stg_name = f"stg_{table.name}"
        # table.name is spliced into a single-quoted Jinja string literal (the
        # source() call is evaluated by dbt as an expression, not treated as
        # a literal YAML string); escape any embedded single quote so an
        # unusual DBML identifier can't break that call.
        escaped_name = table.name.replace("'", "\\'")

        unit_test = {
            "unit_tests": [
                {
                    "name": f"test_{stg_name}_passthrough",
                    "model": stg_name,
                    "given": [
                        {
                            "input": f"source('raw', '{escaped_name}')",
                            "rows": sample_rows,
                        }
                    ],
                    # Deep-copied, not the same list object, so the dumped YAML is two
                    # plain literal blocks instead of an anchor/alias pair.
                    "expect": {"rows": [dict(row) for row in sample_rows]},
                }
            ]
        }

        yml_file = unit_tests_path / f"ut_{stg_name}.yml"
        yml_file.write_text(yaml.safe_dump(unit_test, sort_keys=False, default_flow_style=False))


def _to_native_value(value: Any) -> Any:
    """Convert a single DataFrame cell to a plain, YAML-dumpable Python value."""
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat(sep=" ")
    if isinstance(value, datetime.date):
        return str(value)
    item = getattr(value, "item", None)
    if callable(item):
        # numpy scalar (int64, float64, bool_, ...)
        return item()
    return value


def _rows_as_native_dicts(df: pd.DataFrame) -> list[dict]:
    """Convert a DataFrame's rows to plain-Python-typed dicts, NaN/NaT -> None."""
    rows = []
    for record in df.to_dict(orient="records"):
        rows.append({key: _to_native_value(value) for key, value in record.items()})
    return rows
