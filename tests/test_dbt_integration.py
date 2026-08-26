"""
End-to-end regression test that shells out to the real dbt CLI.

Bug #1 (composite-key singular test written outside `test-paths`) and Bug #2
(`--unit-tests` YAML written outside any `*-paths` dir) both shipped in v0.4.1
undetected because nothing in CI ever asked dbt to actually *discover* the
generated nodes -- CI only ran `dbt seed`/`dbt run`, never `dbt test`/`dbt
build`/`dbt ls`. Pure Python assertions on file paths (see
test_dbt_tests.py::test_generated_file_paths_match_dbt_project_yml_resource_paths)
can't catch this class of bug on their own either, because they can drift out
of sync with `dbt_project.yml`'s actual config exactly the same way the
production code just did. This test is the one thing that authoritatively
proves dbt itself finds and runs what model2data generates.

dbt-core and dbt-duckdb are base dependencies (not the postgres extra), and
DuckDB is file-based with no external service, so this needs nothing beyond
`uv sync --extra dev` and runs as a normal (if slower) part of the suite.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from model2data.cli import main as generate_cli

DBT = shutil.which("dbt")
EXAMPLE_DBML = Path(__file__).resolve().parent.parent / "examples" / "advanced_features.dbml"

EXPECTED_UNIT_TESTS = [
    "test_stg_departments_passthrough",
    "test_stg_employees_passthrough",
    "test_stg_expenses_passthrough",
    "test_stg_performance_ratings_passthrough",
    "test_stg_project_assignments_passthrough",
    "test_stg_projects_passthrough",
    "test_stg_time_entries_passthrough",
]


def _run_dbt(*args, cwd):
    result = subprocess.run(
        [DBT, *args, "--profiles-dir", "."],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"`dbt {' '.join(args)}` failed (exit {result.returncode}):\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    return result


@pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")
def test_generated_project_is_discovered_and_builds_clean(tmp_path, monkeypatch):
    """Generate a project from examples/advanced_features.dbml (enum column,
    composite unique key, self-referencing FK, --unit-tests) and confirm dbt
    itself finds the composite-key test and every unit test, then confirm the
    whole project -- including the self-referencing FK relationship test --
    passes a real `dbt build`."""
    monkeypatch.chdir(tmp_path)

    generate_cli(
        file=EXAMPLE_DBML,
        rows=50,
        seed=42,
        name="integration",
        force=True,
        adapter="duckdb",
        unit_tests=True,
    )

    project_dir = tmp_path / "dbt_integration"
    assert project_dir.exists()

    _run_dbt("deps", cwd=project_dir)

    # Seed and run first: dbt has no DAG edge from a seed to the source it
    # backs, so on a completely fresh database `dbt build` alone can schedule
    # a unit test (which needs to introspect the real relation's columns)
    # before the seed that creates that relation. Seeding first sidesteps
    # that ordering gap; it's not related to any of the three bugs this test
    # guards against.
    _run_dbt("seed", cwd=project_dir)
    _run_dbt("run", cwd=project_dir)

    test_nodes = _run_dbt("ls", "--resource-type", "test", cwd=project_dir).stdout
    assert "unique_combination_stg_project_assignments_project_id_employee_id" in test_nodes, (
        "composite-key singular test not discovered by dbt -- "
        "check it's written under test-paths (Bug #1)"
    )
    assert "relationships_stg_employees_manager_id__id__ref_stg_employees_" in test_nodes

    unit_test_nodes = _run_dbt("ls", "--resource-type", "unit_test", cwd=project_dir).stdout
    for name in EXPECTED_UNIT_TESTS:
        assert name in unit_test_nodes, (
            f"unit test {name} not discovered by dbt -- "
            "check it's written under model-paths (Bug #2)"
        )

    _run_dbt("build", cwd=project_dir)
