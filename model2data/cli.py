import random
import shutil
from collections.abc import Iterable
from datetime import date, datetime
from enum import Enum
from importlib import resources
from pathlib import Path
from typing import Any, Optional

import click
import typer
from faker import Faker
from typer.core import TyperGroup
from typer.models import ParameterInfo

from model2data.dbt.hint_tests import DEFAULT_TOLERANCE, SEVERITIES
from model2data.dbt.naming import dbt_names, for_dbt
from model2data.dbt.project import (
    create_profiles_yml,
    create_project_scaffold,
    create_staging_models,
)
from model2data.dbt.tests import generate_dbt_yml, generate_unit_tests
from model2data.defects import (
    DefectsReport,
    planned_defects,
    write_defects_report,
    write_expected_failures,
)
from model2data.generate.core import (
    generate_data_from_dbml,
    get_cyclic_tables,
    get_unresolved_composite_keys,
)
from model2data.generate.days import (
    DayResult,
    generate_days,
    write_batches,
    write_changelog,
)
from model2data.generate.faker import (
    DEFAULT_LOCALE,
    get_duplicate_unique_columns,
    get_unmapped_columns,
    reset_stats,
)
from model2data.generate.history import history_tables
from model2data.generate.options import TimeProfile
from model2data.model import (
    DEFECT_PRESETS,
    Issue,
    Model,
    ModelError,
    Shape,
    dump,
    load,
    to_engine,
    validate,
)
from model2data.output import finish_run
from model2data.utils import normalize_identifier

SUPPORTED_ADAPTERS = ("duckdb", "postgres")
DAYS_FORMATS = ("batches", "changelog", "final")


def _parse_row_overrides(
    raw: Optional[list[str]],
    tables: dict,
) -> dict[str, int]:
    """Turn repeated `--rows-for TABLE=N` values into a {table: rows} mapping.

    Fails loudly rather than silently ignoring a typo: naming a table that
    isn't in the schema almost always means a misspelling, and quietly
    generating the default row count for it would be discovered only by
    counting rows in the output.
    """
    # `main` is also called directly as a plain function (see tests/), which
    # bypasses Typer and leaves this parameter holding its `OptionInfo` default
    # rather than None. Anything that isn't an actual list means "not supplied".
    if not isinstance(raw, (list, tuple)):
        return {}

    overrides: dict[str, int] = {}
    for item in raw:
        table_name, separator, count = item.partition("=")
        table_name = table_name.strip()
        if not separator or not table_name:
            raise typer.BadParameter(f"Expected TABLE=N, got {item!r}.", param_hint="--rows-for")

        try:
            rows = int(count)
        except ValueError:
            raise typer.BadParameter(
                f"Row count for {table_name!r} must be a whole number, got {count!r}.",
                param_hint="--rows-for",
            ) from None
        if rows < 1:
            raise typer.BadParameter(
                f"Row count for {table_name!r} must be at least 1, got {rows}.",
                param_hint="--rows-for",
            )
        if table_name not in tables:
            known = ", ".join(sorted(tables)) or "none"
            raise typer.BadParameter(
                f"No table named {table_name!r} in this schema. Tables: {known}.",
                param_hint="--rows-for",
            )
        overrides[table_name] = rows
    return overrides


def _parse_table_seeds(
    raw: Optional[list[str]],
    tables: dict,
) -> dict[str, int]:
    """Turn repeated `--table-seed TABLE=N` values into a {table: seed} mapping.

    Same shape and the same loud failure on an unknown name as `--rows-for`:
    the whole point of naming a table here is to change that table, so a typo
    would otherwise produce a run in which nothing moved and nothing was said.
    """
    if not isinstance(raw, (list, tuple)):
        return {}

    table_seeds: dict[str, int] = {}
    for item in raw:
        table_name, separator, value = item.partition("=")
        table_name = table_name.strip()
        if not separator or not table_name:
            raise typer.BadParameter(f"Expected TABLE=N, got {item!r}.", param_hint="--table-seed")

        try:
            table_seed = int(value)
        except ValueError:
            raise typer.BadParameter(
                f"Seed for {table_name!r} must be a whole number, got {value!r}.",
                param_hint="--table-seed",
            ) from None
        if table_name not in tables:
            known = ", ".join(sorted(tables)) or "none"
            raise typer.BadParameter(
                f"No table named {table_name!r} in this schema. Tables: {known}.",
                param_hint="--table-seed",
            )
        table_seeds[table_name] = table_seed
    return table_seeds


class _GenerateByDefault(TyperGroup):
    """`model2data --file x` still means `model2data generate --file x`.

    The CLI was a single command before it had `validate` and `convert`; every
    script calling it that way keeps working, because anything that is not a
    subcommand's name (or `--help`) is handed to `generate`.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args or (args[0] not in self.commands and args[0] not in ("--help", "-h")):
            args = ["generate", *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_GenerateByDefault,
    help=(
        "model2data: Generate analytics-ready datasets from a data model.\n\n"
        "Given a model -- a .model2data.yml document (spec 0.3.0), the same as JSON,\n"
        "or a DBML file -- this tool produces:\n"
        "• Synthetic but realistic data\n"
        "• A runnable dbt project scaffold\n"
        "• dbt seeds, staging models, and profiles\n\n"
        "`model2data --file MODEL` generates; `validate` and `convert` check and\n"
        "convert a model. Agents: run `model2data guide` first."
    ),
    add_completion=False,
)


def _given(value: Any) -> Any:
    """`value`, or None when a direct call left the option at its Typer default.

    `main` is also called as a plain function (see tests/), which leaves every
    option it isn't passed holding its `OptionInfo` default rather than None.
    """
    return None if isinstance(value, ParameterInfo) else value


def _read_model(file: Path) -> Model:
    """The model in `file`, or exit 1 listing every issue found in it."""
    try:
        model = load(file)
    except ModelError as error:
        _print_issues(file, [*error.issues, *error.warnings])
        raise typer.Exit(1) from None
    if model.warnings:
        _print_issues(file, model.warnings)
    return model


def _print_issues(file: Path, issues: list[Issue], label: Optional[str] = None) -> None:
    """Errors, then warnings, each with its document path."""
    label = label or file.name
    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    for marker, group, word in (("❌", errors, "error"), ("⚠️ ", warnings, "warning")):
        if not group:
            continue
        count = f"1 {word}" if len(group) == 1 else f"{len(group)} {word}s"
        typer.echo(f"{marker} {label}: {count}")
        for issue in group:
            text = str(issue).removeprefix("warning: ").replace("\n", "\n      ")
            typer.echo(f"  - {text}")


def _model_stem(file: Path) -> str:
    """`orders.model2data.yml` -> `orders`: the name a model without `name` goes by."""
    name = file.name
    for suffix in (".model2data.yml", ".model2data.yaml", ".model2data.json"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return file.stem


def _dbt_names(tables: Iterable[str]) -> dict[str, str]:
    """Each table key's dbt identifier, refusing two keys that normalise alike."""
    try:
        return dbt_names(tables)
    except ValueError as exc:
        typer.echo(f"❌ {exc}")
        raise typer.Exit(1) from None


@app.command(
    "generate",
    help=(
        "Generate synthetic data and a dbt project from a model: a .model2data.yml "
        "(or .yaml/.json) document, or a DBML file. The model's run settings are the "
        "defaults; every option given here overrides them. `generate` is the default "
        "command: `model2data --file MODEL` runs it."
    ),
)
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
        help="The model: a .model2data.yml / .yaml / .json document, or a .dbml file.",
    ),
    rows: Optional[int] = typer.Option(
        None,
        "--rows",
        "-r",
        min=10,
        help="Number of rows to generate per table (default: the model's run.rows, else 100).",
    ),
    # noqa: B008 is needed on some options and not others because ruff waves a
    # call through in a default only when the annotation is one of the types it
    # knows to be immutable. `str`, `int`, `bool` and `Path` are on that list;
    # the `list` a repeatable option must be annotated with, and `datetime`,
    # are not -- neither is actually mutated here.
    rows_for: Optional[list[str]] = typer.Option(  # noqa: B008
        None,
        "--rows-for",
        metavar="TABLE=N",
        help=(
            "Row count for one table, overriding --rows. Repeatable, e.g.\n"
            "--rows-for customers=200 --rows-for orders=5000."
        ),
    ),
    seed: Optional[int] = typer.Option(
        None,
        "--seed",
        help=(
            "Optional random seed for deterministic generation.\n"
            "Using the same seed will always produce identical datasets."
        ),
    ),
    table_seed: Optional[list[str]] = typer.Option(  # noqa: B008
        None,
        "--table-seed",
        metavar="TABLE=N",
        help=(
            "Re-roll one table without disturbing the others, keeping --seed for the rest.\n"
            "Repeatable, e.g. --table-seed orders=7. Requires --seed."
        ),
    ),
    as_of: Optional[datetime] = typer.Option(  # noqa: B008
        None,
        "--as-of",
        formats=["%Y-%m-%d"],
        metavar="YYYY-MM-DD",
        help=(
            "Date to anchor generated dates and timestamps on (default: today).\n"
            "Pin it and a --seed run reproduces on any later day, not just the day it first ran."
        ),
    ),
    business_hours: Optional[bool] = typer.Option(
        None,
        "--business-hours/--no-business-hours",
        help=(
            "Weight generated timestamps toward weekdays and working hours,\n"
            "instead of spreading them evenly over every hour of every day."
        ),
    ),
    growth: Optional[float] = typer.Option(
        None,
        "--growth",
        min=-1.0,
        help=(
            "Relative change in activity across the generated window: 0.5 means the end\n"
            "is half again as busy as the start, -0.3 means it tailed off. Default: flat."
        ),
    ),
    seasonality: Optional[float] = typer.Option(
        None,
        "--seasonality",
        min=0.0,
        max=1.0,
        help=(
            "Strength of an annual cycle in generated timestamps, 0 (none) to 1,\n"
            "peaking in the fourth quarter."
        ),
    ),
    skew: Optional[float] = typer.Option(
        None,
        "--skew",
        min=0.0,
        max=1.0,
        help=(
            "How unevenly child rows are spread over their parents: 0 (every parent equally\n"
            "likely, the default) to 1 (a few parents hold most of the children)."
        ),
    ),
    locale: Optional[str] = typer.Option(
        None,
        "--locale",
        help=(
            f"Faker locale for generated people and addresses "
            f"(default: {DEFAULT_LOCALE}).\n"
            "Examples: en_GB, nl_BE, fr_FR, de_DE."
        ),
    ),
    name: Optional[str] = typer.Option(
        None,
        "--name",
        "-n",
        help=(
            "Optional override for the generated dbt project's name "
            "(default: the model's name, else the file's)."
        ),
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
    hint_tests: str = typer.Option(
        "warn",
        "--hint-tests",
        help=(
            "Write the model's generation hints as dbt tests (min/max range, after, "
            "null_rate, distinct, when, grain) at this severity: error, warn or off. They "
            "describe intent, so they warn by default rather than break a first dbt "
            "build on real data."
        ),
    ),
    test_tolerance: float = typer.Option(
        DEFAULT_TOLERANCE,
        "--test-tolerance",
        min=0.0,
        help=(
            "Absolute slack of the null_rate test: the null share may be up to "
            "null_rate + this. Range, after, distinct and grain tests have none."
        ),
    ),
    unit_tests: bool = typer.Option(
        False,
        "--unit-tests",
        help=(
            "Also generate deterministic dbt unit test fixtures (models/staging/ut_stg_*.yml) "
            "from the generated seed rows. Requires dbt-core >= 1.8 to run."
        ),
    ),
    days: Optional[int] = typer.Option(
        None,
        "--days",
        min=1,
        help=(
            "Also generate N days after the first, for the tables with `incremental`: new rows\n"
            "and updates each day. The dbt seeds hold the state after the last day."
        ),
    ),
    days_next: bool = typer.Option(
        False,
        "--next",
        help="Shorthand for --days 1.",
    ),
    defects_preset: Optional[str] = typer.Option(
        None,
        "--defects",
        metavar="PRESET",
        help=(
            "Break the data on purpose: 'clean' (no preset, the default), 'messy' (a mix on\n"
            "every table) or 'training' (each kind of dbt test fails once); a table's own\n"
            "`defects` still apply. 'none' ignores every defect, the tables' own too.\n"
            "Overrides the model's run.defects. Writes defects_report.json and\n"
            "EXPECTED_FAILURES.md into the project."
        ),
    ),
    days_format: str = typer.Option(
        "batches",
        "--days-format",
        help=(
            "How the days are written beside the dbt project: 'batches' (days/TABLE/day_NNN.csv,\n"
            "day 0 whole, later days the rows inserted or updated), 'changelog'\n"
            "(changelog/TABLE.csv, every row with _day and _op), or 'final' (only the seeds)."
        ),
    ),
):
    """
    Generate synthetic data and a dbt project from a model.
    """

    days = _given(days)
    if _given(days_next):
        if days is not None:
            raise typer.BadParameter("Use --days or --next, not both.", param_hint="--next")
        days = 1
    days_format = (_given(days_format) or "batches").lower()
    if days_format not in DAYS_FORMATS:
        raise typer.BadParameter(
            f"Choose one of: {', '.join(DAYS_FORMATS)}.", param_hint="--days-format"
        )

    # -------------------------
    # Validate adapter
    # -------------------------
    adapter = (_given(adapter) or "duckdb").lower()
    if adapter not in SUPPORTED_ADAPTERS:
        typer.echo(
            f"❌ Unsupported adapter '{adapter}'. Choose one of: {', '.join(SUPPORTED_ADAPTERS)}."
        )
        raise typer.Exit(1)

    hint_tests = (_given(hint_tests) or "warn").lower()
    if hint_tests not in SEVERITIES:
        typer.echo(f"❌ Unsupported --hint-tests '{hint_tests}'. Choose one of: error, warn, off.")
        raise typer.Exit(1)

    tolerance = _given(test_tolerance)
    tolerance = DEFAULT_TOLERANCE if tolerance is None else float(tolerance)

    preset = _given(defects_preset)
    if preset is not None:
        preset = preset.lower()
        if preset not in DEFECT_PRESETS:
            raise typer.BadParameter(
                f"Choose one of: {', '.join(DEFECT_PRESETS)}.", param_hint="--defects"
            )

    # -------------------------
    # Read the model (names untouched)
    # -------------------------
    model = _read_model(file)
    inputs = to_engine(model)
    tables, refs = inputs.tables, inputs.refs
    run = inputs.run
    shape = run.shape or Shape()

    # -------------------------
    # Settings: an option given here, else the model's run, else the default
    # -------------------------
    def pick(option: Any, from_run: Any, default: Any) -> Any:
        option = _given(option)
        if option is not None:
            return option
        return from_run if from_run is not None else default

    rows = pick(rows, run.rows, 100)
    seed = pick(seed, run.seed, None)
    locale = pick(locale, run.locale, None)
    as_of_option = _given(as_of)
    anchor: Optional[date] = (
        as_of_option.date()
        if isinstance(as_of_option, datetime)
        else date.fromisoformat(run.as_of)
        if run.as_of
        else None
    )
    time_profile = TimeProfile(
        business_hours=bool(pick(business_hours, shape.business_hours, False)),
        growth=float(pick(growth, shape.growth, 0.0)),
        seasonality=float(pick(seasonality, shape.seasonality, 0.0)),
    )
    skew = float(pick(skew, shape.skew, 0.0))

    # -------------------------
    # Deterministic seed
    # -------------------------
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)
        typer.echo(f"🔁 Using deterministic seed: {seed}")
    if anchor is not None:
        typer.echo(f"📅 Anchoring generated dates on: {anchor}")

    if not time_profile.is_uniform:
        shaped = [
            label
            for label, active in (
                ("business hours", time_profile.business_hours),
                (f"growth {time_profile.growth:+.0%}", time_profile.growth != 0.0),
                (f"seasonality {time_profile.seasonality:.0%}", time_profile.seasonality != 0.0),
            )
            if active
        ]
        typer.echo(f"🕒 Shaping timestamps: {', '.join(shaped)}")
    if skew:
        typer.echo(f"📈 Skewing child rows over their parents: {skew:.0%}")

    # Validated before anything touches the filesystem: a typo'd table name here
    # should not leave a half-scaffolded project behind for the next run to trip
    # over with a confusing "destination already exists".
    row_overrides = {**(run.rows_per_table or {}), **_parse_row_overrides(rows_for, tables)}
    table_seeds = {**(run.table_seeds or {}), **_parse_table_seeds(table_seed, tables)}
    if table_seeds and seed is None:
        raise typer.BadParameter(
            "--table-seed re-rolls one table out of the run's seed, so there has to "
            "be one. Add --seed.",
            param_hint="--table-seed",
        )
    if table_seeds:
        rolled = ", ".join(f"{key}={value}" for key, value in sorted(table_seeds.items()))
        typer.echo(f"🎲 Re-rolling with a table seed of its own: {rolled}")
    names = _dbt_names([*tables, *history_tables(inputs)])
    plan = planned_defects(model, preset, days=days or 0)
    preset = preset or (run.defects if run.defects is not None else None)
    if preset in ("messy", "training") and not plan:
        typer.echo(f"ℹ️  The {preset} preset finds nothing to break in this model: no defects.")

    project_name = normalize_identifier(_given(name) or model.name or _model_stem(file))
    dest = Path.cwd() / f"dbt_{project_name}"
    profile_name = f"{project_name}_profile"

    if dest.exists():
        if not _given(force):
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
    typer.echo("🧮 Generating synthetic datasets from the model...")
    reset_stats()
    day_results: list[DayResult] = []
    try:
        if days:
            day_results = generate_days(
                inputs,
                days,
                base_rows=rows,
                seed=seed,
                row_overrides=row_overrides,
                locale=locale,
                as_of=anchor,
                table_seeds=table_seeds,
                time_profile=time_profile,
                skew=skew,
            )
            generated_tables = day_results[-1].state
        else:
            generated_tables = generate_data_from_dbml(
                tables=tables,
                refs=refs,
                base_rows=rows,
                seed=seed,
                row_overrides=row_overrides,
                locale=locale,
                as_of=anchor,
                table_seeds=table_seeds,
                time_profile=time_profile,
                skew=skew,
            )
    except ValueError as exc:
        # A mistake in the schema the model checks could not see, not a bug --
        # it deserves the same one-line, non-traceback treatment as every other
        # validation error this command already reports.
        typer.echo(f"❌ {exc}")
        raise typer.Exit(1) from None

    # -------------------------
    # Defects: broken on purpose, after the clean data, and reported
    # -------------------------
    if plan:
        typer.echo("🧨 Breaking the data on purpose (defects)...")
    out = finish_run(
        inputs,
        day_results or generated_tables,
        plan,
        seed=seed,
        preset=preset,
        as_of=anchor,
        hint_tests=hint_tests,
        test_tolerance=tolerance,
        names=names,
    )
    report = out.report
    generated_tables = out.frames
    if day_results:
        day_results = out.days

    # -------------------------
    # dbt names: a table key becomes a [a-z0-9_] seed and model name here,
    # once, so the CSV's stem, `stg_<name>` and every `ref()` agree.
    # -------------------------
    dbt_tables, dbt_refs = for_dbt(out.tables, refs, names)
    dbt_frames = {names.get(key, key): df for key, df in generated_tables.items()}

    # -------------------------
    # Write dbt seeds (normalized names)
    # -------------------------
    seeds_path = dest / "seeds/raw"
    for table_key, df in dbt_frames.items():
        csv_path = seeds_path / f"{table_key}.csv"
        df.to_csv(csv_path, index=False)

    if day_results and days_format != "final":
        write = write_batches if days_format == "batches" else write_changelog
        write(dest, day_results, names)

    # -------------------------
    # Build dbt assets
    # -------------------------
    typer.echo("🗂️ Building staging models for generated seeds...")
    create_staging_models(dest, project_name)

    typer.echo("🧪 Generating dbt yml with tests...")
    generate_dbt_yml(
        dest,
        dbt_tables,
        dbt_refs,
        project_name,
        hint_tests=hint_tests,
        test_tolerance=tolerance,
    )

    if _given(unit_tests):
        typer.echo("🔬 Generating dbt unit test fixtures (requires dbt-core >= 1.8)...")
        generate_unit_tests(dest, dbt_tables, dbt_frames)

    typer.echo(f"🪪 Ensuring dbt profile exists ({adapter})...")
    create_profiles_yml(dest, profile_name, adapter=adapter)

    # Keep the original model file for reference
    shutil.copy(file, dest / file.name)

    if report is not None:
        write_defects_report(report, dest)
        write_expected_failures(report, dest)

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
    if day_results:
        moving = [k for k in tables if any(len(r.tables[k].inserted) for r in day_results[1:])]
        typer.echo(f"  Days generated:          {len(day_results) - 1} after the first")
        if not moving:
            typer.echo("  ⚠️  No table has `incremental`: nothing changes after the first day.")
        for result in day_results:
            for warning in result.warnings:
                typer.echo(f"  ⚠️  Day {result.day}: {warning}")
    typer.echo(f"  Relationships:           {len(refs)}")
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

    if report is not None:
        _print_defects(report)

    # -------------------------
    # Done
    # -------------------------
    typer.echo("\n🎉 model2data generation complete!\n")
    typer.echo("Next steps:")
    typer.echo(f"  cd {dest}")
    typer.echo("  dbt build   # loads the seeds, builds the models, runs every test")


def _spec_of(file: Path) -> str:
    """The spec a conforming model is written against: 0.3.0, or 0.2.0 (DBML included)."""
    return "0.3.0" if str(load(file).version).startswith("0.3") else "0.2.0"


def _print_defects(report: DefectsReport) -> None:
    typer.echo("\n🧨 Defects (defects_report.json, EXPECTED_FAILURES.md)")
    for defect in report.defects:
        column = defect.column if not isinstance(defect.column, list) else ", ".join(defect.column)
        on = f".{column}" if column else ""
        typer.echo(
            f"  {defect.table}{on}: {defect.defect}, {defect.applied} "
            f"row{'' if defect.applied == 1 else 's'}"
        )
        if defect.note and not defect.applied:
            typer.echo(f"    ⚠️  {defect.note}")
    count = len(report.expected_failures)
    typer.echo(f"  dbt build should fail {count} test{'' if count == 1 else 's'}:")
    for failure in report.expected_failures:
        warn = " (warn)" if failure.severity == "warn" else ""
        typer.echo(f"    - {failure.test}{warn}")


_SKIPPED_DIRS = {".git", ".venv", "node_modules"}


def _expand_globs(patterns: list[str]) -> list[Path]:
    """Files matching recursive glob patterns relative to the cwd, sorted, without duplicates."""
    found: dict[Path, None] = {}
    for pattern in patterns:
        for match in sorted(Path.cwd().glob(pattern)):
            relative = match.relative_to(Path.cwd())
            if match.is_file() and not _SKIPPED_DIRS.intersection(relative.parts):
                found[relative] = None
    return list(found)


def _github_escape(text: str, *, prop: bool = False) -> str:
    """Escape a workflow-command message (or property value) the way GitHub expects."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def _print_github_issues(file: Path, issues: list[Issue]) -> None:
    """One `::error` / `::warning` annotation per issue, shown inline on a pull request."""
    for issue in issues:
        level = "error" if issue.is_error else "warning"
        where = f"file={_github_escape(file.as_posix(), prop=True)}"
        if issue.line:
            where += f",line={issue.line}"
        text = f"{issue.path}: {issue.message}" if issue.path else issue.message
        typer.echo(f"::{level} {where}::{_github_escape(text)}")


@app.command("validate")
def validate_command(
    files: Optional[list[Path]] = typer.Argument(  # noqa: B008
        None,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="The models: .model2data.yml / .yaml / .json documents, or .dbml files.",
    ),
    glob: Optional[list[str]] = typer.Option(  # noqa: B008
        None,
        "--glob",
        help=(
            "A recursive glob relative to the current directory, e.g. '**/*.model2data.yml'. "
            "Repeatable; expands without the shell, so CI needs no globstar."
        ),
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        help="'text' (default) or 'github' for ::error / ::warning annotations on a pull request.",
    ),
    require_files: bool = typer.Option(
        False, "--require-files", help="Exit 1 when no model file was given or matched."
    ),
):
    """Check that models conform to spec 0.3.0 (or 0.2.x), printing every issue with its path.

    Takes one or more files and/or --glob patterns. Exits 1 when any file has an error;
    warnings are printed, and the model conforms.
    """
    if output_format not in ("text", "github"):
        raise typer.BadParameter("must be 'text' or 'github'", param_hint="--format")
    targets = list(files or [])
    if glob:
        targets += [path for path in _expand_globs(glob) if path not in targets]
    if not targets:
        typer.echo("no model files matched")
        raise typer.Exit(1 if require_files else 0)

    failed = 0
    for file in targets:
        issues = validate(file)
        if output_format == "github":
            _print_github_issues(file, issues)
        else:
            _print_issues(file, issues, label=str(file) if len(targets) > 1 else file.name)
        if any(issue.is_error for issue in issues):
            failed += 1
        elif output_format == "text":
            typer.echo(
                f"✅ {file if len(targets) > 1 else file.name} conforms to spec {_spec_of(file)}."
            )
    if len(targets) > 1 or output_format == "github":
        count = len(targets)
        checked = f"{count} model file{'s' if count != 1 else ''}"
        if failed:
            verb = "does" if failed == 1 else "do"
            typer.echo(f"❌ {failed} of {checked} {verb} not conform.")
        else:
            typer.echo(f"✅ {checked} {'conforms' if count == 1 else 'conform'}.")
    if failed:
        raise typer.Exit(1)


@app.command("convert")
def convert_command(
    file: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="The model to convert: a .dbml file (or a .model2data.yml / .json document).",
    ),
    output: Optional[Path] = typer.Option(  # noqa: B008
        None,
        "--output",
        "-o",
        dir_okay=False,
        help="Where to write the .model2data.yml document (default: print it).",
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite the output file if it exists."),
):
    """Convert a model -- DBML, typically -- to a .model2data.yml document (spec 0.2.0, or
    0.3.0 when it has defects)."""
    try:
        model = load(file)
    except ModelError as error:
        _print_issues(file, [*error.issues, *error.warnings])
        raise typer.Exit(1) from None
    text = dump(model)
    if model.warnings and output is not None:
        _print_issues(file, model.warnings)
    if output is None:
        typer.echo(text, nl=False)
        return
    if output.exists() and not force:
        typer.echo(f"❌ {output} already exists. Use --force to overwrite it.")
        raise typer.Exit(1)
    output.write_text(text, encoding="utf-8")
    typer.echo(f"✅ Wrote {output}")


class GuideTopic(str, Enum):
    setup = "setup"
    triage = "triage"
    tune = "tune"


_MODEL_PATTERNS = ("*.model2data.yml", "*.model2data.yaml", "*.model2data.json", "*.dbml")


def _detect_topic(cwd: Path) -> tuple[GuideTopic, str]:
    """`triage` when the current directory holds a model, else `setup`, and why.

    Reads file names in `cwd` only: no subdirectories, no network.
    """
    models = sorted(path.name for pattern in _MODEL_PATTERNS for path in cwd.glob(pattern))
    if models:
        return GuideTopic.triage, f"found {', '.join(models)}"
    return GuideTopic.setup, "no *.model2data.yml or *.dbml in this directory"


@app.command("guide")
def guide_command(
    topic: Optional[GuideTopic] = typer.Argument(  # noqa: B008
        None,
        help=(
            "setup: no model here yet -- install, write one, first run. "
            "triage: what validate, generate and dbt build printed, and what to do. "
            "tune: every option, model key and exit code. "
            "Default: picked from the current directory (a *.model2data.yml or *.dbml "
            "file in it means triage)."
        ),
    ),
):
    """Print instructions for an agent about to use model2data, as Markdown.

    Reads nothing but file names in the current directory and writes nothing.
    """
    if topic is None:
        topic, reason = _detect_topic(Path.cwd())
        typer.echo(f"# model2data guide: {reason} -> {topic.value}\n")
    page = resources.files("model2data").joinpath(f"guide/{topic.value}.md")
    typer.echo(page.read_text("utf-8"), nl=False)
