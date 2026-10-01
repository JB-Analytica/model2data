"""model2data.model: reading, checking and writing a spec 0.2.0 document."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from model2data.model import (
    Column,
    Enum,
    ForeignKey,
    Group,
    Key,
    Model,
    ModelError,
    Reference,
    Relationship,
    Run,
    Shape,
    Table,
    dump,
    from_dict,
    load,
    to_dict,
    to_engine,
    validate,
)

SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec"
REFERENCE = SPEC / "examples" / "coffee_webshop.model2data.yml"

HEADER = "model2data: 0.2.0\n"


def _issues(source, **kwargs) -> list[str]:
    """Every issue for a document, as `path: message` strings."""
    if isinstance(source, dict):
        try:
            from_dict(source)
        except ModelError as error:
            return [str(issue) for issue in error.issues]
        return []
    return [str(issue) for issue in validate(source, **kwargs)]


def _doc(tables: dict, **top) -> dict:
    return {"model2data": "0.2.0", "tables": tables, **top}


def _one_issue(document: dict) -> str:
    issues = _issues(document)
    assert len(issues) == 1, issues
    return issues[0]


# ---------------------------------------------------------------------------
# The YAML profile
# ---------------------------------------------------------------------------
def test_yaml_1_1_booleans_are_strings():
    model = load(
        HEADER
        + "enums:\n  answer: [yes, no, on, off, y, n, NO]\n"
        + "tables:\n  t:\n    columns:\n      a: answer\n"
    )
    assert model.enums["answer"].members == ["yes", "no", "on", "off", "y", "n", "NO"]


def test_only_true_and_false_are_booleans():
    model = load(HEADER + "tables:\n  t:\n    columns:\n      a: {type: int, pk: true}\n")
    assert model.tables["t"].columns["a"].pk is True


def test_a_date_is_a_string():
    model = load(
        HEADER + "tables:\n  t:\n    columns:\n      a: int\nrun:\n  seed: 1\n  as_of: 2026-01-01\n"
    )
    assert model.run is not None
    assert model.run.as_of == "2026-01-01"


def test_a_leading_zero_integer_is_decimal_not_octal():
    model = load(HEADER + "tables:\n  t:\n    columns:\n      a: int\nrun:\n  seed: 010\n")
    assert model.run is not None and model.run.seed == 10


def test_the_version_as_a_yaml_number_is_accepted():
    model = load("model2data: 0.2\ntables:\n  t:\n    columns:\n      a: int\n")
    assert model.version == 0.2


def test_duplicate_keys_are_an_error_with_their_path():
    issues = _issues(HEADER + "tables:\n  t:\n    columns:\n      a: int\n      a: text\n")
    assert len(issues) == 1
    assert issues[0] == (
        "tables.t.columns.a (line 6): duplicate key 'a', first written on line 5: a key may "
        "appear only once in a mapping"
    )


def test_anchors_and_aliases_are_an_error():
    issues = _issues(HEADER + "tables:\n  t:\n    columns:\n      a: &int int\n      b: *int\n")
    assert issues == ["line 5: anchors (&int) are not allowed: write each value out in full"]
    issues = _issues(HEADER + "tables:\n  t:\n    columns:\n      a: *int\n")
    assert "aliases (*int) are not allowed" in issues[0]


def test_merge_keys_are_an_error():
    issues = _issues(HEADER + "tables:\n  t:\n    columns:\n      a:\n        <<: {type: int}\n")
    assert issues == [
        "tables.t.columns.a (line 6): merge keys (<<) are not allowed: write the keys out"
    ]


@pytest.mark.parametrize("tag", ["!!str", "!custom", "!"])
def test_tags_are_an_error(tag):
    issues = _issues(HEADER + f"tables:\n  t:\n    columns:\n      a: {tag} int\n")
    assert len(issues) == 1 and "tags" in issues[0] and "are not allowed" in issues[0]


def test_more_than_one_document_is_an_error():
    issues = _issues(HEADER + "tables:\n  t:\n    columns:\n      a: int\n---\nb: 1\n")
    assert issues == ["a model is one YAML document; this file holds more than one"]


def test_an_empty_document_is_an_error():
    assert _issues("# nothing\n") == ["the document is empty"]


def test_yaml_syntax_errors_say_where():
    issues = _issues(HEADER + "tables: [\n")
    assert len(issues) == 1 and issues[0].startswith("line ") and "not valid YAML" in issues[0]


def test_json_is_read_as_json():
    document = json.loads(json.dumps(_doc({"t": {"columns": {"a": "int"}}})))
    assert load(json.dumps(document)) == from_dict(document)


def test_a_file_path_is_read(tmp_path):
    path = tmp_path / "shop.model2data.yml"
    path.write_text(HEADER + "tables:\n  t:\n    columns:\n      a: int\n", encoding="utf-8")
    assert load(path).tables["t"].columns["a"] == Column(type="int")
    assert load(str(path)) == load(path)
    json_path = tmp_path / "shop.json"
    json_path.write_text(json.dumps(_doc({"t": {"columns": {"a": "int"}}})), encoding="utf-8")
    assert load(json_path) == load(path)


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------
def test_a_typo_in_a_hint_is_reported_at_its_path():
    issue = _one_issue(
        _doc({"t": {"columns": {"a": {"type": "int", "generate": {"nul_rate": 0.1}}}}})
    )
    assert issue.startswith("tables.t.columns.a.generate.nul_rate: unknown key")
    assert "null_rate" in issue


def test_a_missing_type_is_reported():
    issues = _issues(_doc({"t": {"columns": {"a": {"typ": "int"}}}}))
    assert "tables.t.columns.a: `type` is required" in issues
    assert any(issue.startswith("tables.t.columns.a.typ: unknown key") for issue in issues)


def test_a_parameter_of_another_distribution_is_reported():
    issue = _one_issue(
        _doc(
            {
                "t": {
                    "columns": {
                        "a": {
                            "type": "numeric",
                            "generate": {"distribution": {"kind": "normal", "median": 3}},
                        }
                    }
                }
            }
        )
    )
    assert issue.startswith("tables.t.columns.a.generate.distribution.median: unknown key")


def test_an_old_version_is_refused_with_a_way_forward():
    issue = _one_issue({"model2data": "0.1.0", "tables": {"t": {"columns": {"a": "int"}}}})
    assert issue.startswith("model2data: the document is written against spec 0.1.0")
    assert "model2data convert" in issue


def test_every_issue_is_reported_not_just_the_first():
    issues = _issues(
        _doc(
            {
                "t": {
                    "color": "orange",
                    "columns": {
                        "a": {"type": "int", "generate": {"null_rate": 1.5}},
                        "b": {"type": "text", "generate": {"min": 1}},
                    },
                }
            },
            run={"rows": 0},
        )
    )
    assert len(issues) == 4, issues
    assert any(issue.startswith("tables.t.color: must be a hex colour") for issue in issues)
    assert any(issue.startswith("tables.t.columns.a.generate.null_rate") for issue in issues)
    assert any(issue.startswith("tables.t.columns.b.generate.min") for issue in issues)
    assert any(issue.startswith("run.rows: must be 1 or more") for issue in issues)


def test_extensions_are_kept():
    document = _doc(
        {
            "t": {
                "x-layout": {"x": 1},
                "columns": {"a": {"type": "int", "x-pii": False, "generate": {"x-note": 1}}},
            }
        },
        **{"x-owner": "data team"},
    )
    model = from_dict(document)
    assert model.extensions == {"x-owner": "data team"}
    assert model.tables["t"].extensions == {"x-layout": {"x": 1}}
    assert model.tables["t"].columns["a"].extensions == {"x-pii": False}
    assert model.tables["t"].columns["a"].generate == {"x-note": 1}
    assert to_dict(model) == {
        "model2data": "0.2.0",
        "tables": {
            "t": {
                "columns": {"a": {"type": "int", "generate": {"x-note": 1}, "x-pii": False}},
                "x-layout": {"x": 1},
            }
        },
        "x-owner": "data team",
    }


# ---------------------------------------------------------------------------
# Checks beyond the schema, one test per rule, each at its path
# ---------------------------------------------------------------------------
def _parent_and_child(child_column: dict, parent_columns=None, **parent) -> dict:
    return _doc(
        {
            "customers": {
                "columns": parent_columns or {"id": {"type": "int", "pk": True}},
                **parent,
            },
            "orders": {"columns": {"id": {"type": "int", "pk": True}, "customer_id": child_column}},
        }
    )


def test_1_references_name_a_table_and_column_of_the_model():
    assert _one_issue(_parent_and_child({"type": "int", "references": "clients.id"})) == (
        'tables.orders.columns.customer_id.references: names the table "clients", '
        "which is not in the model"
    )
    issue = _one_issue(_parent_and_child({"type": "int", "references": {"to": "customers.uid"}}))
    assert issue.startswith(
        'tables.orders.columns.customer_id.references.to: names the column "uid"'
    )


def test_1_foreign_keys_relationships_groups_and_run_name_tables_of_the_model():
    issues = _issues(
        _doc(
            {
                "a": {
                    "columns": {"x": "int", "y": "int"},
                    "keys": [{"pk": ["x", "y"]}],
                    "foreign_keys": [
                        {"columns": ["x", "y"], "references": "b", "to_columns": ["x", "y"]}
                    ],
                }
            },
            relationships=[{"many_to_many": ["a.x", "c.id"]}],
            groups={"g": {"tables": ["a", "d"]}},
            run={"seed": 1, "rows_per_table": {"e": 5}, "table_seeds": {"f": 2}},
        )
    )
    assert issues == [
        'tables.a.foreign_keys.0.references: names the table "b", which is not in the model',
        'relationships.0.many_to_many.1: names the table "c", which is not in the model',
        'groups.g.tables.1: names the table "d", which is not in the model',
        'run.rows_per_table.e: names the table "e", which is not in the model',
        'run.table_seeds.f: names the table "f", which is not in the model',
    ]


def test_a_reference_onto_a_column_that_is_not_a_key_is_a_warning():
    document = _parent_and_child(
        {"type": "int", "references": "customers.email"},
        {"id": {"type": "int", "pk": True}, "email": "text"},
    )
    model = from_dict(document)  # conforms
    assert [str(issue) for issue in model.warnings] == [
        "warning: tables.orders.columns.customer_id.references: references customers.email, "
        "which is neither customers's primary key nor unique: child values are drawn from the "
        "values it holds, but nothing makes them one row each"
    ]
    assert [issue.severity for issue in model.warnings] == ["warning"]
    assert not from_dict(
        _parent_and_child(
            {"type": "int", "references": "customers.email"},
            {"id": {"type": "int", "pk": True}, "email": {"type": "text", "unique": True}},
        )
    ).warnings


def test_a_reference_onto_one_column_of_a_composite_key_is_a_warning():
    model = from_dict(
        _parent_and_child(
            {"type": "int", "references": "customers.a"},
            {"a": {"type": "int", "pk": True}, "b": {"type": "int", "pk": True}},
        )
    )
    assert len(model.warnings) == 1
    assert "composite primary key (a, b)" in model.warnings[0].message


def test_2_foreign_key_columns_pair_up():
    document = _doc(
        {
            "p": {"columns": {"x": "int", "y": "int", "z": "int"}},
            "c": {
                "columns": {"x": "int", "y": "int"},
                "foreign_keys": [
                    {"columns": ["x", "y"], "references": "p", "to_columns": ["x", "y", "z"]},
                    {"columns": ["x", "y"], "references": "p", "to_columns": ["x", "y"]},
                ],
            },
        }
    )
    with pytest.raises(ModelError) as raised:
        from_dict(document)
    assert [str(issue) for issue in raised.value.issues] == [
        "tables.c.foreign_keys.0.to_columns: has 3 columns and `columns` has 2: they pair up "
        "in order, so they must be as many"
    ]
    # Onto columns that are not a key of the parent: warnings, beside the error.
    assert [str(issue) for issue in raised.value.warnings] == [
        "warning: tables.c.foreign_keys.0.to_columns: (x, y, z) is not a key of p: child "
        "values are drawn from the ones it holds, but a parent of one row per value needs them "
        "to be its primary key, one of its keys, or a unique column",
        "warning: tables.c.foreign_keys.1.to_columns: (x, y) is not a key of p: child values "
        "are drawn from the ones it holds, but a parent of one row per value needs them to be "
        "its primary key, one of its keys, or a unique column",
    ]
    # `validate` on text adds the line each issue sits on; `from_dict` has no text.
    assert [str(replace(issue, line=None)) for issue in validate(json.dumps(document))][1:] == [
        str(issue) for issue in raised.value.warnings
    ]


def test_3_a_key_names_columns_of_its_own_table():
    issue = _one_issue(_doc({"t": {"columns": {"a": "int"}, "keys": [{"unique": ["a", "b"]}]}}))
    assert issue == 'tables.t.keys.0.unique.1: names "b", which is not a column of t'


def test_a_table_has_at_most_one_primary_key():
    issue = _one_issue(
        _doc(
            {
                "t": {
                    "columns": {"a": {"type": "int", "pk": True}, "b": "int", "c": "int"},
                    "keys": [{"pk": ["b", "c"]}],
                }
            }
        )
    )
    assert issue.startswith("tables.t.keys.0: is a second primary key of t")


@pytest.mark.parametrize(
    "column, hint, fragment",
    [
        ({"type": "text"}, {"min": 1}, 'a numeric column, and "text" is not numeric'),
        ({"type": "int"}, {"true_rate": 0.5}, "a boolean column"),
        ({"type": "int"}, {"weights": {"a": 1}}, "an enum column"),
        ({"type": "int"}, {"skew": 0.5}, "a foreign-key column, and this column references"),
        ({"type": "time"}, {"growth": 0.5}, 'a date or timestamp column, and "time" is neither'),
        ({"type": "int", "unique": True}, {"distinct": 5}, "a column that is unique"),
        ({"type": "int", "not_null": True}, {"null_rate": 0.1}, "this one is not_null"),
    ],
)
def test_4_a_hint_sits_on_a_column_of_its_kind(column, hint, fragment):
    issue = _one_issue(
        _doc(
            {
                "t": {
                    "columns": {
                        "id": {"type": "int", "pk": True},
                        "c": {**column, "generate": hint},
                    }
                }
            }
        )
    )
    assert issue.startswith(f"tables.t.columns.c.generate.{next(iter(hint))}: ")
    assert fragment in issue


def test_4_a_column_typed_with_an_enum_is_of_kind_enum_only():
    # `maintenance_type` contains "int", but it is an enum, not an integer.
    document = _doc(
        {"t": {"columns": {"m": {"type": "maintenance_type", "generate": {"min": 1}}}}},
        enums={"maintenance_type": ["a", "b"]},
    )
    assert "a numeric column" in _one_issue(document)


def test_4_real_money_and_number_are_numeric():
    for type_name in ("real", "money", "number", "double precision"):
        assert not _issues(
            _doc({"t": {"columns": {"a": {"type": type_name, "generate": {"min": 1, "max": 2}}}}})
        )


def test_5_weights_name_members_of_the_enum():
    issue = _one_issue(
        _doc(
            {
                "orders": {
                    "columns": {
                        "status": {"type": "order_status", "generate": {"weights": {"returned": 2}}}
                    }
                }
            },
            enums={"order_status": ["pending", "paid"]},
        )
    )
    assert issue == (
        'tables.orders.columns.status.generate.weights: weighs "returned", which is not a '
        "member of order_status (members: pending, paid)"
    )


def test_5_integer_members_are_their_decimal_text():
    model = from_dict(
        _doc(
            {"t": {"columns": {"level": {"type": "lvl", "generate": {"weights": {1: 5}}}}}},
            enums={"lvl": [1, 2, 3]},
        )
    )
    assert model.enums["lvl"].members == ["1", "2", "3"]
    assert model.tables["t"].columns["level"].generate == {"weights": {"1": 5}}


@pytest.mark.parametrize(
    "after, message",
    [
        ("missing", 'names "missing", which is not a column of t'),
        ("label", "names label, which is not a date or timestamp column"),
        ("b", "names the column itself: `after` names another column"),
    ],
)
def test_6_after_names_another_temporal_column(after, message):
    issue = _one_issue(
        _doc(
            {
                "t": {
                    "columns": {
                        "a": "timestamp",
                        "label": "text",
                        "b": {"type": "timestamp", "generate": {"after": after}},
                    }
                }
            }
        )
    )
    assert issue == f"tables.t.columns.b.generate.after: {message}"


def test_6_after_hints_do_not_form_a_cycle():
    issue = _one_issue(
        _doc(
            {
                "t": {
                    "columns": {
                        "a": {"type": "date", "generate": {"after": "c"}},
                        "b": {"type": "date", "generate": {"after": "a"}},
                        "c": {"type": "timestamp", "generate": {"after": "b"}},
                    }
                }
            }
        )
    )
    assert (
        issue
        == "tables.t.columns.a.generate.after: the after hints of t form a cycle: a -> c -> b -> a"
    )


def test_7_integer_bounds_are_whole_and_in_order():
    issues = _issues(
        _doc(
            {
                "t": {
                    "columns": {
                        "a": {"type": "int", "generate": {"min": 1.5}},
                        "b": {"type": "int", "generate": {"min": 150}},
                        "c": {"type": "int", "generate": {"min": 5, "max": 2}},
                        "d": {"type": "numeric", "generate": {"min": 150}},
                        "e": {"type": "int", "generate": {"min": 2.0, "max": 3}},
                    }
                }
            }
        )
    )
    assert issues == [
        "tables.t.columns.a.generate.min: must be a whole number on an integer column (got 1.5)",
        "tables.t.columns.b.generate.min: min 150 exceeds max 100 (max left out defaults to 100)",
        "tables.t.columns.c.generate.min: min 5 exceeds max 2",
    ]


def test_8_null_rate_is_only_on_a_nullable_column():
    issue = _one_issue(
        _doc(
            {"t": {"columns": {"id": {"type": "int", "pk": True, "generate": {"null_rate": 0.1}}}}}
        )
    )
    assert issue == (
        "tables.t.columns.id.generate.null_rate: only sits on a nullable column, and this one "
        "is in the primary key: it would have no rows to null"
    )


def test_9_table_seeds_need_a_seed():
    issue = _one_issue(_doc({"t": {"columns": {"a": "int"}}}, run={"table_seeds": {"t": 2}}))
    assert issue.startswith("run.table_seeds: needs run.seed")


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_dump_writes_the_reference_example_byte_for_byte():
    text = REFERENCE.read_text(encoding="utf-8")
    assert dump(load(REFERENCE)) == text


def _rich_model() -> Model:
    return Model(
        version="0.2.0",
        name="everything",
        description="Line one.\nLine two, with: a colon.\n",
        enums={
            "status": Enum(["active", "no", "on hold", "1"], None),
            "billing.plan": Enum(["free", "pro"], {"free": "No charge", "pro": None}),
        },
        tables={
            "raw.users": Table(
                description="Catalogue: one row per user",
                color="#ea580c",
                role="dimension",
                columns={
                    "id": Column("bigint", pk=True),
                    "email": Column("email", unique=True, not_null=True),
                    "country": Column("country"),
                    "plan": Column("billing.plan", default="free"),
                    "created at": Column("timestamp", extensions={"x-default-expression": "now()"}),
                    "status": Column(
                        "status",
                        description="# not a comment, and 'quoted'",
                        generate={"weights": {"active": 3, "no": 1}, "null_rate": 0.1},
                    ),
                },
                extensions={"x-layout": {"x": 10, "y": [1, 2]}},
            ),
            "orders": Table(
                columns={
                    "id": Column("bigint", pk=True, increment=True),
                    "user_id": Column(
                        "bigint",
                        not_null=True,
                        references=Reference("raw.users.id"),
                        generate={"skew": 0.8},
                    ),
                    "total": Column(
                        "numeric(10,2)",
                        measure=True,
                        generate={
                            "min": 0.5,
                            "max": 1e6,
                            "distribution": {"kind": "lognormal", "median": 120, "spread": 0.7},
                        },
                    ),
                    "paid": Column("boolean", default=False, generate={"true_rate": 0.9}),
                    "shipped_at": Column("timestamp", generate={"after": "placed_at"}),
                    "placed_at": Column("timestamp", not_null=True),
                    "note": Column("text", default="", measure=False),
                },
            ),
            "profiles": Table(
                columns={
                    "user_id": Column(
                        "bigint", pk=True, references=Reference("raw.users.id", one_to_one=True)
                    ),
                    "a": Column("int", not_null=True),
                    "b": Column("int", not_null=True),
                },
                keys=[Key("unique", ["a", "b"])],
            ),
            "links": Table(
                columns={"a": Column("int"), "b": Column("int")},
                foreign_keys=[ForeignKey(["a", "b"], "profiles", ["a", "b"], one_to_one=True)],
            ),
        },
        relationships=[Relationship(["orders.id", "profiles.a"], "Loosely related")],
        groups={"sales": Group(["orders", "raw.users"], "#fff", "The sales tables")},
        run=Run(
            rows=50,
            rows_per_table={"orders": 200},
            seed=7,
            table_seeds={"orders": 2},
            as_of="2026-01-01",
            locale="nl_BE",
            shape=Shape(business_hours=True, growth=0.5, seasonality=0.2, skew=0.3),
        ),
        extensions={"x-owner": "data team"},
    )


def test_load_of_dump_is_the_model():
    model = _rich_model()
    assert load(dump(model)) == model


def test_dump_is_canonical_and_stable():
    model = _rich_model()
    text = dump(model)
    assert dump(load(text)) == text
    assert text.startswith("# yaml-language-server: $schema=")
    assert '  status: [active, "no", on hold, "1"]\n' in text
    assert '    description: "Catalogue: one row per user"\n' in text
    assert "      id: {type: bigint, pk: true}\n" in text
    assert "      country: country\n" in text
    assert (
        "user_id: {type: bigint, pk: true, references: {to: raw.users.id, one_to_one: true}}"
        in text
    )
    assert "description: |\n  Line one.\n  Line two, with: a colon.\n" in text
    positions = [
        text.index(f"\n{key}:") for key in ("enums", "tables", "relationships", "groups", "run")
    ]
    assert positions == sorted(positions)


@pytest.mark.parametrize(
    "value",
    [
        "yes",
        "NO",
        "null",
        "~",
        "true",
        "1.5",
        "0x1f",
        "2026-01-01",
        "- item",
        "a: b",
        "a #b",
        "#x",
        "trailing ",
        " leading",
        "multi\nline",
        "tab\there",
        'back\\slash "quote"',
        "ünïcödé",
        "[flow]",
        "{flow}",
        "a, b",
        "...",
        "---",
        "@at",
        "`tick`",
        "",
        "\u2028",
    ],
)
def test_any_string_round_trips(value):
    model = Model(
        tables={"t": Table(columns={"c": Column("text", description=value, default=value)})},
        enums={"e": Enum([value or "x"])},
    )
    assert load(dump(model)) == model


def test_to_dict_is_from_dicts_inverse():
    model = _rich_model()
    assert from_dict(copy.deepcopy(to_dict(model))) == model


# ---------------------------------------------------------------------------
# Into the generator
# ---------------------------------------------------------------------------
def test_to_engine_maps_the_model_onto_the_generators_inputs():
    inputs = to_engine(_rich_model())
    assert list(inputs.tables) == ["raw.users", "orders", "profiles", "links"]
    users = inputs.tables["raw.users"]
    assert users.name == "raw.users"
    assert users.note == {"role": "dimension"}
    assert users.description == "Catalogue: one row per user"
    columns = {column.name: column for column in users.columns}
    assert columns["id"].settings == {"pk"}
    assert columns["email"].settings == {"unique", "not null"}
    assert columns["plan"].enum_values == ["free", "pro"]
    assert columns["plan"].default == "free"
    assert columns["status"].note == {"weights": {"active": 3, "no": 1}, "null_rate": 0.1}
    assert columns["status"].description == "# not a comment, and 'quoted'"

    orders = {column.name: column for column in inputs.tables["orders"].columns}
    assert orders["id"].settings == {"pk", "increment"}
    assert orders["total"].note == {
        "min": 0.5,
        "max": 1e6,
        "distribution": "lognormal",
        "median": 120,
        "spread": 0.7,
        "measure": True,
    }

    profiles = {column.name: column for column in inputs.tables["profiles"].columns}
    # A one-to-one child holds each parent at most once.
    assert profiles["user_id"].settings == {"pk", "unique"}
    assert inputs.tables["profiles"].composite_keys == [{"columns": ["a", "b"], "type": "unique"}]

    assert inputs.refs == [
        {
            "source_table": "orders",
            "source_column": "user_id",
            "target_table": "raw.users",
            "target_column": "id",
        },
        {
            "source_table": "profiles",
            "source_column": "user_id",
            "target_table": "raw.users",
            "target_column": "id",
            "one_to_one": True,
        },
        {
            "source_table": "links",
            "source_column": "a",
            "target_table": "profiles",
            "target_column": "a",
            "one_to_one": True,
        },
        {
            "source_table": "links",
            "source_column": "b",
            "target_table": "profiles",
            "target_column": "b",
            "one_to_one": True,
        },
    ]
    assert inputs.many_to_many == [
        {
            "source_table": "orders",
            "source_column": "id",
            "target_table": "profiles",
            "target_column": "a",
        }
    ]
    assert inputs.run.seed == 7


def test_several_pk_columns_are_one_composite_primary_key():
    model = from_dict(
        _doc(
            {"t": {"columns": {"a": {"type": "int", "pk": True}, "b": {"type": "int", "pk": True}}}}
        )
    )
    table = to_engine(model).tables["t"]
    assert table.composite_keys == [{"columns": ["a", "b"], "type": "pk"}]
    assert all("pk" not in column.settings for column in table.columns)


def test_a_whole_float_bound_on_an_integer_column_reaches_the_generator_as_an_int():
    model = from_dict(_doc({"t": {"columns": {"a": {"type": "int", "generate": {"min": 2.0}}}}}))
    note = to_engine(model).tables["t"].columns[0].note
    assert note == {"min": 2} and isinstance(note["min"], int)


def test_an_enum_type_is_matched_case_insensitively():
    model = from_dict(_doc({"t": {"columns": {"s": "Status"}}}, enums={"status": ["a"]}))
    assert to_engine(model).tables["t"].columns[0].enum_values == ["a"]
