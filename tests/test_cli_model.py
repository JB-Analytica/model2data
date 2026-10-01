"""The CLI on spec 0.2.0 models: `--file x.model2data.yml`, `validate`, `convert`."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.model import dump, from_dbml, load

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

MODEL = """\
model2data: 0.2.0
name: shop

enums:
  status: [open, closed]

tables:
  customers:
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, unique: true, not_null: true}

  orders:
    columns:
      id: {type: bigint, pk: true}
      customer_id: {type: bigint, not_null: true, references: customers.id}
      status: status
      placed_at: {type: timestamp, not_null: true}

run:
  rows: 12
  rows_per_table: {orders: 30}
  seed: 5
  as_of: 2026-01-01
"""


@pytest.fixture
def in_tmp(tmp_path):
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        yield tmp_path
    finally:
        os.chdir(original)


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def _seeds(project: Path) -> dict[str, str]:
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted((project / "seeds" / "raw").glob("*.csv"))
    }


def test_generate_reads_a_model_and_its_run_settings(in_tmp):
    model = _write(in_tmp, "shop.model2data.yml", MODEL)
    result = runner.invoke(app, ["--file", str(model)])
    assert result.exit_code == 0, result.output
    assert "Using deterministic seed: 5" in result.output
    assert "Anchoring generated dates on: 2026-01-01" in result.output
    # The model's name names the project.
    project = in_tmp / "dbt_shop"
    assert len(pd.read_csv(project / "seeds" / "raw" / "customers.csv")) == 12
    assert len(pd.read_csv(project / "seeds" / "raw" / "orders.csv")) == 30
    assert (project / "shop.model2data.yml").exists()


def test_cli_options_override_the_run_settings(in_tmp):
    model = _write(in_tmp, "shop.model2data.yml", MODEL)
    result = runner.invoke(
        app, ["--file", str(model), "--rows", "20", "--rows-for", "orders=15", "--name", "x"]
    )
    assert result.exit_code == 0, result.output
    project = in_tmp / "dbt_x"
    assert len(pd.read_csv(project / "seeds" / "raw" / "customers.csv")) == 20
    assert len(pd.read_csv(project / "seeds" / "raw" / "orders.csv")) == 15


def test_the_run_seed_reproduces_and_a_seed_option_overrides_it(in_tmp):
    model = _write(in_tmp, "shop.model2data.yml", MODEL)
    first = runner.invoke(app, ["--file", str(model), "--name", "a"])
    second = runner.invoke(app, ["--file", str(model), "--name", "b"])
    third = runner.invoke(app, ["--file", str(model), "--name", "c", "--seed", "6"])
    assert first.exit_code == second.exit_code == third.exit_code == 0
    assert _seeds(in_tmp / "dbt_a") == _seeds(in_tmp / "dbt_b")
    assert _seeds(in_tmp / "dbt_a") != _seeds(in_tmp / "dbt_c")


def test_generate_reads_the_same_model_as_json(in_tmp):
    yaml_model = _write(in_tmp, "shop.model2data.yml", MODEL)
    document = load(yaml_model)
    from model2data.model import to_dict

    json_model = _write(in_tmp, "shop.json", json.dumps(to_dict(document)))
    assert runner.invoke(app, ["--file", str(yaml_model), "--name", "y"]).exit_code == 0
    assert runner.invoke(app, ["--file", str(json_model), "--name", "j"]).exit_code == 0
    assert _seeds(in_tmp / "dbt_y") == _seeds(in_tmp / "dbt_j")


def test_generate_refuses_an_invalid_model_listing_every_issue(in_tmp):
    model = _write(
        in_tmp,
        "bad.model2data.yml",
        MODEL.replace(
            "status: status", "status: {type: status, generate: {weights: {gone: 1}}}"
        ).replace("placed_at: {type: timestamp, not_null: true}", "placed_at: {type: timestmp}"),
    )
    result = runner.invoke(app, ["--file", str(model)])
    assert result.exit_code == 1
    assert "❌ bad.model2data.yml: 1 error" in result.output
    assert "tables.orders.columns.status.generate.weights" in result.output
    assert not (in_tmp / "dbt_shop").exists()


def test_a_table_key_becomes_a_dbt_safe_name(in_tmp):
    model = _write(
        in_tmp,
        "keys.model2data.yml",
        "model2data: 0.2.0\ntables:\n  raw.Users:\n    columns:\n      id: {type: int, pk: true}\n"
        "  user accounts:\n    columns:\n      id: {type: int, pk: true}\n"
        "      user_id: {type: int, references: raw.Users.id}\n",
    )
    result = runner.invoke(app, ["--file", str(model), "--rows", "10"])
    assert result.exit_code == 0, result.output
    project = in_tmp / "dbt_keys"
    assert sorted(path.name for path in (project / "seeds" / "raw").glob("*.csv")) == [
        "raw_users.csv",
        "user_accounts.csv",
    ]
    assert "ref('raw_users')" in (project / "models" / "staging" / "stg_raw_users.sql").read_text()


def test_two_table_keys_that_would_be_one_dbt_name_are_refused(in_tmp):
    model = _write(
        in_tmp,
        "clash.model2data.yml",
        "model2data: 0.2.0\ntables:\n  raw.users:\n    columns:\n      id: int\n"
        "  raw_users:\n    columns:\n      id: int\n",
    )
    result = runner.invoke(app, ["--file", str(model), "--rows", "10"])
    assert result.exit_code == 1
    assert "'raw.users' and 'raw_users' would both be the dbt seed 'raw_users'" in result.output


def test_generate_prints_warnings_and_goes_on(in_tmp):
    result = runner.invoke(app, ["--file", str(EXAMPLES / "hackernews.dbml"), "--rows", "10"])
    assert result.exit_code == 0, result.output
    assert "⚠️  hackernews.dbml: 1 warning" in result.output
    assert "tables._dlt_loads.columns.schema_version_hash.references" in result.output


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def test_validate_a_conforming_model(tmp_path):
    result = runner.invoke(app, ["validate", str(_write(tmp_path, "m.model2data.yml", MODEL))])
    assert result.exit_code == 0
    assert result.output == "✅ m.model2data.yml conforms to spec 0.2.0.\n"


def test_validate_prints_every_issue_with_its_path_and_exits_1(tmp_path):
    model = _write(
        tmp_path,
        "m.model2data.yml",
        MODEL.replace("rows: 12", "rows: 0").replace("status: status", "status: {type: statuz}"),
    )
    model.write_text(
        model.read_text().replace(
            "customer_id: {type: bigint", "customer_id: {type: bigint, nul: 1"
        )
    )
    result = runner.invoke(app, ["validate", str(model)])
    assert result.exit_code == 1
    assert "❌ m.model2data.yml: 2 errors" in result.output
    assert "  - tables.orders.columns.customer_id.nul (line 16): unknown key" in result.output
    assert "  - run.rows (line 21): must be 1 or more (got 0)" in result.output


def test_validate_exits_0_on_warnings_alone(tmp_path):
    result = runner.invoke(app, ["validate", str(EXAMPLES / "hackernews.dbml")])
    assert result.exit_code == 0
    assert "⚠️  hackernews.dbml: 1 warning" in result.output
    assert "conforms to spec 0.2.0" in result.output


def test_validate_reports_a_yaml_profile_error(tmp_path):
    model = _write(tmp_path, "m.model2data.yml", MODEL + "name: again\n")
    result = runner.invoke(app, ["validate", str(model)])
    assert result.exit_code == 1
    assert "name (line 25): duplicate key 'name', first written on line 2" in result.output


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------
def test_convert_prints_the_document(tmp_path):
    result = runner.invoke(app, ["convert", str(EXAMPLES / "ecommerce.dbml")])
    assert result.exit_code == 0
    expected = dump(from_dbml((EXAMPLES / "ecommerce.dbml").read_text(encoding="utf-8")))
    assert result.output == expected


def test_convert_writes_a_file_and_will_not_overwrite_without_force(tmp_path):
    out = tmp_path / "ecommerce.model2data.yml"
    result = runner.invoke(app, ["convert", str(EXAMPLES / "ecommerce.dbml"), "-o", str(out)])
    assert result.exit_code == 0
    assert load(out) == load(EXAMPLES / "ecommerce.dbml")
    again = runner.invoke(app, ["convert", str(EXAMPLES / "ecommerce.dbml"), "-o", str(out)])
    assert again.exit_code == 1 and "already exists" in again.output
    forced = runner.invoke(
        app, ["convert", str(EXAMPLES / "ecommerce.dbml"), "-o", str(out), "--force"]
    )
    assert forced.exit_code == 0


def test_convert_refuses_dbml_it_cannot_read(tmp_path):
    # Not a DBML gap -- a 0.2.0 column references one column, not two.
    bad = _write(
        tmp_path,
        "bad.dbml",
        "Table a {\n  id int [pk]\n}\nTable b {\n  id int [pk]\n}\n"
        "Table t {\n  x int [ref: > a.id, ref: > b.id]\n}\n",
    )
    result = runner.invoke(app, ["convert", str(bad)])
    assert result.exit_code == 1
    assert "references both a.id and b.id" in result.output


def test_help_lists_the_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("generate", "validate", "convert"):
        assert command in result.output


# ---------------------------------------------------------------------------
# validate: several files, --glob, --format github, and the CI integration files
# ---------------------------------------------------------------------------
def _broken(tmp_path, name="bad.model2data.yml"):
    return _write(tmp_path, name, MODEL.replace("rows: 12", "rows: 0"))


def test_validate_several_files_all_valid(tmp_path):
    a = _write(tmp_path, "a.model2data.yml", MODEL)
    b = _write(tmp_path, "b.model2data.yml", MODEL)
    result = runner.invoke(app, ["validate", str(a), str(b)])
    assert result.exit_code == 0, result.output
    assert f"✅ {a} conforms to spec 0.2.0." in result.output
    assert "✅ 2 model files conform." in result.output


def test_validate_several_files_one_invalid_exits_1_with_path_prefixed_issues(tmp_path):
    good = _write(tmp_path, "good.model2data.yml", MODEL)
    bad = _broken(tmp_path)
    result = runner.invoke(app, ["validate", str(good), str(bad)])
    assert result.exit_code == 1
    assert f"❌ {bad}: 1 error" in result.output
    assert "  - run.rows (line 21): must be 1 or more (got 0)" in result.output
    assert "❌ 1 of 2 model files does not conform." in result.output


def test_validate_glob_matches_nested_files(tmp_path, monkeypatch):
    (tmp_path / "deep" / "er").mkdir(parents=True)
    _write(tmp_path, "top.model2data.yml", MODEL)
    _write(tmp_path / "deep" / "er", "nested.model2data.yml", MODEL)
    (tmp_path / "node_modules").mkdir()
    _write(tmp_path / "node_modules", "skipped.model2data.yml", MODEL)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "--glob", "**/*.model2data.yml"])
    assert result.exit_code == 0, result.output
    assert "top.model2data.yml" in result.output
    assert "nested.model2data.yml" in result.output
    assert "skipped" not in result.output
    assert "2 model files conform" in result.output


def test_validate_glob_with_no_match_is_a_note_unless_files_are_required(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "--glob", "**/*.model2data.yml"])
    assert result.exit_code == 0
    assert "no model files matched" in result.output
    required = runner.invoke(app, ["validate", "--glob", "**/*.model2data.yml", "--require-files"])
    assert required.exit_code == 1
    assert "no model files matched" in required.output


def test_validate_format_github_prints_annotations(tmp_path, monkeypatch):
    _broken(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "bad.model2data.yml", "--format", "github"])
    assert result.exit_code == 1
    assert (
        "::error file=bad.model2data.yml,line=21::run.rows: must be 1 or more (got 0)"
        in result.output
    )
    yaml_error = _write(tmp_path, "dup.model2data.yml", MODEL + "name: again\n")
    result = runner.invoke(app, ["validate", yaml_error.name, "--format", "github"])
    assert "::error file=dup.model2data.yml,line=25::" in result.output


def test_validate_format_github_prints_warnings_and_exits_0():
    result = runner.invoke(
        app, ["validate", str(EXAMPLES / "hackernews.dbml"), "--format", "github"]
    )
    assert result.exit_code == 0
    assert "::warning file=" in result.output


def test_validate_rejects_an_unknown_format(tmp_path):
    model = _write(tmp_path, "m.model2data.yml", MODEL)
    assert runner.invoke(app, ["validate", str(model), "--format", "xml"]).exit_code != 0


def test_ci_integration_files_run_the_validate_command():
    import yaml

    root = Path(__file__).resolve().parent.parent
    action = yaml.safe_load((root / "action.yml").read_text())
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) == {"files", "version", "python-version"}
    steps = " ".join(str(step.get("run", "")) for step in action["runs"]["steps"])
    assert "model2data validate --glob" in steps
    assert "--format github" in steps
    hooks = yaml.safe_load((root / ".pre-commit-hooks.yaml").read_text())
    assert hooks[0]["id"] == "model2data-validate"
    assert hooks[0]["entry"] == "model2data validate"
    assert hooks[0]["language"] == "python"
    assert hooks[0]["pass_filenames"] is True


def test_validate_format_github_puts_the_line_on_a_value_issue(tmp_path, monkeypatch):
    _write(
        tmp_path,
        "m.model2data.yml",
        MODEL.replace("pk: true}\n      email", "pk: maybe}\n      email"),
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["validate", "m.model2data.yml", "--format", "github"])
    assert result.exit_code == 1
    assert (
        "::error file=m.model2data.yml,line=10::tables.customers.columns.id.pk: "
        'must be true or false, not the string "maybe"'
    ) in result.output


def test_validate_summary_agrees_with_its_count(tmp_path):
    good = _write(tmp_path, "good.model2data.yml", MODEL)
    bad = _broken(tmp_path)
    one_good = runner.invoke(app, ["validate", str(good), "--format", "github"])
    assert "✅ 1 model file conforms." in one_good.output
    one_bad = runner.invoke(app, ["validate", str(bad), "--format", "github"])
    assert "❌ 1 of 1 model file does not conform." in one_bad.output
    mixed = runner.invoke(app, ["validate", str(good), str(bad)])
    assert "❌ 1 of 2 model files does not conform." in mixed.output
    both = runner.invoke(app, ["validate", str(bad), str(_broken(tmp_path, "b2.model2data.yml"))])
    assert "❌ 2 of 2 model files do not conform." in both.output


def test_action_uses_setup_python_v6():
    import yaml

    root = Path(__file__).resolve().parent.parent
    action = yaml.safe_load((root / "action.yml").read_text())
    uses = [step.get("uses") for step in action["runs"]["steps"]]
    assert "actions/setup-python@v6" in uses
