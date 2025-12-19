from collections import defaultdict
from pathlib import Path
from typing import Any, Union


def generate_dbt_yml(dest: Path, tables: dict, refs: list[dict], source_name: str = "hackernews"):
    """
    Generate:
      1) __sources.yml with all raw_* seeds (no tests)
      2) One .yml per staging model (stg_*) with tests
    Table and column names are used exactly as in DBML.
    """

    staging_path = dest / "models" / "staging"
    staging_path.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Build foreign key map
    # -------------------------
    fk_map = defaultdict(list)
    for ref in refs:
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
        sources_lines.append(f"        description: {table_desc}")

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

            model_columns.append({"name": col.name, "tests": tests if tests else None})

        # Render model YAML
        lines = ["version: 2", "", "models:"]
        lines.append(f"  - name: {stg_name}")
        lines.append("    columns:")
        for col in model_columns:
            lines.append(f"      - name: {col['name']}")
            if col["tests"]:
                lines.append("        tests:")
                for test in col["tests"]:
                    if isinstance(test, str):
                        lines.append(f"          - {test}")
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
