"""The metric tests against dbt itself: `dbt build` computes every metric and gets its known value.

`metric_values.json` is computed in Python, on DuckDB, from the frames the run
wrote. Each generated `data-tests/metrics/metric_<name>.sql` computes the same
metric over the project's staging models, as the warehouse loaded them from
the seeds. These cases run a real `dbt build` and require every metric test to
pass: on the reference example, on a model with every kind of column and join,
and on the training model with its defects (orphans, nulls, invalid values,
messy text, duplicate keys) over several days. One more edits a known value
and requires its test to fail, so a passing test is known to check something.
The shipped examples with `--lightdash` must `dbt parse` cleanly, and, when
the Lightdash CLI is on PATH too, compile every explore with
`lightdash compile --no-partial-compilation`.

Run with the project's own dbt first on PATH (`PATH="$PWD/.venv/bin:$PATH"`).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from model2data.cli import main as generate_cli

DBT = shutil.which("dbt")
LIGHTDASH = shutil.which("lightdash")
ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "model2data" / "spec" / "examples"
FIXTURES = ROOT / "tests" / "fixtures" / "metrics"

pytestmark = pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")


def _build(project: Path, *args: str) -> dict[str, str]:
    """Each test's status after `dbt build`; a model or seed that does not succeed fails here."""
    result = subprocess.run(
        [str(DBT), "build", "--profiles-dir", ".", *args],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    run_results = project / "target" / "run_results.json"
    assert run_results.exists(), result.stdout + result.stderr
    statuses = {}
    for node in json.loads(run_results.read_text())["results"]:
        unique_id, status = node["unique_id"], node["status"]
        if unique_id.startswith("test."):
            statuses[unique_id.split(".")[2]] = status
        else:
            assert status == "success", f"{unique_id} {status}:\n{result.stdout}"
    return statuses


def _metric_tests(statuses: dict[str, str]) -> dict[str, str]:
    return {name: status for name, status in statuses.items() if name.startswith("metric_")}


@pytest.mark.parametrize(
    "model, metrics, options",
    [
        pytest.param(
            SPEC / "coffee_webshop.model2data.yml",
            SPEC / "coffee_webshop.metrics.yml",
            {"rows": 300, "seed": 7},
            id="coffee-example",
        ),
        pytest.param(
            FIXTURES / "shop.model2data.yml",
            FIXTURES / "shop.metrics.yml",
            {"rows": 80, "seed": 3},
            id="every-kind-and-join",
        ),
        pytest.param(
            ROOT / "examples" / "ecommerce_training.model2data.yml",
            FIXTURES / "ecommerce_training.metrics.yml",
            {"rows": 60, "days": 3},
            id="training-defects-and-days",
        ),
        pytest.param(
            ROOT / "examples" / "ecommerce_training.model2data.yml",
            FIXTURES / "ecommerce_training.metrics.yml",
            {"rows": 60, "days": 2, "defects_preset": "messy"},
            id="messy-preset",
        ),
    ],
)
def test_every_metric_test_passes_in_dbt_build(tmp_path, monkeypatch, model, metrics, options):
    monkeypatch.chdir(tmp_path)
    generate_cli(
        file=model, force=True, adapter="duckdb", name="metrics", metrics_file=metrics, **options
    )
    project = tmp_path / "dbt_metrics"
    values = json.loads((project / "metric_values.json").read_text())
    statuses = _metric_tests(_build(project))
    assert set(statuses) == {f"metric_{name}" for name in values["metrics"]}
    assert all(status == "pass" for status in statuses.values()), statuses


def test_a_wrong_known_value_fails_its_test(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    generate_cli(
        file=SPEC / "coffee_webshop.model2data.yml",
        rows=50,
        seed=1,
        force=True,
        name="metrics",
        metrics_file=SPEC / "coffee_webshop.metrics.yml",
    )
    project = tmp_path / "dbt_metrics"
    test = project / "data-tests" / "metrics" / "metric_revenue.sql"
    text = test.read_text()
    # Move the known value by twice the tolerance.
    text = re.sub(
        r"abs\(value - ([0-9.]+)\) > ([0-9.]+)",
        lambda m: f"abs(value - ({m.group(1)} + 2 * {m.group(2)})) > {m.group(2)}",
        text,
    )
    test.write_text(text)
    statuses = _metric_tests(_build(project))
    assert statuses.pop("metric_revenue") == "fail"
    assert set(statuses.values()) == {"pass"}


@pytest.mark.parametrize(
    "model, metrics",
    [
        pytest.param(
            SPEC / "coffee_webshop.model2data.yml",
            SPEC / "coffee_webshop.metrics.yml",
            id="coffee-example",
        ),
        pytest.param(
            ROOT / "examples" / "ecommerce.model2data.yml",
            ROOT / "examples" / "ecommerce.metrics.yml",
            id="ecommerce-example",
        ),
        pytest.param(
            FIXTURES / "shop.model2data.yml",
            FIXTURES / "shop.metrics.yml",
            id="every-kind-and-join",
        ),
    ],
)
def test_the_lightdash_meta_parses_and_compiles(tmp_path, monkeypatch, model, metrics):
    monkeypatch.chdir(tmp_path)
    generate_cli(
        file=model, rows=40, seed=2, force=True, name="lit", metrics_file=metrics, lightdash=True
    )
    project = tmp_path / "dbt_lit"
    parse = subprocess.run(
        [str(DBT), "parse", "--profiles-dir", ".", "--no-partial-parse"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert parse.returncode == 0, parse.stdout + parse.stderr
    assert "warn" not in parse.stdout.lower(), parse.stdout
    manifest = json.loads((project / "target" / "manifest.json").read_text())
    metas = [
        node["config"]["meta"]
        for node in manifest["nodes"].values()
        if node["resource_type"] == "model"
    ]
    assert metas and all("label" in meta for meta in metas)
    if LIGHTDASH is None:
        return
    compiled = subprocess.run(
        [
            LIGHTDASH,
            "compile",
            "--project-dir",
            ".",
            "--profiles-dir",
            ".",
            "--no-warehouse-credentials",
            "--no-partial-compilation",
            "--no-version-check",
        ],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert "ERRORS=0" in compiled.stdout + compiled.stderr
