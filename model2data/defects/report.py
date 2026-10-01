"""What a run's defects did: `defects_report.json`, and the project's `EXPECTED_FAILURES.md`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

REPORT_FILE = "defects_report.json"
EXPECTED_FILE = "EXPECTED_FAILURES.md"

# Defects no generic dbt test catches: what each one is for, in EXPECTED_FAILURES.md.
_UNTESTED = {
    "late_arriving": (
        "an incremental model that loads `{column} > max({column})` skips these rows: "
        "they arrived after the load that covered their {column}"
    ),
    "late_updates": (
        "a snapshot with `strategy: timestamp` (or an incremental model filtering on "
        "`{column} > max({column})`) misses these versions, because {column} did not move "
        "forward; `strategy: check` catches them"
    ),
    "messy_text": (
        "leading or trailing whitespace and inconsistent casing: trim and normalise "
        "the case in staging"
    ),
}


@dataclass
class AppliedDefect:
    """One defect as a run applied it.

    `rows` are the affected rows' primary-key values as the output holds them
    (a list per row for a composite key, null where a defect nulled it;
    `row_key` names the key's columns), or 1-based row numbers when the table
    has no primary key (`row_key` None). `row_numbers` are always the rows'
    1-based numbers in the table's seed CSV: unambiguous where a duplicated key
    is not. `days` is, for the late kinds,
    the day each row arrived on, in the order of `rows`. `expected_failures`
    names the dbt tests this defect breaks; `note` says why it was not applied,
    or not in full, or what it breaks that no test sees.
    """

    table: str
    defect: str
    column: Union[str, list[str], None]
    requested: dict[str, Any]
    applied: int
    rows: list[Any] = field(default_factory=list)
    row_key: Optional[list[str]] = None
    row_numbers: list[int] = field(default_factory=list)
    days: Optional[list[int]] = None
    expected_failures: list[str] = field(default_factory=list)
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "table": self.table,
            "defect": self.defect,
            "column": self.column,
            "requested": dict(self.requested),
            "applied": self.applied,
            "rows": list(self.rows),
            "row_key": self.row_key,
            "row_numbers": list(self.row_numbers),
        }
        if self.days is not None:
            out["days"] = list(self.days)
        out["expected_failures"] = list(self.expected_failures)
        if self.note is not None:
            out["note"] = self.note
        return out


@dataclass
class ExpectedFailure:
    """A dbt test the defects break: its name as dbt gives it, and which defects break it.

    `table` is the model's table key, `column` the raw column name (None for a
    table-level test), `type` the generic test (`unique`, `not_null`, ...), and
    `severity` `error`, or `warn` for a hint test at the default severity (dbt
    reports it as WARN rather than FAIL).
    """

    test: str
    table: str
    column: Optional[str]
    type: str
    severity: str
    defects: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "test": self.test,
            "table": self.table,
            "column": self.column,
            "type": self.type,
            "severity": self.severity,
            "defects": list(self.defects),
        }


@dataclass
class DefectsReport:
    """Every defect a run applied, and every dbt test they break.

    `failing_without_defects` names the tests the data fails with no defect
    applied (a composite key the generator could not keep unique): they fail
    too, but no defect is why.
    """

    engine: str
    seed: Optional[int]
    preset: Optional[str]
    defects: list[AppliedDefect] = field(default_factory=list)
    expected_failures: list[ExpectedFailure] = field(default_factory=list)
    failing_without_defects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model2data": self.engine,
            "seed": self.seed,
            "preset": self.preset,
            "defects": [defect.to_dict() for defect in self.defects],
            "expected_failures": [failure.to_dict() for failure in self.expected_failures],
            "failing_without_defects": list(self.failing_without_defects),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str) + "\n"


def write_defects_report(report: DefectsReport, path: Path) -> Path:
    """Write `report` as JSON to `path`, or to `path/defects_report.json` for a directory."""
    target = path / REPORT_FILE if path.is_dir() else path
    target.write_text(report.to_json(), encoding="utf-8")
    return target


def write_expected_failures(report: DefectsReport, dest: Path) -> Path:
    """Write `dest/EXPECTED_FAILURES.md`: the tests `dbt build` should fail, and why."""
    target = dest / EXPECTED_FILE
    target.write_text(expected_failures_markdown(report), encoding="utf-8")
    return target


def _rows_text(defect: AppliedDefect, limit: int = 10) -> str:
    shown = [
        "(" + ", ".join(str(part) for part in row) + ")" if isinstance(row, list) else str(row)
        for row in defect.rows[:limit]
    ]
    more = len(defect.rows) - limit
    text = ", ".join(shown) + (f" and {more} more" if more > 0 else "")
    if not text:
        return "none"
    where = f"{', '.join(defect.row_key)} " if defect.row_key else "row numbers "
    return where + text


def _column_text(column: Union[str, list[str], None]) -> str:
    if column is None:
        return ""
    if isinstance(column, list):
        return ", ".join(f"`{name}`" for name in column)
    return f"`{column}`"


def expected_failures_markdown(report: DefectsReport) -> str:
    """EXPECTED_FAILURES.md's text."""
    preset = f", preset `{report.preset}`" if report.preset else ""
    seed = f"seed {report.seed}" if report.seed is not None else "no seed"
    lines = [
        "# Expected failures",
        "",
        f"This project's data was generated with deliberate defects (model2data "
        f"{report.engine}, {seed}{preset}). Every defect is counted and listed below, "
        "with the rows it broke. `dbt build` should fail exactly the tests in the first "
        "table: a test listed there that passes did not fire, and one that fails but is not "
        "listed is failing for another reason.",
        "",
        "## Tests that should fail",
        "",
    ]
    if report.expected_failures:
        lines += [
            "| Test | dbt reports | Table | Column | Broken by |",
            "| --- | --- | --- | --- | --- |",
        ]
        for failure in report.expected_failures:
            status = "WARN" if failure.severity == "warn" else "FAIL"
            lines.append(
                f"| `{failure.test}` | {status} | {failure.table} | "
                f"{_column_text(failure.column)} | {', '.join(failure.defects)} |"
            )
    else:
        lines.append("None: no defect in this run breaks a generated test.")
    untested = [
        defect for defect in report.defects if defect.defect in _UNTESTED and defect.applied
    ]
    lines += [
        "",
        "## Should be caught by your incremental logic / snapshots, not by tests",
        "",
    ]
    if untested:
        for defect in untested:
            column = defect.column if isinstance(defect.column, str) else "updated_at"
            days = sorted(set(defect.days or []))
            on = f" on day {', '.join(str(day) for day in days)}" if days else ""
            lines.append(
                f"- **{defect.table}**.`{column}`, {defect.defect} ({defect.applied} rows{on}; "
                f"{_rows_text(defect)}): {_UNTESTED[defect.defect].format(column=column)}."
            )
    else:
        lines.append("None in this run.")
    lines += [
        "",
        "## The defects",
        "",
        "| Table | Defect | Column | Asked | Applied | Rows |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for defect in report.defects:
        asked = ", ".join(f"{key} {value}" for key, value in defect.requested.items())
        lines.append(
            f"| {defect.table} | {defect.defect} | {_column_text(defect.column)} | {asked} | "
            f"{defect.applied} | {_rows_text(defect)} |"
        )
    notes = [defect for defect in report.defects if defect.note]
    if notes:
        lines += ["", "Notes:", ""]
        lines += [f"- **{d.table}**, {d.defect}: {d.note}" for d in notes]
    if report.failing_without_defects:
        lines += [
            "",
            "## Tests that fail without any defect",
            "",
            "These fail on the generated data with no defect applied, so they fail here "
            "too, but no defect is why:",
            "",
        ]
        lines += [f"- `{name}`" for name in report.failing_without_defects]
    return "\n".join(lines) + "\n"
