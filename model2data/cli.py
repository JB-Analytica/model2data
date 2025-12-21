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
from model2data.dbt.tests import generate_dbt_yml
from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import parse_dbml
from model2data.utils import normalize_identifier

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
):
    """
    Generate synthetic data and a dbt project from a DBML model.
    """

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

    typer.echo("🪪 Ensuring dbt profile exists...")
    create_profiles_yml(dest, profile_name)

    # Keep original DBML for reference
    shutil.copy(file, dest / file.name)

    # -------------------------
    # Done
    # -------------------------
    typer.echo("\n🎉 model2data generation complete!\n")
    typer.echo("Next steps:")
    typer.echo(f"  cd {dest}")
    typer.echo("  dbt deps")
    typer.echo("  dbt seed")
    typer.echo("  dbt run")
