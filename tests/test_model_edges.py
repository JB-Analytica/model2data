"""Edges of reading and writing a model: YAML scalars, DBML corners, files, the CLI."""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.naming import dbt_identifier as _dbt_identifier
from model2data.model import ModelError, dump, from_dbml, from_dict, load, to_dict
from model2data.model._yaml import parse_yaml, scalar, string_scalar
from model2data.model.engine import run_as_of
from model2data.model.types import Run
from model2data.parse.dbml import get_parse_warnings

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
DAILY = EXAMPLES / "ecommerce_daily.model2data.yml"


# ---------------------------------------------------------------------------
# YAML scalars under the profile
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "value"),
    [("0o17", 15), ("0x1f", 31), (".inf", math.inf), ("-.Inf", -math.inf), ("+.inf", math.inf)],
)
def test_core_schema_numbers_are_read(text, value):
    assert parse_yaml(f"k: {text}\n") == {"k": value}


def test_nan_is_read():
    assert math.isnan(parse_yaml("k: .nan\n")["k"])


def test_an_unhashable_key_is_an_issue_not_a_crash():
    with pytest.raises(ModelError, match="unhashable key"):
        parse_yaml("? [a]\n: 1\n")


@pytest.mark.parametrize(
    "text",
    [
        "back\\slash",
        '"starts with a quote\\"',
        "'single and \\\\ backslash",
        'a "quote"',
        "two\nlines",
        "tab\there",
        "bell\x07",
        "zero\u200bwidth",
        "tag\U000e0001",
    ],
)
def test_a_string_needing_escapes_reads_back_as_itself(text):
    assert parse_yaml(f"k: {string_scalar(text)}\n") == {"k": text}


def test_special_floats_are_written_and_read_back():
    assert scalar(math.inf) == ".inf" and scalar(-math.inf) == "-.inf"
    assert math.isnan(parse_yaml(f"k: {scalar(math.nan)}\n")["k"])


def test_only_json_scalars_are_written():
    with pytest.raises(TypeError, match="not a JSON scalar"):
        scalar(object())


# ---------------------------------------------------------------------------
# dump: extension values of every shape
# ---------------------------------------------------------------------------
def test_extension_values_of_every_shape_round_trip():
    data = to_dict(load(DAILY))
    data["x-studio"] = {
        "notes": [
            {"text": "first line\nsecond line"},
            [["nested", "list"], "two\nlines"],
            "  leading space\nand a newline",
            "trailing space \nhere",
            "a" * 120,
        ],
    }
    model = from_dict(data)
    assert load(dump(model)) == model


# ---------------------------------------------------------------------------
# DBML corners
# ---------------------------------------------------------------------------
def test_dbml_table_groups_carry_colour_and_note():
    model = from_dbml(
        "Table a {\n  id int [pk]\n}\nTableGroup g [color: #aabbcc] {\n  a\n  Note: 'the core'\n}\n"
    )
    group = to_dict(model)["groups"]["g"]
    assert group == {"tables": ["a"], "color": "#aabbcc", "description": "the core"}


def test_a_dbml_expression_index_is_not_a_key():
    model = from_dbml(
        "Table a {\n  id int [pk]\n  name text\n  indexes {\n    (`lower(name)`) [unique]\n  }\n}\n"
    )
    assert to_dict(model)["tables"]["a"].get("keys") is None


def test_a_composite_one_to_one_dbml_ref_is_a_one_to_one_foreign_key():
    model = from_dbml(
        "Table a {\n  x int\n  y int\n  indexes {\n    (x, y) [pk]\n  }\n}\n"
        "Table b {\n  x int\n  y int\n}\n"
        "Ref: b.(x, y) - a.(x, y)\n"
    )
    foreign_keys = [
        fk for table in to_dict(model)["tables"].values() for fk in table.get("foreign_keys", [])
    ]
    assert [fk.get("one_to_one") for fk in foreign_keys] == [True]


@pytest.mark.parametrize(
    "dbml",
    [
        'Enum "a.b" {\n  x\n}\nTable t {\n  id int [pk]\n}\n',
        'Table t {\n  id int [pk]\n}\nTableGroup "a.b" {\n  t\n}\n',
        'Table "a.b" {\n  id int [pk]\n}\nTable t {\n  id int [pk]\n  a int [ref: > "a.b".id]\n}\n',
    ],
)
def test_a_dbml_name_the_spec_cannot_hold_is_reported(dbml):
    with pytest.raises(ModelError, match="has a '.' in its name"):
        from_dbml(dbml)


def test_the_line_parser_warnings_are_gone():
    assert get_parse_warnings() == []


# ---------------------------------------------------------------------------
# Files and text
# ---------------------------------------------------------------------------
def test_text_that_is_not_a_path_is_read_as_text():
    assert load("model2data: 0.2.0\ntables:\n  t:\n    columns:\n      id: integer\n")


def test_a_name_too_long_to_be_a_file_is_read_as_text():
    with pytest.raises(ModelError):
        load("x" * 300 + ".model2data.yml")


def test_a_file_that_is_not_utf8_is_an_issue(tmp_path):
    path = tmp_path / "m.model2data.yml"
    path.write_bytes(b"model2data: 0.2.0\nname: caf\xe9\n")
    with pytest.raises(ModelError, match="is not UTF-8"):
        load(path)


def test_json_that_does_not_parse_is_an_issue(tmp_path):
    path = tmp_path / "m.model2data.json"
    path.write_text('{"model2data": "0.2.0",', encoding="utf-8")
    with pytest.raises(ModelError, match="not valid JSON"):
        load(path)


def test_primary_key_is_read_from_keys_or_columns():
    model = load(DAILY)
    assert model.tables["orders"].primary_key() == ["id"]
    data = {
        "model2data": "0.2.0",
        "tables": {
            "t": {
                "columns": {"a": "integer", "b": "integer"},
                "keys": [{"unique": ["a", "b"]}, {"pk": ["a", "b"]}],
            }
        },
    }
    assert from_dict(data).tables["t"].primary_key() == ["a", "b"]
    data["tables"]["t"]["keys"] = [{"unique": ["a", "b"]}]
    assert from_dict(data).tables["t"].primary_key() == []


def test_run_as_of():
    assert run_as_of(Run()) is None
    assert str(run_as_of(Run(as_of="2026-01-31"))) == "2026-01-31"


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("key", "name"), [("___", "table"), ("2024 sales", "t_2024_sales")])
def test_dbt_identifiers(key, name):
    assert _dbt_identifier(key) == name


def _in(tmp_path: Path, *args: str):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return runner.invoke(app, list(args))
    finally:
        os.chdir(cwd)


def test_an_unknown_hint_test_severity_is_refused(tmp_path):
    result = _in(tmp_path, "--file", str(DAILY), "--hint-tests", "loud")
    assert result.exit_code == 1
    assert "Unsupported --hint-tests 'loud'" in result.output


def test_days_on_a_model_without_incremental_say_nothing_moves(tmp_path):
    shop = EXAMPLES / "ecommerce.model2data.yml"
    result = _in(tmp_path, "--file", str(shop), "--rows", "20", "--days", "1")
    assert result.exit_code == 0, result.output
    assert "nothing changes after the first day" in result.output


def test_convert_to_a_file_prints_the_warnings(tmp_path):
    source = tmp_path / "w.dbml"
    source.write_text(
        "Table a {\n  id int [pk]\n  code int\n}\nTable b {\n  id int [pk]\n"
        "  a_code int [ref: > a.code]\n}\n",
        encoding="utf-8",
    )
    out = tmp_path / "w.model2data.yml"
    result = runner.invoke(app, ["convert", str(source), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "a.code" in result.output
