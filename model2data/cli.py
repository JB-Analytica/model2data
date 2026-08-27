import random
import shutil
from pathlib import Path
from typing import Optional

import typer
from faker import Faker

from model2data.dbt.project import (
    create_profiles_yml,
    create_project_scaffold,
    create_staging_models,
)
from model2data.dbt.tests import generate_dbt_yml, generate_unit_tests
from model2data.generate.core import (
    generate_data_from_dbml,
    get_cyclic_tables,
    get_unresolved_composite_keys,
)
from model2data.generate.faker import (
    get_duplicate_unique_columns,
    get_unmapped_columns,
    reset_stats,
)
from model2data.parse.dbml import get_parse_warnings, parse_dbml
from model2data.utils import normalize_identifier

SUPPORTED_ADAPTERS = ("duckdb", "postgres")

app = typer.Typer(
    help=(
        "model2data: Generate analytics-ready datasets from DBML models.\n\n"
        "Given a DBML file, this tool produces:\n"
        "• Synthetic but realistic data\n"
        "• A runnable dbt project scaffold\n"
        "• dbt seeds, staging models, and profiles\n"
    ),
    add_completion=False,
)


@app.command(help="Generate synthetic data and a dbt project from a DBML model.")
def main(
    file: Path = typer.Option(  # noqa: B008
        ...,
        "--file",
        "-f",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Path to the DBML file to generate data from.",
    ),
    rows: int = typer.Option(
        100,
        "--rows",
        "-r",
        min=10,
        help="Number of rows to generate per table.",
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help=(
            "Optional random seed for deterministic generation.\n"
            "Using the same seed will always produce identical datasets."
        ),
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help="Optional override for the generated dbt project's name.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite the destination directory if it already exists.",
    ),
    adapter: str = typer.Option(
        "duckdb",
        "--adapter",
        "-a",
        help=f"dbt warehouse adapter to target. One of: {', '.join(SUPPORTED_ADAPTERS)}.",
    ),
    unit_tests: bool = typer.Option(
        False,
        "--unit-tests",
        help=(
            "Also generate deterministic dbt unit test fixtures (models/staging/ut_stg_*.yml) "
            "from the generated seed rows. Requires dbt-core >= 1.8 to run."
        ),
    ),
):
    """
    Generate synthetic data and a dbt project from a DBML model.
    """

    # -------------------------
    # Validate adapter
    # -------------------------
    adapter = adapter.lower()
    if adapter not in SUPPORTED_ADAPTERS:
        typer.echo(
            f"❌ Unsupported adapter '{adapter}'. Choose one of: {', '.join(SUPPORTED_ADAPTERS)}."
        )
        raise typer.Exit(1)

    # -------------------------
    # Deterministic seed
    # -------------------------
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)
        typer.echo(f"🔁 Using deterministic seed: {seed}")

    # -------------------------
    # Parse DBML (names untouched)
    # -------------------------
    tables, refs = parse_dbml(file)
    parse_warnings = get_parse_warnings()
    if not tables:
        typer.echo("❌ No tables found in the provided DBML file.")
        raise typer.Exit(1)

    project_name = normalize_identifier(name or file.stem)
    dest = Path.cwd() / f"dbt_{project_name}"
    profile_name = f"{project_name}_profile"

    if dest.exists():
        if not force:
            typer.echo(f"❌ Destination {dest} already exists.\nUse --force to overwrite.")
            raise typer.Exit(1)
        shutil.rmtree(dest)

    # -------------------------
    # dbt project scaffold
    # -------------------------
    typer.echo(f"📦 Creating dbt project scaffold at {dest}")
    create_project_scaffold(dest, project_name, profile_name)

    # -------------------------
    # Generate synthetic data
    # -------------------------
    typer.echo("🧮 Generating synthetic datasets from DBML definitions...")
    reset_stats()
    generated_tables = generate_data_from_dbml(
        tables=tables,
        refs=refs,
        base_rows=rows,
        seed=seed,
    )

    # -------------------------
    # Write dbt seeds (normalized names)
    # -------------------------
    seeds_path = dest / "seeds/raw"
    for table_key, df in generated_tables.items():
        csv_path = seeds_path / f"{table_key}.csv"
        df.to_csv(csv_path, index=False)

    # -------------------------
    # Build dbt assets
    # -------------------------
    typer.echo("🗂️ Building staging models for generated seeds...")
    create_staging_models(dest, project_name)

    typer.echo("🧪 Generating dbt yml with tests...")
    generate_dbt_yml(dest, tables, refs, project_name)

    if unit_tests:
        typer.echo("🔬 Generating dbt unit test fixtures (requires dbt-core >= 1.8)...")
        generate_unit_tests(dest, tables, generated_tables)

    typer.echo(f"🪪 Ensuring dbt profile exists ({adapter})...")
    create_profiles_yml(dest, profile_name, adapter=adapter)

    # Keep original DBML for reference
    shutil.copy(file, dest / file.name)

    # -------------------------
    # Summary
    # -------------------------
    total_rows = sum(len(df) for df in generated_tables.values())
    unmapped = get_unmapped_columns()
    cyclic_tables = get_cyclic_tables()
    unresolved_keys = get_unresolved_composite_keys()
    duplicate_unique = get_duplicate_unique_columns()

    typer.echo("\n📊 Summary")
    typer.echo(f"  Tables generated:        {len(generated_tables)}")
    typer.echo(f"  Rows generated:          {total_rows}")
    typer.echo(f"  Relationships in DBML:   {len(refs)}")
    if unmapped:
        typer.echo(f"  Columns using generic fallback text: {len(unmapped)}")
        for col_name, data_type in unmapped:
            typer.echo(f"    - {col_name} ({data_type})")
    else:
        typer.echo("  Columns using generic fallback text: 0")
    if cyclic_tables:
        typer.echo(
            "  ⚠️  Tables in an unresolved FK cycle (data may not respect "
            f"all relationships): {', '.join(cyclic_tables)}"
        )
    if unresolved_keys:
        typer.echo(
            "  ⚠️  Composite keys left with duplicate rows (their generated dbt "
            "test will fail — try a lower --rows, or widen the key's value space):"
        )
        for label in unresolved_keys:
            typer.echo(f"    - {label}")
    if duplicate_unique:
        typer.echo(
            "  ⚠️  Unique columns left with duplicate values (their generated dbt "
            "test will fail — try a lower --rows, or widen the column's value space):"
        )
        for label in duplicate_unique:
            typer.echo(f"    - {label}")
    if parse_warnings:
        typer.echo(f"  ⚠️  DBML lines model2data could not fully parse: {len(parse_warnings)}")
        for warning in parse_warnings:
            typer.echo(f"    - {warning}")

    # -------------------------
    # Done
    # -------------------------
    typer.echo("\n🎉 model2data generation complete!\n")
    typer.echo("Next steps:")
    typer.echo(f"  cd {dest}")
    typer.echo("  dbt build   # loads the seeds, builds the models, runs every test")
