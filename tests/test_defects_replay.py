"""The late kinds against the loads they are meant to fool, replayed from the day files.

`late_arriving` promises rows an incremental model filtering on
`col > (select max(col) from target)` skips; `late_updates` promises versions a
`strategy: timestamp` snapshot misses (and that filter skips too). Both are
checked here by replaying the files a run writes (`days/<table>/day_NNN.csv`,
defects included) one day at a time: exactly the reported rows and versions
are missed, no more, no fewer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from test_defects import SHOP
from typer.testing import CliRunner

from model2data.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

LATE_SHOP = SHOP.replace(
    "  lines:\n",
    "    defects:\n"
    "      - {type: late_arriving, count: 6}\n"
    "      - {type: late_updates, count: 6}\n"
    "      - {type: nulls, column: amount, count: 3}\n\n"
    "  lines:\n",
).replace(
    "      created_at: {type: timestamp, not_null: true}\n",
    "      created_at: {type: timestamp, not_null: true}\n"
    "    defects:\n"
    "      - {type: late_arriving, count: 5}\n",
)


def _generate(tmp_path: Path, monkeypatch, text: str, seed: int, days: int, *extra: str) -> Path:
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "m.model2data.yml"
    model.write_text(text)
    result = runner.invoke(
        app,
        ["--file", str(model), "--seed", str(seed), "--as-of", "2026-10-01"]
        + ["--days", str(days), "--name", "p", "--force", *extra],
    )
    assert result.exit_code == 0, result.output
    return tmp_path / "dbt_p"


def _batches(project: Path, table: str) -> list[pd.DataFrame]:
    files = sorted((project / "days" / table).glob("day_*.csv"))
    return [pd.read_csv(path, dtype=str, keep_default_na=False) for path in files]


def _incremental_skips(batches: list[pd.DataFrame], column: str, key: str) -> set:
    """(key, day) of every row with a `column` that `col > max(col)` does not load."""
    loaded = max(value for value in batches[0][column] if value)
    skipped = set()
    for day, batch in enumerate(batches[1:], start=1):
        newest = loaded
        for row in batch.to_dict("records"):
            value = row[column]
            if not value:
                continue
            if pd.Timestamp(value) > pd.Timestamp(loaded):
                newest = max(newest, value, key=pd.Timestamp)
            else:
                skipped.add((row[key], day))
        loaded = newest
    return skipped


def _snapshot_misses(batches: list[pd.DataFrame], updated_at: str, key: str) -> set:
    """(key, day) of every changed version a timestamp-strategy snapshot does not capture."""
    current: dict[str, dict] = {row[key]: row for row in batches[0].to_dict("records")}
    missed = set()
    for day, batch in enumerate(batches[1:], start=1):
        for row in batch.to_dict("records"):
            seen = current.get(row[key])
            if seen is None or pd.Timestamp(row[updated_at]) > pd.Timestamp(seen[updated_at]):
                current[row[key]] = row
            elif row != seen:
                missed.add((row[key], day))
    return missed


def _reported(report: dict, table: str, kind: str) -> set:
    found = set()
    for defect in report["defects"]:
        if defect["table"] == table and defect["defect"] == kind:
            found |= {(str(r), d) for r, d in zip(defect["rows"], defect["days"], strict=True)}
    return found


def _column(report: dict, table: str, kind: str) -> str | None:
    return next(
        (d["column"] for d in report["defects"] if d["table"] == table and d["defect"] == kind),
        None,
    )


CASES = (
    [pytest.param("example", seed, id=f"training-example-{seed}") for seed in (1, 7, 42)]
    + [pytest.param("shop", seed, id=f"shop-{seed}") for seed in (3, 11, 29)]
    + [pytest.param("daily", seed, id=f"daily-training-{seed}") for seed in (2, 42)]
)


@pytest.mark.parametrize(("source", "seed"), CASES)
def test_replaying_the_loads_misses_exactly_the_reported_rows(tmp_path, monkeypatch, source, seed):
    texts = {
        "example": (EXAMPLES / "ecommerce_training.model2data.yml").read_text(),
        "daily": (EXAMPLES / "ecommerce_daily.model2data.yml").read_text(),
        "shop": LATE_SHOP,
    }
    extra = ["--defects", "training"] if source == "daily" else []
    project = _generate(tmp_path, monkeypatch, texts[source], seed, 5, *extra)
    report = json.loads((project / "defects_report.json").read_text())
    checked = 0
    for table in ("customers", "orders"):
        batches = _batches(project, table)
        arriving = _reported(report, table, "late_arriving")
        updates = _reported(report, table, "late_updates")
        event = _column(report, table, "late_arriving")
        if event is not None:
            # Late updates of the same column are skipped by the same filter.
            same = updates if _column(report, table, "late_updates") == event else set()
            assert _incremental_skips(batches, event, "id") == arriving | same
            checked += 1
        updated_at = _column(report, table, "late_updates")
        if updated_at is not None:
            assert _snapshot_misses(batches, updated_at, "id") == updates
            checked += 1
    assert checked >= 2
