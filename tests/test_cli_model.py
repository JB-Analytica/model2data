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
    assert "  - tables.orders.columns.customer_id.nul: unknown key" in result.output
    assert "  - run.rows: must be 1 or more (got 0)" in result.output


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
