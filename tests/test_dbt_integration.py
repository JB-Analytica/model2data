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
production code just did. These tests are the one thing that authoritatively
proves dbt itself finds and runs what model2data generates.

The seed-ordering bug (staging models reading from a `source` that had no DAG
edge back to the seed behind it, so a one-pass `dbt build` on a fresh database
could run a model before its seed) survived just as long, and for the same
reason -- CI ran `dbt seed` and `dbt run` as separate, already-ordered steps
and never a bare `dbt build` from clean. Hence
test_bare_dbt_build_succeeds_on_a_fresh_database below.

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

    # No `dbt seed`/`dbt run` warm-up: staging models `ref()` their seeds, so
    # the single `dbt build` below orders seeds before models on its own.
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


@pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")
def test_bare_dbt_build_succeeds_on_a_fresh_database(tmp_path, monkeypatch):
    """A bare `dbt build` must succeed on a freshly generated project against a
    completely fresh database, with no `dbt seed`/`dbt run` warm-up.

    This is the regression test for the ordering bug that shipped through the
    project's whole history: staging models read from `source('raw', <seed>)`,
    and a dbt source carries no DAG edge to the seed that materializes it. `dbt
    build` runs seeds and models in one DAG pass, so with nothing forcing the
    order it could schedule a staging model before its seed existed and fail
    with "Table with name <seed> does not exist". Nothing caught it because CI
    only ever ran `dbt seed` and `dbt run` as separate, already-ordered steps.
    Staging models now `ref()` their seeds, which is a real DAG edge.

    Kept deliberately narrow -- one `dbt deps`, one `dbt build`, nothing
    else -- so it stays a direct statement of the invariant even if the
    node-discovery test above changes shape.
    """
    monkeypatch.chdir(tmp_path)

    generate_cli(
        file=EXAMPLE_DBML,
        rows=50,
        seed=42,
        name="freshbuild",
        force=True,
        adapter="duckdb",
        unit_tests=True,
    )

    project_dir = tmp_path / "dbt_freshbuild"
    assert not list(project_dir.glob("*.duckdb")), (
        "the generated project must start with no database file for this test to mean anything"
    )

    _run_dbt("deps", cwd=project_dir)
    result = _run_dbt("build", cwd=project_dir)

    assert "ERROR=0" in result.stdout, f"`dbt build` reported errors:\n{result.stdout}"
