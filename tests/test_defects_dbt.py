"""The defects report against dbt itself: `dbt build` fails exactly the tests it names.

The report's `expected_failures` is a prediction made in Python. This is where
it is held to account: each case generates a project with defects, runs a real
`dbt build` on DuckDB, and compares the tests dbt reports as failing (FAIL, or
WARN for a hint test at its default severity) with the report -- no more, no
fewer. A test the report names that passes would be a defect that did not
fire; one that fails unnamed, a defect breaking more than it says.

Run with the project's own dbt first on PATH (`PATH="$PWD/.venv/bin:$PATH"`): a
global dbt older than 1.10 rejects the `arguments:` blocks the project writes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_defects import SHOP
from test_history import HISTORY

from model2data.cli import main as generate_cli
from model2data.model import Defect, dump, load

DBT = shutil.which("dbt")
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

pytestmark = pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")


def _build(project: Path) -> set[str]:
    """The tests `dbt build` reports as failing (FAIL or WARN); any other error fails here."""
    result = subprocess.run(
        [str(DBT), "build", "--profiles-dir", "."],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    run_results = project / "target" / "run_results.json"
    assert run_results.exists(), result.stdout + result.stderr
    failing = set()
    for node in json.loads(run_results.read_text())["results"]:
        unique_id, status = node["unique_id"], node["status"]
        if unique_id.startswith("test."):
            if status in ("fail", "warn"):
                failing.add(unique_id.split(".")[2])
            assert status != "error", f"{unique_id} errored:\n{result.stdout}"
        else:
            assert status == "success", f"{unique_id} {status}:\n{result.stdout}"
    return failing


def _generate(tmp_path, monkeypatch, model_text: str, **options) -> Path:
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "shop.model2data.yml"
    model.write_text(model_text, encoding="utf-8")
    generate_cli(file=model, force=True, adapter="duckdb", name="defects", **options)
    return tmp_path / "dbt_defects"


def _report(project: Path) -> dict:
    return json.loads((project / "defects_report.json").read_text())


def _with(table: str, *defects: Defect) -> str:
    model = load(SHOP)
    model.tables[table].defects = list(defects)
    return dump(model)


@pytest.mark.parametrize(
    ("table", "defect", "days", "expected"),
    [
        pytest.param(
            "customers",
            Defect("duplicate_keys", count=3),
            None,
            {"unique_stg_customers_id"},
            id="duplicate_keys",
        ),
        pytest.param(
            "customers",
            Defect("duplicate_keys", column="email", count=2),
            None,
            {"unique_stg_customers_email"},
            id="duplicate_keys-unique-column",
        ),
        pytest.param(
            "lines",
            Defect("duplicate_keys", count=2),
            None,
            {"unique_combination_stg_lines_order_id_line"},
            id="duplicate_keys-composite",
        ),
        pytest.param(
            "orders",
            Defect("orphan_foreign_keys", column="customer_id", share=0.1),
            None,
            {"relationships_stg_orders_customer_id__id__ref_stg_customers_"},
            id="orphan_foreign_keys",
        ),
        pytest.param(
            "customers",
            Defect("nulls", column="name", count=3),
            None,
            {"not_null_stg_customers_name"},
            id="nulls",
        ),
        pytest.param(
            "customers",
            Defect("invalid_values", column="tier", count=4),
            None,
            {"accepted_values_stg_customers_tier__bronze__silver__gold"},
            id="invalid_values",
        ),
        pytest.param(
            "customers",
            Defect("messy_text", column="nickname", count=5),
            None,
            set(),
            id="messy_text",
        ),
        pytest.param(
            "customers",
            Defect("messy_text", column="segment", count=5),
            None,
            {"model2data_max_distinct_stg_customers_segment__4"},
            id="messy_text-breaks-a-hint-test",
        ),
        pytest.param("orders", Defect("late_arriving", count=3), 3, set(), id="late_arriving"),
        pytest.param("orders", Defect("late_updates", count=3), 3, set(), id="late_updates"),
    ],
)
def test_dbt_fails_exactly_the_tests_the_report_names(
    tmp_path, monkeypatch, table, defect, days, expected
):
    project = _generate(tmp_path, monkeypatch, _with(table, defect), days=days)
    report = _report(project)
    (applied,) = report["defects"]
    assert applied["applied"] == (defect.count or applied["applied"]) > 0
    assert report["failing_without_defects"] == []
    named = {failure["test"] for failure in report["expected_failures"]}
    assert named == expected
    assert _build(project) == named


@pytest.mark.parametrize(
    ("example", "preset", "days"),
    [
        pytest.param("ecommerce", "training", None, id="training"),
        pytest.param("ecommerce", "messy", None, id="messy"),
        pytest.param("ecommerce_daily", "training", 2, id="training-days"),
        pytest.param("ecommerce_daily", "messy", 2, id="messy-days"),
        # The example's own run: the training preset, and defects of its tables.
        pytest.param("ecommerce_training", None, 3, id="example"),
    ],
)
def test_a_preset_fails_exactly_the_tests_the_report_names(
    tmp_path, monkeypatch, example, preset, days
):
    text = (EXAMPLES / f"{example}.model2data.yml").read_text()
    project = _generate(
        tmp_path, monkeypatch, text, days=days, seed=42, rows=60, defects_preset=preset
    )
    report = _report(project)
    named = {failure["test"] for failure in report["expected_failures"]}
    assert report["failing_without_defects"] == []
    assert _build(project) == named
    if report["preset"] == "training":
        # Each standard kind of test fails exactly once, and a kept history's overlap test.
        kinds = sorted(failure["type"] for failure in report["expected_failures"])
        expected_kinds = ["accepted_values", "not_null", "relationships", "unique"]
        if example == "ecommerce_training":
            expected_kinds.append("model2data_no_overlapping_ranges")
        assert kinds == sorted(expected_kinds)
        if days:
            late = {d["defect"] for d in report["defects"] if d["defect"].startswith("late")}
            assert late == {"late_arriving", "late_updates"}


PARENT_TEST = (
    "model2data_not_before_parent_stg_orders_ordered_at"
    "__id__customer_id__created_at__ref_stg_customers_"
)


def test_the_parent_dates_test_passes_clean_and_fails_a_customer_created_late(
    tmp_path, monkeypatch
):
    """Orders follow their customers' `created_at`: dbt agrees, and catches one that does not."""
    project = _generate(tmp_path, monkeypatch, SHOP)
    assert (project / "macros" / "model2data_parent_tests.sql").exists()
    assert _build(project) == set()

    seeds = project / "seeds" / "raw"
    orders = (seeds / "orders.csv").read_text().splitlines()
    customer = orders[1].split(",")[1]
    lines = (seeds / "customers.csv").read_text().splitlines()
    header = lines[0].split(",")
    at, key = header.index("created_at"), header.index("id")
    for number, line in enumerate(lines[1:], start=1):
        cells = line.split(",")
        if cells[key] == customer:
            cells[at] = "2099-01-01 00:00:00"
            lines[number] = ",".join(cells)
    (seeds / "customers.csv").write_text("\n".join(lines) + "\n")
    assert _build(project) == {PARENT_TEST}


def test_a_history_fails_only_its_overlap_test(tmp_path, monkeypatch):
    model = load(HISTORY)
    model.tables["orders"].defects = [Defect("overlapping_history", count=3)]
    project = _generate(tmp_path, monkeypatch, dump(model), days=3)
    assert (project / "seeds" / "raw" / "orders_history.csv").exists()
    assert (project / "macros" / "model2data_history_tests.sql").exists()
    named = {failure["test"] for failure in _report(project)["expected_failures"]}
    assert named == {"model2data_no_overlapping_ranges_stg_orders_history_id__valid_from__valid_to"}
    assert _build(project) == named


def test_a_clean_history_passes_both_its_tests(tmp_path, monkeypatch):
    project = _generate(tmp_path, monkeypatch, HISTORY, days=3)
    assert not (project / "defects_report.json").exists()
    assert _build(project) == set()


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "defects"


@pytest.mark.parametrize(
    ("fixture", "options"),
    [
        # late_updates on a table with a nullable integer column (it crashed on pd.NA).
        pytest.param("lu2", {"days": 3, "defects_preset": "messy"}, id="late-updates-nullable-int"),
        # Orphans of a uuid and a date foreign key, duplicates of a date, a uuid and a decimal.
        pytest.param("pkd", {}, id="typed-orphans-and-duplicates"),
        # An orphan date on a column with an `after` hint (generation crashed).
        pytest.param("dfk", {}, id="orphan-date-with-after"),
        # Duplicates, then nulls and messy text over the same keys: nothing undoes them.
        pytest.param("dupnull", {}, id="no-defect-undoes-another"),
        # A history of a table with a column named `_position`.
        pytest.param("hcol", {"days": 2}, id="history-keeps-every-column"),
        # messy on small tables, where a later defect used to undo a duplicate.
        pytest.param("shop", {"defects_preset": "messy", "rows": 12, "seed": 15}, id="messy-15"),
        pytest.param("shop", {"defects_preset": "messy", "rows": 12, "seed": 53}, id="messy-53"),
    ],
)
def test_review_cases_fail_exactly_the_tests_the_report_names(
    tmp_path, monkeypatch, fixture, options
):
    text = (FIXTURES / f"{fixture}.model2data.yml").read_text() if fixture != "shop" else SHOP
    project = _generate(tmp_path, monkeypatch, text, **options)
    failed = _build(project)
    report_file = project / "defects_report.json"
    report = (
        _report(project)
        if report_file.exists()
        else {"expected_failures": [], "failing_without_defects": []}
    )
    named = {failure["test"] for failure in report["expected_failures"]}
    assert failed == named | set(report["failing_without_defects"])
    for defect in report.get("defects", []):
        assert defect["applied"] == len(defect["rows"]) == len(defect["row_numbers"])
