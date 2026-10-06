"""The CLI's metrics: `validate` on a metrics file, `generate --metrics`, `metrics list|export`."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from model2data.cli import app
from model2data.cli import main as generate_cli

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "model2data" / "spec" / "examples"
FIXTURES = ROOT / "tests" / "fixtures" / "metrics"
COFFEE = SPEC / "coffee_webshop.model2data.yml"
COFFEE_METRICS = SPEC / "coffee_webshop.metrics.yml"
TRAINING = ROOT / "examples" / "ecommerce_training.model2data.yml"
TRAINING_METRICS = FIXTURES / "ecommerce_training.metrics.yml"

runner = CliRunner()


def _invoke(args, cwd: Path):
    previous = Path.cwd()
    os.chdir(cwd)
    try:
        return runner.invoke(app, [str(arg) for arg in args])
    finally:
        os.chdir(previous)


def _hashes(directory: Path) -> dict[str, str]:
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def test_validate_checks_a_metrics_file_against_the_model_beside_it(tmp_path):
    result = _invoke(["validate", COFFEE_METRICS], tmp_path)
    assert result.exit_code == 0, result.output
    assert (
        "✅ coffee_webshop.metrics.yml conforms to metrics spec 0.1.0, checked against "
        "coffee_webshop.model2data.yml." in result.output
    )


def test_validate_takes_the_model_from_an_option(tmp_path):
    metrics = tmp_path / "mine.metrics.yml"
    shutil.copy(COFFEE_METRICS, metrics)
    result = _invoke(["validate", metrics, "--model", COFFEE], tmp_path)
    assert result.exit_code == 0, result.output
    assert "checked against coffee_webshop.model2data.yml" in result.output


def test_validate_without_a_model_checks_the_file_alone_and_says_so(tmp_path):
    metrics = tmp_path / "lonely.metrics.yml"
    metrics.write_text("model2data-metrics: 0.1.0\nmodel: lonely\nmetrics:\n  n: {count: t}\n")
    result = _invoke(["validate", metrics], tmp_path)
    assert result.exit_code == 0, result.output
    assert (
        "no model to check it against: there is no lonely.model2data.yml beside it" in result.output
    )
    assert "✅ lonely.metrics.yml conforms to metrics spec 0.1.0." in result.output


def test_validate_reports_a_metrics_files_errors(tmp_path):
    metrics = tmp_path / "coffee_webshop.metrics.yml"
    metrics.write_text(
        "model2data-metrics: 0.1.0\nmodel: coffee_webshop\nmetrics:\n"
        "  m: {measure: orders.status, agg: sum}\n"
    )
    shutil.copy(COFFEE, tmp_path / COFFEE.name)
    result = _invoke(["validate", metrics], tmp_path)
    assert result.exit_code == 1
    assert "❌ coffee_webshop.metrics.yml: 1 error" in result.output
    assert "metrics.m.agg (line 4): sum needs a numeric column" in result.output
    github = _invoke(["validate", metrics, "--format", "github"], tmp_path)
    assert "::error file=" in github.output and "line=4" in github.output


def test_validate_refuses_a_metrics_file_whose_model_does_not_conform(tmp_path):
    (tmp_path / "bad.model2data.yml").write_text("model2data: 0.4.0\ntables: {}\n")
    metrics = tmp_path / "bad.metrics.yml"
    metrics.write_text("model2data-metrics: 0.1.0\nmodel: bad\n")
    result = _invoke(["validate", metrics], tmp_path)
    assert result.exit_code == 1
    assert "its model, bad.model2data.yml, does not conform" in result.output


def test_validate_mixes_models_and_metrics_files(tmp_path):
    result = _invoke(["validate", COFFEE, COFFEE_METRICS], tmp_path)
    assert result.exit_code == 0, result.output
    assert "conforms to spec 0.2.0" in result.output
    assert "conforms to metrics spec 0.1.0" in result.output
    assert "✅ 2 model files conform." in result.output


# ---------------------------------------------------------------------------
# generate --metrics
# ---------------------------------------------------------------------------
COMMON = ["--seed", "7", "--as-of", "2026-03-15", "--rows", "60", "--name", "shop"]


def test_metrics_only_add_files(tmp_path):
    plain, with_metrics = tmp_path / "plain", tmp_path / "metrics"
    plain.mkdir()
    with_metrics.mkdir()
    first = _invoke(["generate", "-f", COFFEE, *COMMON], plain)
    assert first.exit_code == 0, first.output
    second = _invoke(["generate", "-f", COFFEE, *COMMON, "--metrics", COFFEE_METRICS], with_metrics)
    assert second.exit_code == 0, second.output
    before, after = _hashes(plain), _hashes(with_metrics)
    assert {path: after[path] for path in before} == before
    added = sorted(set(after) - set(before))
    assert added == sorted(
        [
            "dbt_shop/coffee_webshop.metrics.yml",
            "dbt_shop/metric_values.json",
            "dbt_shop/osi/shop.yml",
            *(
                f"dbt_shop/data-tests/metrics/metric_{name}.sql"
                for name in [
                    "revenue",
                    "orders",
                    "order_lines",
                    "average_order_value",
                    "web_revenue",
                    "web_share",
                    "coffee_units_sold",
                    "orders_total_amount",
                    "orders_count",
                    "order_items_quantity",
                    "order_items_count",
                ]
            ),
        ]
    )
    assert "📏 Computing each metric's known value and its dbt test..." in second.output
    assert (
        "Metrics:                 11 (metric_values.json, osi/shop.yml, data-tests/metrics/)"
        in (second.output)
    )
    values = json.loads((with_metrics / "dbt_shop" / "metric_values.json").read_text())
    assert values["run"] == {"seed": 7, "as_of": "2026-03-15"}
    assert list(values["metrics"])[:2] == ["revenue", "orders"]
    ossie = yaml.safe_load((with_metrics / "dbt_shop" / "osi" / "shop.yml").read_text())
    assert ossie["version"] == "0.1.1"


def test_generate_with_metrics_twice_writes_the_same_bytes(tmp_path):
    runs = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        result = _invoke(
            ["generate", "-f", COFFEE, *COMMON, "--metrics", COFFEE_METRICS], tmp_path / name
        )
        assert result.exit_code == 0, result.output
        runs.append(_hashes(tmp_path / name))
    assert runs[0] == runs[1]


def test_generate_refuses_a_metrics_file_with_errors_before_writing_anything(tmp_path):
    metrics = tmp_path / "broken.metrics.yml"
    metrics.write_text("model2data-metrics: 0.1.0\nmodel: someone_else\n")
    result = _invoke(["generate", "-f", COFFEE, *COMMON, "--metrics", metrics], tmp_path)
    assert result.exit_code == 1
    assert "the metrics are for model 'someone_else'" in result.output
    assert not (tmp_path / "dbt_shop").exists()


def test_generate_prints_a_metrics_files_warnings(tmp_path):
    metrics = tmp_path / "w.metrics.yml"
    metrics.write_text(
        "model2data-metrics: 0.1.0\nmodel: coffee_webshop\n"
        "metrics:\n  big: {count: orders, where: {orders.total_amount: {gt: 9000}}}\n"
    )
    result = _invoke(["generate", "-f", COFFEE, *COMMON, "--metrics", metrics], tmp_path)
    assert result.exit_code == 0, result.output
    assert "⚠️  w.metrics.yml: 1 warning" in result.output
    assert "can never match" in result.output


def test_generate_as_a_function_with_days_and_defects(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    generate_cli(
        file=TRAINING,
        rows=40,
        seed=5,
        name="training",
        force=True,
        days=2,
        metrics_file=TRAINING_METRICS,
    )
    project = tmp_path / "dbt_training"
    values = json.loads((project / "metric_values.json").read_text())
    assert values["run"] == {"seed": 5, "as_of": "2026-01-01"}
    assert (project / "data-tests" / "metrics" / "metric_odd_mix.sql").exists()
    assert values["metrics"]["odd_mix"]["value"] is None


def test_generate_with_no_metrics_in_the_file_still_says_so(tmp_path):
    metrics = tmp_path / "none.metrics.yml"
    metrics.write_text("model2data-metrics: 0.1.0\nmodel: coffee_webshop\ninfer: false\n")
    result = _invoke(["generate", "-f", COFFEE, *COMMON, "--metrics", metrics], tmp_path)
    assert result.exit_code == 0, result.output
    assert "Metrics:                 0" in result.output


# ---------------------------------------------------------------------------
# metrics list / export
# ---------------------------------------------------------------------------
def test_metrics_list(tmp_path):
    result = _invoke(["metrics", "list", "-f", COFFEE, "--metrics", COFFEE_METRICS], tmp_path)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split() == [
        "revenue",
        "simple",
        "file",
        "sum",
        "of",
        "orders.total_amount,",
        "filtered,",
        "by",
        "orders.order_date",
    ]
    assert any(line.split()[:3] == ["order_lines", "count", "file"] for line in lines)
    assert any(
        line.split()[:4] == ["average_order_value", "ratio", "file", "revenue"] for line in lines
    )
    assert any(line.split()[:3] == ["web_share", "derived", "file"] for line in lines)
    assert any(line.split()[:3] == ["orders_count", "count", "inferred"] for line in lines)


def test_metrics_list_without_a_file_lists_the_inferred_ones(tmp_path):
    result = _invoke(["metrics", "list", "-f", COFFEE], tmp_path)
    assert [line.split()[0] for line in result.output.splitlines()] == [
        "orders_total_amount",
        "orders_count",
        "order_items_quantity",
        "order_items_count",
    ]
    bare = _invoke(
        ["metrics", "list", "-f", ROOT / "examples" / "ecommerce.model2data.yml"], tmp_path
    )
    assert bare.exit_code == 0
    assert "No metrics" in bare.output


def test_metrics_export_prints_ossie(tmp_path):
    result = _invoke(["metrics", "export", "-f", COFFEE, "--metrics", COFFEE_METRICS], tmp_path)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# Apache Ossie (Open Semantic Interchange) 0.1.1")
    assert yaml.safe_load(result.output)["semantic_model"][0]["name"] == "coffee_webshop"


def test_metrics_export_writes_a_file(tmp_path):
    out = tmp_path / "coffee.osi.yml"
    args = [
        "metrics",
        "export",
        "-f",
        COFFEE,
        "--metrics",
        COFFEE_METRICS,
        "--to",
        "OSSIE",
        "-o",
        out,
    ]
    result = _invoke(args, tmp_path)
    assert result.exit_code == 0, result.output
    assert f"✅ Wrote {out} (Apache Ossie 0.1.1, 11 metrics)" in result.output
    assert "ℹ️  Not expressible in Ossie 0.1.1 itself" in result.output
    again = _invoke(args, tmp_path)
    assert again.exit_code == 1 and "already exists" in again.output
    assert _invoke([*args, "--force"], tmp_path).exit_code == 0


def test_metrics_export_without_losses_prints_none(tmp_path):
    model = tmp_path / "one.model2data.yml"
    model.write_text("model2data: 0.4.0\nname: one\ntables:\n  t:\n    columns:\n      id: int\n")
    result = _invoke(["metrics", "export", "-f", model, "-o", tmp_path / "o.yml"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "Not expressible" not in result.output


def test_metrics_export_refuses_an_unknown_format(tmp_path):
    result = _invoke(["metrics", "export", "-f", COFFEE, "--to", "metricflow"], tmp_path)
    assert result.exit_code == 2
    assert "Choose one of: ossie" in result.output


def test_metrics_with_no_command_shows_help(tmp_path):
    result = _invoke(["metrics"], tmp_path)
    assert "list" in result.output and "export" in result.output


@pytest.mark.parametrize("command", ["list", "export"])
def test_metrics_commands_refuse_a_broken_metrics_file(tmp_path, command):
    metrics = tmp_path / "x.metrics.yml"
    metrics.write_text(
        "model2data-metrics: 0.1.0\nmodel: coffee_webshop\nmetrics:\n  m: {count: nope}\n"
    )
    result = _invoke(["metrics", command, "-f", COFFEE, "--metrics", metrics], tmp_path)
    assert result.exit_code == 1
    assert "metrics.m.count" in result.output
