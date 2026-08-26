import datetime
from collections import defaultdict
from pathlib import Path
from typing import Any, Union

import pandas as pd
import yaml

from model2data.generate.faker import is_free_text_type
from model2data.generate.relationships import classify_refs


def _yaml_field_lines(key: str, value: str, indent: str) -> list[str]:
    """Render `key: value` as safely-escaped YAML lines at the given indent."""
    dumped = yaml.safe_dump({key: value}, default_flow_style=False, sort_keys=False)
    return [f"{indent}{line}" for line in dumped.strip("\n").splitlines()]


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
    sources_lines = ["version: 2", "", "sources:"]
    sources_lines.append("  - name: raw")
    sources_lines.append("    schema: raw")
    sources_lines.append(f"    description: {source_name.capitalize()} raw seed data")
    sources_lines.append("    tables:")

    for table in tables.values():
        seed_name = table.name  # keep exact name
        table_desc = getattr(table, "description", None) or f"Table {seed_name}"
        sources_lines.append(f"      - name: {seed_name}")
        sources_lines.extend(_yaml_field_lines("description", table_desc, "        "))

    sources_file = staging_path / "__sources.yml"
    sources_file.write_text("\n".join(sources_lines))

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
                            "field": fk["target_column"],
                        }
                    }
                )

            model_columns.append(
                {
                    "name": col.name,
                    "description": getattr(col, "description", None),
                    "tests": tests if tests else None,
                }
            )

        # Render model YAML
        lines = ["version: 2", "", "models:"]
        lines.append(f"  - name: {stg_name}")
        lines.append("    columns:")
        for col in model_columns:
            lines.append(f"      - name: {col['name']}")
            if col["description"]:
                lines.extend(_yaml_field_lines("description", col["description"], "        "))
            if col["tests"]:
                lines.append("        tests:")
                for test in col["tests"]:
                    if isinstance(test, str):
                        lines.append(f"          - {test}")
                    elif "accepted_values" in test:
                        lines.append("          - accepted_values:")
                        lines.append("              arguments:")
                        lines.append("                values:")
                        for value in test["accepted_values"]["values"]:
                            lines.append(f"                  - {value!r}")
                    else:
                        # relationships test with arguments
                        for k, v in test.items():
                            lines.append(f"          - {k}:")
                            lines.append("              arguments:")
                            for fk_key, fk_val in v.items():
                                lines.append(f"                {fk_key}: {fk_val}")

        # Write YAML to same folder as SQL model
        yml_file = staging_path / f"{stg_name}.yml"
        yml_file.write_text("\n".join(lines))

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
    lines = ["version: 2", "", "seeds:"]
    any_column_types = False

    for table in tables.values():
        column_types = {
            col.name: "varchar" for col in table.columns if is_free_text_type(col.data_type)
        }
        if not column_types:
            continue

        any_column_types = True
        lines.append(f"  - name: {table.name}")
        lines.append("    config:")
        lines.append("      column_types:")
        for col_name, col_type in column_types.items():
            lines.append(f"        {col_name}: {col_type}")

    if not any_column_types:
        return

    seed_raw_path = dest / "seeds" / "raw"
    seed_raw_path.mkdir(parents=True, exist_ok=True)
    (seed_raw_path / "__seed_config.yml").write_text("\n".join(lines))


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

            columns_csv = ", ".join(columns)
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

        unit_test = {
            "unit_tests": [
                {
                    "name": f"test_{stg_name}_passthrough",
                    "model": stg_name,
                    "given": [
                        {
                            "input": f"source('raw', '{table.name}')",
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
