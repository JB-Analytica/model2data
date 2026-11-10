"""`model2data dashboard check`, `validate` on a dashboard, and `guide dashboards`."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model2data.cli import app

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"
LINE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?P<column>\d+): (?P<severity>error|warning): .+$"
)

TILE = """\
model2data-dashboard: 0.1.0
pages:
  - id: p
    name: P
    tiles:
      - id: t
        kind: kpi
        metric: {metric}
        place: {{x: 0, y: 0, w: 3, h: 2}}
"""


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    """A folder with the example model and metrics, and nothing else."""
    for name in ("coffee_webshop.model2data.yml", "coffee_webshop.metrics.yml"):
        shutil.copy(EXAMPLES / name, tmp_path / name)
    return tmp_path


def _write(folder: Path, name: str, metric: str = "revenue", text: str | None = None) -> Path:
    path = folder / name
    path.write_text(text if text is not None else TILE.format(metric=metric), encoding="utf-8")
    return path


def test_the_example_checks_clean():
    file = EXAMPLES / "coffee_webshop_sales.dashboard.yml"
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 0, result.output
    assert result.output == (
        f"{file}: 0 errors, 0 warnings, checked against coffee_webshop.model2data.yml and "
        "coffee_webshop.metrics.yml\n"
    )


def test_errors_print_file_line_column_and_exit_1(shop):
    file = _write(
        shop, "sales.dashboard.yml", text=TILE.format(metric="revenue").replace("kpi", "chart")
    )
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 1
    first, summary = result.output.splitlines()
    match = LINE.match(first)
    assert match is not None, first
    assert match["file"] == str(file)
    assert (match["line"], match["column"], match["severity"]) == ("7", "15", "error")
    assert "kind should be" in first
    assert summary.startswith(f"{file}: 1 error, 0 warnings, checked against")


def test_warnings_alone_exit_0(shop):
    file = _write(shop, "sales.dashboard.yml", metric="revnue")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 0, result.output
    first = result.output.splitlines()[0]
    assert first == (
        f"{file}:8:17: warning: tile 't': metric 'revnue' is not a metric of this model "
        "(did you mean 'revenue'?)."
    )


def test_many_files_exit_1_when_one_has_an_error(shop):
    good = _write(shop, "good.dashboard.yml")
    bad = _write(shop, "bad.dashboard.yml", text="model2data-dashboard: 0.1.0\nfoo: 1\n")
    result = runner.invoke(app, ["dashboard", "check", str(good), str(bad)])
    assert result.exit_code == 1
    assert f"{good}: 0 errors, 0 warnings" in result.output
    assert f"{bad}:2:1: error: the file has unknown key 'foo'" in result.output


def test_the_model_s_implied_metrics_without_a_metrics_file(shop):
    (shop / "coffee_webshop.metrics.yml").unlink()
    file = _write(shop, "sales.dashboard.yml", metric="revenue")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert "metric 'revenue' is not a metric of this model" in result.output
    assert "checked against coffee_webshop.model2data.yml\n" in result.output
    file = _write(shop, "sales.dashboard.yml", metric="orders_total_amount")
    assert "0 warnings" in runner.invoke(app, ["dashboard", "check", str(file)]).output


def test_no_model_beside_it_is_a_note(tmp_path):
    file = _write(tmp_path, "sales.dashboard.yml", metric="anything")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 0
    assert result.output.splitlines()[0] == (
        f"{file}: warning: no model beside it: its metric, table and column names are not "
        "checked; pass --model to check them"
    )


def test_the_model_is_found_by_model_then_alone(shop):
    other = shop / "other.model2data.yml"
    other.write_text(
        "model2data: 0.5.0\nname: other\ntables:\n  t:\n    columns:\n      id: {type: int, pk: true}\n",
        encoding="utf-8",
    )
    # Two models: `model:` names one.
    text = TILE.format(metric="revenue").replace("pages:", "model: coffee_webshop\npages:")
    file = _write(shop, "sales.dashboard.yml", text=text)
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert "0 warnings, checked against coffee_webshop.model2data.yml" in result.output
    # A model stem in the file name picks nothing: the file name is only the name.
    file = _write(shop, "coffee_webshop_sales.dashboard.yml")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert "warning: several models beside it" in result.output
    # Neither: two models are too many to guess from.
    file = _write(shop, "plain.dashboard.yml")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert (
        "warning: several models beside it (coffee_webshop.model2data.yml, other" in result.output
    )
    # --model chooses.
    result = runner.invoke(app, ["dashboard", "check", str(file), "--model", str(other)])
    assert "metric 'revenue' is not a metric of this model" in result.output
    assert "checked against other.model2data.yml" in result.output


def test_metrics_can_be_given(shop, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    metrics = elsewhere / "mine.metrics.yml"
    metrics.write_text(
        "model2data-metrics: 0.1.0\nmodel: coffee_webshop\ninfer: false\nmetrics:\n"
        "  sales:\n    measure: orders.total_amount\n",
        encoding="utf-8",
    )
    file = _write(shop, "sales.dashboard.yml", metric="sales")
    result = runner.invoke(app, ["dashboard", "check", str(file), "--metrics", str(metrics)])
    assert result.exit_code == 0
    assert "0 warnings, checked against coffee_webshop.model2data.yml and mine.metrics.yml" in (
        result.output
    )


def test_a_broken_model_or_metrics_file_is_an_error(shop):
    file = _write(shop, "sales.dashboard.yml")
    metrics = shop / "coffee_webshop.metrics.yml"
    metrics.write_text(
        "model2data-metrics: 0.1.0\nmodel: coffee_webshop\nbad: 1\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 1
    assert "error: its metrics file, coffee_webshop.metrics.yml, does not conform" in result.output
    (shop / "coffee_webshop.model2data.yml").write_text(
        "model2data: 0.5.0\nbad: 1\n", encoding="utf-8"
    )
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 1
    assert "error: its model, coffee_webshop.model2data.yml, does not conform" in result.output


def test_a_file_name_that_breaks_the_name_rule_is_a_warning(shop):
    file = _write(shop, "Sales Board.dashboard.yml")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 0
    assert "warning: its file name gives the dashboard the name 'Sales Board'" in result.output
    file = _write(shop, "coffee_webshop.sales.dashboard.yml")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 0
    assert (
        "warning: its file name gives the dashboard the name 'coffee_webshop.sales'"
        in result.output
    )


def test_validate_takes_a_dashboard():
    file = EXAMPLES / "coffee_webshop_sales.dashboard.yml"
    result = runner.invoke(app, ["validate", str(file)])
    assert result.exit_code == 0, result.output
    assert result.output == (
        "✅ coffee_webshop_sales.dashboard.yml conforms to dashboard spec 0.1.0, checked against "
        "coffee_webshop.model2data.yml.\n"
    )


def test_validate_reports_a_dashboard_s_errors_with_paths(shop):
    file = _write(shop, "sales.dashboard.yml", text="model2data-dashboard: 0.1.0\nfoo: 1\n")
    result = runner.invoke(app, ["validate", str(file), "--format", "github"])
    assert result.exit_code == 1
    assert f"::error file={file.as_posix().replace(':', '%3A')},line=2::foo%3A" not in result.output
    assert ",line=2::foo: the file has unknown key 'foo'" in result.output


def test_dashboard_without_a_subcommand_shows_help():
    result = runner.invoke(app, ["dashboard"])
    assert "check" in result.output


def test_the_guide_has_a_dashboards_topic():
    result = runner.invoke(app, ["guide", "dashboards"])
    assert result.exit_code == 0
    assert "model2data dashboard check" in result.output
    assert "https://studio.jbanalytica.com" in result.output


def test_an_unreadable_file_still_reports(shop):
    file = _write(shop, "sales.dashboard.yml", text="model2data-dashboard: 0.1.0\ntitle: [a\n")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 1
    assert "error: Not valid YAML:" in result.output
    file.write_bytes(b"model2data-dashboard: 0.1.0\ntitle: \xff\n")
    result = runner.invoke(app, ["dashboard", "check", str(file)])
    assert result.exit_code == 1
    assert f"{file}: error: sales.dashboard.yml is not UTF-8" in result.output
