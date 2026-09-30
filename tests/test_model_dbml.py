"""DBML into a spec 0.2.0 model: `model2data.model.from_dbml`.

The conformance corpus in tests/fixtures/dbml_conformance/ is the studio's
(model2data-studio/scripts/conformance/), each file a DBML feature the
engine's old line parser misread. Every file either converts to the model the
spec's "From 0.1" table describes, or is refused with a message saying what
the DBML reader cannot read.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.model import (
    Enum,
    ForeignKey,
    Key,
    ModelError,
    Reference,
    dump,
    from_dbml,
    load,
    to_engine,
)
from model2data.parse.dbml import get_many_to_many_refs, parse_dbml

CORPUS = Path(__file__).resolve().parent / "fixtures" / "dbml_conformance"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _corpus(name: str):
    return from_dbml((CORPUS / f"{name}.dbml").read_text(encoding="utf-8"))


REFUSED = {
    "checks": "does not support check constraints",
    "records": "does not support Records blocks",
    "table_partials": "does not support TablePartial",
    "ref_directions": "references both users.id and legacy schema.old users.id",
}


def test_every_kind_of_default_literal():
    columns = _corpus("defaults").tables["settings"].columns
    assert {name: column.default for name, column in columns.items()} == {
        "id": None,
        "retries": 3,
        "offset_hours": -5,
        "ratio": 0.25,
        "budget": 1000.0,
        "whole": 2.0,
        "enabled": True,
        "label": "it's quoted",
        # A null default is no static default, not the text "NULL".
        "parent_id": None,
        "created_at": None,
    }
    assert columns["created_at"].extensions == {"x-default-expression": "now()"}


def test_arrays_of_parameterised_types():
    columns = _corpus("parameterised_types").tables["measurements"].columns
    assert columns["samples"].type == "numeric(10,2)[]"
    assert columns["labels"].type == "varchar(40)[]"
    assert columns["taken_at"].type == "timestamp(6)"


def test_names_outside_ascii_need_no_quotes():
    model = _corpus("unicode_names")
    assert list(model.tables["café"].columns) == ["id", "crème"]
    assert model.tables["commandes"].columns["café_id"].references.to == "café.id"


def test_a_ref_colour_is_read_and_dropped():
    # How dbdiagram draws a line is no part of the model.
    team = _corpus("ref_settings").tables["players"].columns["team_id"]
    assert team.references.to == "teams.id"


def test_the_corpus_is_all_here():
    assert len(list(CORPUS.glob("*.dbml"))) == 17


@pytest.mark.parametrize("name, message", sorted(REFUSED.items()))
def test_what_cannot_be_read_is_refused_saying_why(name, message):
    with pytest.raises(ModelError) as raised:
        _corpus(name)
    assert len(raised.value.issues) == 1
    assert message in str(raised.value.issues[0])


@pytest.mark.parametrize(
    "name",
    sorted(path.stem for path in CORPUS.glob("*.dbml") if path.stem not in REFUSED),
)
def test_every_readable_file_round_trips_through_yaml(name):
    model = _corpus(name)
    assert load(dump(model)) == model


def test_aliases_colours_groups_multiline_notes_and_the_project():
    model = _corpus("blocks")
    assert model.name == "shop"
    assert model.description == "A project note with { braces } and a } on its own."
    customers = model.tables["customers"]
    assert customers.color == "#3498DB"
    assert customers.description == "Customers.\nSpans lines."
    assert customers.columns["name"].description == "plain prose note"
    # `C` is only an alias: the ref names the table.
    assert model.tables["orders"].columns["customer_id"].references == Reference("customers.id")
    assert model.groups["sales"].tables == ["customers", "orders"]


def test_enums_types_defaults_and_hints():
    model = _corpus("enums_and_types")
    assert model.enums["status"] == Enum(
        ["active", "on hold", "closed"], {"active": "live", "on hold": None, "closed": None}
    )
    assert model.enums["billing.plan"] == Enum(["free", "pro"])
    columns = model.tables["accounts"].columns
    assert columns["plan"].type == "billing.plan"
    assert columns["status"].default == "active"
    assert columns["status"].not_null
    assert columns["id"].increment
    assert columns["price"].type == "decimal(10, 2)"
    assert columns["price"].generate == {"min": 1, "max": 99}
    assert columns["tags"].type == "text[]"
    assert columns["created_at"].default is None
    assert columns["created_at"].extensions == {"x-default-expression": "now()"}
    engine = to_engine(model).tables["accounts"]
    plan = next(column for column in engine.columns if column.name == "plan")
    assert plan.enum_values == ["free", "pro"]


def test_indexes_become_keys_and_several_pk_columns_one_key():
    model = _corpus("indexes")
    memberships = model.tables["memberships"]
    assert memberships.keys == [
        Key("pk", ["user_id", "team_id"]),
        Key("unique", ["team_id", "role"]),
    ]
    assert memberships.columns["role"].unique
    two = to_engine(model).tables["two_pk_columns"]
    assert two.composite_keys == [{"columns": ["a", "b"], "type": "pk"}]


def test_inline_refs_and_a_one_to_one():
    model = _corpus("inline_refs")
    posts = model.tables["posts"].columns
    assert posts["author_id"].references == Reference("users.id")
    assert posts["editor id"].references == Reference("users.id")
    assert model.tables["profiles"].columns["user_id"].references == Reference(
        "users.id", one_to_one=True
    )


def test_ref_blocks_composite_refs_and_a_ref_onto_columns_that_are_no_key():
    model = _corpus("ref_blocks")
    assert model.tables["b"].foreign_keys == [ForeignKey(["ax", "ay"], "a", ["x", "y"])]
    assert model.tables["c"].columns["id"].references == Reference("b.id")
    # a.(x, y) is no key of a: converted as written, with a warning.
    assert [issue.path for issue in model.warnings] == ["tables.b.foreign_keys.0.to_columns"]


def test_schemas_key_tables_and_enums_schema_dot_name():
    model = _corpus("schema_refs")
    assert list(model.tables) == ["sales.accounts", "sales.invoices", "sales.tags"]
    assert model.enums["sales.region"].members == ["emea", "apac"]
    assert model.tables["sales.invoices"].foreign_keys == [
        ForeignKey(["tenant_id", "account_no"], "sales.accounts", ["tenant_id", "account_no"])
    ]
    assert [r.many_to_many for r in model.relationships] == [["sales.invoices.id", "sales.tags.id"]]
    assert model.groups["billing"].tables == ["sales.accounts", "sales.invoices"]

    model = _corpus("schemas")
    assert list(model.tables) == ["raw.users", "analytics.users", "events"]
    assert model.tables["analytics.users"].columns["raw_id"].references == Reference("raw.users.id")


def test_settings_and_notes():
    columns = _corpus("settings_and_notes").tables["t"].columns
    assert columns["id"].type == "INT" and columns["id"].pk and columns["id"].increment
    assert columns["c"].description == "has, a comma [and brackets]"
    assert columns["e"].default == 5
    assert columns["e"].generate == {"null_rate": 0.2}
    assert columns["f"].default is False
    assert columns["g"].generate == {"distribution": {"kind": "normal", "mean": 10, "stddev": 2}}


def test_json_notes_are_hints_measure_and_role():
    model = from_dbml(
        """
Table sales {
  id int [pk]
  amount numeric [note: '{"measure": true, "distribution": "lognormal", "min": 1}']
  kind text [note: '{"measure": false}']
  Note: '{"role": "fact"}'
}
"""
    )
    sales = model.tables["sales"]
    assert sales.role == "fact"
    assert sales.description is None
    assert sales.columns["amount"].measure is True
    assert sales.columns["amount"].generate == {"distribution": "lognormal", "min": 1}
    assert sales.columns["kind"].measure is False
    assert sales.columns["kind"].generate == {}
    note = to_engine(model).tables["sales"].columns[1].note
    assert note == {"distribution": "lognormal", "min": 1, "measure": True}


def test_a_table_note_holding_other_json_is_refused():
    with pytest.raises(ModelError, match="the only hint a table note can hold is role"):
        from_dbml("Table t {\n  id int\n  Note: '{\"rows\": 5}'\n}\n")


def test_a_bad_hint_in_a_note_is_reported_at_its_document_path():
    with pytest.raises(ModelError) as raised:
        from_dbml("Table t {\n  id int [pk]\n  label varchar [note: '{\"nul_rate\": 0.1}']\n}\n")
    assert str(raised.value.issues[0]).startswith("tables.t.columns.label.generate.nul_rate:")


def test_default_null_is_no_default():
    model = from_dbml("Table t {\n  id int [pk]\n  parent_id int [default: null]\n}\n")
    assert model.tables["t"].columns["parent_id"].default is None


def test_a_name_with_a_dot_is_an_error_naming_the_table():
    with pytest.raises(ModelError) as raised:
        from_dbml('Table "a.b" {\n  id int\n}\nTable c {\n  "x.y" int\n}\n')
    messages = [str(issue) for issue in raised.value.issues]
    assert messages[0].startswith("tables.a.b: the table 'a.b' has a '.' in its name")
    assert messages[1].startswith("tables.c.columns.x.y: the column 'x.y' has a '.'")


@pytest.mark.parametrize(
    "dbml, child, parent",
    [
        # Exactly one side is the primary key: the other side holds the key.
        ("Ref: users.id - profiles.user_id", ("profiles", "user_id"), "users.id"),
        ("Ref: profiles.user_id - users.id", ("profiles", "user_id"), "users.id"),
        # Neither is: the written order stands, left is the child.
        ("Ref: profiles.alt - users.alt", ("profiles", "alt"), "users.alt"),
    ],
)
def test_one_to_one_direction(dbml, child, parent):
    model = from_dbml(
        "Table users {\n  id int [pk]\n  alt int [unique]\n}\n"
        "Table profiles {\n  id int [pk]\n  user_id int [unique]\n  alt int [unique]\n}\n" + dbml
    )
    table, column = child
    assert model.tables[table].columns[column].references == Reference(parent, one_to_one=True)


def test_an_inline_one_to_one_between_two_keys_sits_on_the_declaring_column():
    model = from_dbml("Table nodes {\n  id int [pk]\n  next_id int [unique, ref: - nodes.id]\n}\n")
    assert model.tables["nodes"].columns["next_id"].references == Reference(
        "nodes.id", one_to_one=True
    )


def test_a_one_to_one_child_takes_each_parent_at_most_once():
    tables, refs = to_engine(_corpus("inline_refs")).tables, to_engine(_corpus("inline_refs")).refs
    data = generate_data_from_dbml(tables, refs, base_rows=30, seed=1, as_of=datetime(2026, 1, 1))
    user_ids = data["profiles"]["user_id"].dropna()
    assert user_ids.is_unique
    assert user_ids.isin(data["users"]["id"]).all()


def test_parse_dbml_is_from_dbml_then_to_engine(tmp_path):
    path = CORPUS / "schema_refs.dbml"
    tables, refs = parse_dbml(path)
    inputs = to_engine(from_dbml(path.read_text(encoding="utf-8")))
    assert tables == inputs.tables
    assert refs == inputs.refs
    assert get_many_to_many_refs() == inputs.many_to_many


def test_a_ref_onto_a_column_that_is_no_key_draws_from_the_parents_values():
    """hackernews: `_dlt_loads.schema_version_hash` references
    `_dlt_version.version_hash`, which is not unique. The model conforms, with
    a warning, and every hash a load names is a version's hash."""
    model = load(EXAMPLES / "hackernews.dbml")
    assert [issue.path for issue in model.warnings] == [
        "tables._dlt_loads.columns.schema_version_hash.references"
    ]
    inputs = to_engine(model)
    for seed in (1, 7, 42):
        data = generate_data_from_dbml(
            inputs.tables, inputs.refs, base_rows=40, seed=seed, as_of=datetime(2026, 1, 1)
        )
        hashes = data["_dlt_loads"]["schema_version_hash"].dropna()
        assert len(hashes)
        assert hashes.isin(data["_dlt_version"]["version_hash"]).all(), seed
