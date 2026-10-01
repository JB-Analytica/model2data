"""The dbt tests a model's hints imply: mapping, options, macros, and dbt itself."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.hint_tests import (
    HintTest,
    hint_tests_for,
    write_hint_macros,
)
from model2data.dbt.tests import generate_dbt_yml
from model2data.model import from_dict, to_engine

runner = CliRunner()
DBT = shutil.which("dbt")
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

HINTED = {
    "model2data": "0.2.0",
    "enums": {"status": ["new", "paid"]},
    "tables": {
        "orders": {
            "grain": ["order_id", "line_no"],
            "keys": [{"pk": ["order_id", "line_no"]}],
            "columns": {
                "order_id": {"type": "bigint", "not_null": True},
                "line_no": {"type": "int", "not_null": True},
                "status": {"type": "status", "generate": {"weights": {"new": 3}}},
                "qty": {"type": "int", "generate": {"min": 1, "max": 9}},
                "floor_only": {"type": "int", "generate": {"min": 50}},
                "price": {"type": "numeric(10,2)", "generate": {"max": 99.5, "null_rate": 0.25}},
                "ordered_at": {"type": "timestamp", "not_null": True},
                "shipped_at": {
                    "type": "timestamp",
                    "generate": {"after": "ordered_at", "null_rate": 0.3},
                },
                "due_on": {"type": "date", "generate": {"after": "ordered_at"}},
                "channel": {"type": "word", "generate": {"distinct": 4}},
                "paid": {"type": "boolean", "generate": {"true_rate": 0.7}},
                "score": {
                    "type": "float",
                    "generate": {"distribution": {"kind": "normal", "mean": 5, "stddev": 1}},
                },
            },
        }
    },
}

PLAIN = {
    "model2data": "0.2.0",
    "tables": {
        "t": {"columns": {"id": {"type": "bigint", "pk": True}, "n": {"type": "int"}}},
        "u": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "t_id": {"type": "bigint", "references": "t.id"},
            }
        },
    },
}


def _by(tests: list[HintTest]) -> dict[tuple[str, str | None, str], dict]:
    return {(t.table, t.column, t.test): t.arguments for t in tests}


def test_mapping_of_each_hint_to_a_test():
    found = _by(hint_tests_for(from_dict(HINTED)))
    assert found == {
        ("orders", "qty", "model2data_between"): {"min_value": 1, "max_value": 9},
        ("orders", "floor_only", "model2data_between"): {"min_value": 50},
        ("orders", "price", "model2data_between"): {"max_value": 99.5},
        ("orders", "price", "model2data_max_null_share"): {"max_share": 0.35},
        ("orders", "shipped_at", "model2data_not_before"): {"other": "ordered_at"},
        ("orders", "shipped_at", "model2data_max_null_share"): {"max_share": 0.4},
        ("orders", "due_on", "model2data_not_before"): {
            "other": "ordered_at",
            "granularity": "day",
        },
        ("orders", "channel", "model2data_max_distinct"): {"max_count": 4},
        ("orders", None, "model2data_unique_combination"): {"columns": ["order_id", "line_no"]},
    }


def test_statistical_shape_hints_make_no_test():
    shapes = {
        "model2data": "0.2.0",
        "enums": {"status": ["a", "b"]},
        "tables": {
            "t": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "s": {"type": "status", "generate": {"weights": {"a": 2}}},
                    "b": {"type": "boolean", "generate": {"true_rate": 0.9}},
                    "x": {
                        "type": "float",
                        "generate": {"distribution": {"kind": "lognormal", "median": 10}},
                    },
                    "d": {
                        "type": "timestamp",
                        "generate": {"business_hours": True, "growth": 0.5, "seasonality": 0.2},
                    },
                }
            },
            "c": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "t_id": {
                        "type": "bigint",
                        "references": "t.id",
                        "generate": {"skew": 0.5},
                    },
                }
            },
        },
    }
    assert hint_tests_for(from_dict(shapes)) == []


def test_accepts_model_engine_inputs_and_tables_alike():
    model = from_dict(HINTED)
    inputs = to_engine(model)
    assert hint_tests_for(model) == hint_tests_for(inputs) == hint_tests_for(inputs.tables)


def test_tolerance_moves_only_the_null_share_limit():
    default = _by(hint_tests_for(from_dict(HINTED)))
    tight = _by(hint_tests_for(from_dict(HINTED), tolerance=0.0))
    assert tight[("orders", "price", "model2data_max_null_share")] == {"max_share": 0.25}
    assert hint_tests_for(from_dict(HINTED), tolerance=0.9)[3].arguments == {"max_share": 1.0}
    for key, arguments in default.items():
        if key[2] != "model2data_max_null_share":
            assert tight[key] == arguments
    with pytest.raises(ValueError):
        hint_tests_for(from_dict(HINTED), tolerance=-1)


def test_to_dbt_nests_arguments_and_severity():
    test = HintTest("t", "c", "model2data_between", {"min_value": 1})
    assert test.to_dbt("warn") == {
        "model2data_between": {"arguments": {"min_value": 1}, "config": {"severity": "warn"}}
    }


def _yml(tmp_path: Path, table: str = "orders") -> dict:
    return yaml.safe_load((tmp_path / "models" / "staging" / f"stg_{table}.yml").read_text())


def test_generate_dbt_yml_writes_hint_tests_with_severity(tmp_path):
    tables = to_engine(from_dict(HINTED)).tables
    generate_dbt_yml(tmp_path, tables, [], hint_tests="error", test_tolerance=0.05)

    doc = _yml(tmp_path)
    model = doc["models"][0]
    columns = {c["name"]: c for c in model["columns"]}
    assert model["tests"] == [
        {
            "model2data_unique_combination": {
                "arguments": {"columns": ["order_id", "line_no"]},
                "config": {"severity": "error"},
            }
        }
    ]
    assert columns["qty"]["tests"] == [
        {
            "model2data_between": {
                "arguments": {"min_value": 1, "max_value": 9},
                "config": {"severity": "error"},
            }
        }
    ]
    assert columns["price"]["tests"][-1]["model2data_max_null_share"]["arguments"] == {
        "max_share": 0.3
    }
    # The enum's accepted_values is untouched by severity: no config.
    assert columns["status"]["tests"] == [
        {"accepted_values": {"arguments": {"values": ["new", "paid"]}}}
    ]
    assert (tmp_path / "macros" / "model2data_hint_tests.sql").exists()


def test_warn_severity_and_structural_tests_keep_their_behaviour(tmp_path):
    tables = to_engine(from_dict(HINTED)).tables
    generate_dbt_yml(tmp_path, tables, [], hint_tests="warn")
    columns = {c["name"]: c for c in _yml(tmp_path)["models"][0]["columns"]}
    assert columns["order_id"]["tests"] == ["not_null"]
    assert columns["qty"]["tests"][0]["model2data_between"]["config"] == {"severity": "warn"}


def test_off_writes_no_hint_tests_and_no_macros(tmp_path):
    tables = to_engine(from_dict(HINTED)).tables
    generate_dbt_yml(tmp_path, tables, [], hint_tests="off")
    assert "model2data_" not in (tmp_path / "models/staging/stg_orders.yml").read_text()
    assert not (tmp_path / "macros").exists()


def test_generate_dbt_yml_defaults_to_no_hint_tests(tmp_path):
    tables = to_engine(from_dict(HINTED)).tables
    generate_dbt_yml(tmp_path, tables, [])
    assert "model2data_" not in (tmp_path / "models/staging/stg_orders.yml").read_text()


def test_invalid_severity_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        generate_dbt_yml(tmp_path, {}, [], hint_tests="loud")


def _generate(tmp_path: Path, model: dict, *options: str, name: str = "p") -> Path:
    path = tmp_path / "m.model2data.yml"
    path.write_text(yaml.safe_dump(model))
    cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        result = runner.invoke(
            app, ["--file", str(path), "--rows", "30", "--seed", "3", "--name", name, *options]
        )
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    return tmp_path / f"dbt_{name}"


def test_a_model_without_hints_produces_exactly_todays_yaml(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    default = _generate(tmp_path / "a", PLAIN)
    off = _generate(tmp_path / "b", PLAIN, "--hint-tests", "off")
    for file in sorted(default.rglob("*")):
        if file.is_file() and file.suffix in (".yml", ".sql", ".csv"):
            assert file.read_text() == (off / file.relative_to(default)).read_text(), file
    assert not (default / "macros" / "model2data_hint_tests.sql").exists()
    assert yaml.safe_load((default / "models/staging/stg_t.yml").read_text()) == {
        "version": 2,
        "models": [
            {
                "name": "stg_t",
                "columns": [
                    {"name": "id", "tests": ["not_null", "unique"]},
                    {"name": "n"},
                ],
            }
        ],
    }


def test_cli_defaults_to_warn_and_takes_tolerance(tmp_path):
    project = _generate(tmp_path, HINTED, "--test-tolerance", "0.2")
    columns = {c["name"]: c for c in _yml(project)["models"][0]["columns"]}
    null_test = columns["price"]["tests"][-1]["model2data_max_null_share"]
    assert null_test["config"] == {"severity": "warn"}
    assert null_test["arguments"] == {"max_share": 0.45}


def test_cli_hint_tests_option_values(tmp_path):
    (tmp_path / "e").mkdir()
    (tmp_path / "o").mkdir()
    err = _generate(tmp_path / "e", HINTED, "--hint-tests", "error")
    assert "'severity': 'error'" not in (err / "models/staging/stg_orders.yml").read_text()
    assert "severity: error" in (err / "models/staging/stg_orders.yml").read_text()
    off = _generate(tmp_path / "o", HINTED, "--hint-tests", "off")
    assert "model2data_" not in (off / "models/staging/stg_orders.yml").read_text()

    result = runner.invoke(app, ["--file", str(tmp_path / "m.model2data.yml"), "--hint-tests", "x"])
    assert result.exit_code != 0


def test_dbt_names_are_used_for_tables(tmp_path):
    model = {
        "model2data": "0.2.0",
        "tables": {"Raw Orders": {"columns": {"id": {"type": "int", "generate": {"min": 1}}}}},
    }
    project = _generate(tmp_path, model)
    assert (project / "models/staging/stg_raw_orders.yml").exists()
    assert "model2data_between" in (project / "models/staging/stg_raw_orders.yml").read_text()


def test_write_hint_macros_ships_every_generic_test(tmp_path):
    sql = write_hint_macros(tmp_path).read_text()
    for name in (
        "model2data_between",
        "model2data_not_before",
        "model2data_max_null_share",
        "model2data_max_distinct",
        "model2data_unique_combination",
    ):
        assert f"{{% test {name}(" in sql


# --- dbt itself ------------------------------------------------------------


def _dbt(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DBT or "dbt", *args, "--profiles-dir", "."],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=240,
    )


needs_dbt = pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")


@needs_dbt
@pytest.mark.parametrize("seed", [1, 2])
def test_hinted_model_passes_its_own_hint_tests_in_duckdb(tmp_path, monkeypatch, seed):
    """Every hint test holds on the engine's own data, at `error` severity."""
    path = tmp_path / "m.model2data.yml"
    path.write_text(yaml.safe_dump(HINTED))
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["--file", str(path), "--rows", "300", "--seed", str(seed), "--name", "own"]
        + ["--hint-tests", "error"],
    )
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_own"

    nodes = _dbt(project, "ls", "--resource-type", "test").stdout
    for name in (
        "model2data_between",
        "model2data_not_before",
        "model2data_max_null_share",
        "model2data_max_distinct",
        "model2data_unique_combination",
    ):
        assert name in nodes
    build = _dbt(project, "build")
    assert build.returncode == 0, build.stdout + build.stderr
    assert "ERROR=0" in build.stdout and "WARN=0" in build.stdout


@needs_dbt
def test_macros_catch_violations_and_severity_decides_the_outcome(tmp_path, monkeypatch):
    """Corrupt the seeds so each hint is broken: `error` fails, `warn` only warns."""
    path = tmp_path / "m.model2data.yml"
    path.write_text(yaml.safe_dump(HINTED))
    monkeypatch.chdir(tmp_path)
    for severity in ("error", "warn"):
        name = f"bad_{severity}"
        result = runner.invoke(
            app,
            ["--file", str(path), "--rows", "50", "--seed", "1", "--name", name]
            + ["--hint-tests", severity],
        )
        assert result.exit_code == 0, result.output
        project = tmp_path / f"dbt_{name}"
        seed = project / "seeds" / "raw" / "orders.csv"
        lines = seed.read_text().splitlines()
        header = lines[0].split(",")
        rows = [line.split(",") for line in lines[1:]]
        col = {h: i for i, h in enumerate(header)}
        for row in rows[:5]:
            row[col["qty"]] = "1000"  # above max
            row[col["shipped_at"]] = "1999-01-01 00:00:00"  # before ordered_at
            row[col["due_on"]] = "1999-01-01"
        for row in rows[:30]:
            row[col["price"]] = ""  # nulls beyond null_rate + tolerance
        for i, row in enumerate(rows):
            row[col["channel"]] = f"channel{i}"  # more than 4 distinct
        rows[1][col["order_id"]], rows[1][col["line_no"]] = rows[0][col["order_id"]], "1"
        rows[0][col["line_no"]] = "1"
        seed.write_text("\n".join([",".join(header)] + [",".join(r) for r in rows]) + "\n")

        build = _dbt(project, "build")
        out = build.stdout
        if severity == "error":
            assert build.returncode != 0
            for test in (
                "model2data_between",
                "model2data_not_before",
                "model2data_max_null_share",
                "model2data_max_distinct",
            ):
                assert any(test in line and "FAIL" in line for line in out.splitlines()), (
                    test,
                    out,
                )
        else:
            # Only warnings among the hint tests; the seed's own pk test may fail.
            hint_lines = [line for line in out.splitlines() if "model2data_" in line]
            assert any("WARN" in line for line in hint_lines), out
            assert not any("FAIL" in line for line in hint_lines), out


@needs_dbt
@pytest.mark.parametrize(
    "example", sorted(p.name for p in EXAMPLES.glob("*.model2data.yml")), ids=lambda n: n[:-13]
)
def test_every_example_builds_with_hint_tests_at_error(tmp_path, monkeypatch, example):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["--file", str(EXAMPLES / example), "--rows", "60", "--seed", "7", "--name", "ex"]
        + ["--hint-tests", "error"],
    )
    assert result.exit_code == 0, result.output
    build = _dbt(tmp_path / "dbt_ex", "build")
    assert build.returncode == 0, build.stdout + build.stderr
    assert "ERROR=0" in build.stdout
