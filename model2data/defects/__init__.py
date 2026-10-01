"""Data that breaks things on purpose: deliberate, counted, deterministic defects.

A model says which defects a run puts in its data -- per table under `defects`,
or as a preset under `run.defects` (spec 0.3.0, "Defects") -- and a run applies
them after the clean data is generated, so the clean data stays what it was.
Every defect is reported: which rows it broke, and which dbt tests of the
generated project now fail, so a test that does not fire is visible.

The CLI is one caller; the studio composes the same steps itself:

    >>> from model2data.model import load, to_engine
    >>> from model2data.generate.core import generate_data_from_dbml
    >>> from model2data.defects import planned_defects, apply_defects
    >>> model = load("examples/ecommerce.model2data.yml")
    >>> plan = planned_defects(model, "training")          # what will break, before generating
    >>> inputs = to_engine(model)
    >>> frames = generate_data_from_dbml(inputs.tables, inputs.refs, seed=42)
    >>> frames, report = apply_defects(inputs, frames, plan, seed=42, preset="training")
    >>> report.to_dict()["expected_failures"]               # the tests `dbt build` fails

and, once the dbt project is written, `write_defects_report(report, dest)` and
`write_expected_failures(report, dest)`. `model2data.output.finish_run` does the
same and also builds the tables' histories (`incremental.history`), which
`overlapping_history` breaks: it is the one call the CLI makes, after
`generate_days`.
"""

from model2data.defects.apply import apply_defects, defect_stream_seed, requested_rows
from model2data.defects.checks import as_seeded, failing
from model2data.defects.presets import default_column, planned_defects, preset_defects
from model2data.defects.report import (
    EXPECTED_FILE,
    REPORT_FILE,
    AppliedDefect,
    DefectsReport,
    ExpectedFailure,
    expected_failures_markdown,
    write_defects_report,
    write_expected_failures,
)

__all__ = [
    "EXPECTED_FILE",
    "REPORT_FILE",
    "AppliedDefect",
    "DefectsReport",
    "ExpectedFailure",
    "apply_defects",
    "as_seeded",
    "default_column",
    "defect_stream_seed",
    "expected_failures_markdown",
    "failing",
    "planned_defects",
    "preset_defects",
    "requested_rows",
    "write_defects_report",
    "write_expected_failures",
]
