"""The words each kind of schema violation is reported in, one case per kind."""

from __future__ import annotations

from datetime import date

import pytest
from test_model import _doc, _issues

COLUMNS = {"id": {"type": "integer", "pk": True}}


def _column(value) -> dict:
    return _doc({"t": {"columns": {**COLUMNS, "x": value}}})


def _run(**run) -> dict:
    return _doc({"t": {"columns": COLUMNS}}, run=run)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            "- 1\n- 2\n", "a model is a mapping at the top level, not a list", id="not-a-mapping"
        ),
        pytest.param(
            _column(None), "tables.t.columns.x: must be a string or a mapping, not null", id="null"
        ),
        pytest.param(
            _column(True),
            "tables.t.columns.x: must be a string or a mapping, not a boolean",
            id="boolean",
        ),
        pytest.param(
            _column(1.5),
            "tables.t.columns.x: must be a string or a mapping, not the number 1.5",
            id="number",
        ),
        pytest.param(
            _column([1]), "tables.t.columns.x: must be a string or a mapping, not a list", id="list"
        ),
        pytest.param(
            _column({"type": "integer", "generate": {"distribution": {"kind": "zipf"}}}),
            "tables.t.columns.x.generate.distribution.kind: must be one of "
            '"uniform", "normal", "lognormal", "exponential", not "zipf"',
            id="distribution-kind",
        ),
        pytest.param(
            _column({"type": "integer", "measure": "total"}),
            'tables.t.columns.x.measure: must be one of "sum", "average", "min", "max", '
            '"median", "count", "count_distinct", not "total"',
            id="measure",
        ),
        pytest.param(
            _doc({"t": {"color": "red", "columns": COLUMNS}}),
            'tables.t.color: must be a hex colour such as "#ea580c" '
            '(quoted in YAML, where # starts a comment) (got "red")',
            id="colour",
        ),
        pytest.param(
            _run(as_of="2026-13-01"),
            'run.as_of: must be an ISO 8601 date, YYYY-MM-DD (got "2026-13-01")',
            id="date",
        ),
        pytest.param(
            _doc({"t": {"columns": COLUMNS, "incremental": {"update_rate": 2}}}),
            "tables.t.incremental.update_rate: must be 1 or less (got 2)",
            id="maximum",
        ),
        pytest.param(
            _doc({"t": {"columns": COLUMNS, "keys": [{"pk": ["id", "id"]}]}}),
            "tables.t.keys.0.pk: lists the same item more than once",
            id="unique-items",
        ),
        pytest.param(
            _doc({"t": {"columns": COLUMNS, "keys": [{}]}}),
            "tables.t.keys.0: must not be empty",
            id="empty-key",
        ),
        pytest.param(
            _doc({"t": {"columns": COLUMNS}}, groups={"g": {"tables": []}}),
            "groups.g.tables: must not be empty",
            id="min-items",
        ),
    ],
)
def test_a_violation_is_reported_in_words(document, expected):
    assert expected in _issues(document)


def test_a_key_with_both_pk_and_unique_says_to_write_two_keys():
    document = _doc({"t": {"columns": COLUMNS, "keys": [{"pk": ["id"], "unique": ["id"]}]}})
    assert "tables.t.keys.0: a key is `pk` or `unique`, not both: write two keys" in _issues(
        document
    )


def test_a_newer_spec_says_to_upgrade():
    issues = _issues({"model2data": "0.3.0", "tables": {"t": {"columns": COLUMNS}}})
    assert issues == [
        "model2data: the document is written against spec 0.3.0, and this reader implements "
        "spec 0.2.0 (0.2.x). Upgrade model2data to read it."
    ]


def _table(**table) -> dict:
    return _doc({"t": {"columns": COLUMNS, **table}})


TWO = {"a": "integer", "b": "integer"}


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            _doc({"t": {"columns": COLUMNS}}, enums={"s": [1, "1"]}),
            'enums.s: lists the member "1" twice (an integer member is its decimal text)',
            id="enum-member-twice",
        ),
        pytest.param(
            _table(keys=["id"]),
            'tables.t.keys.0: must be a mapping, not the string "id"',
            id="key-not-a-mapping",
        ),
        pytest.param(
            _table(keys=[{"pk": ["id"]}, {"pk": ["id"]}]),
            "tables.t.keys.1: is a second primary key of t: a table has at most one",
            id="second-primary-key",
        ),
        pytest.param(
            _table(incremental={"updated_at": "nope"}),
            'tables.t.incremental.updated_at: names "nope", which is not a column of t',
            id="updated-at-not-a-column",
        ),
        pytest.param(
            _doc(
                {
                    "p": {"columns": TWO, "keys": [{"pk": ["a", "b"]}]},
                    "t": {
                        "columns": TWO,
                        "foreign_keys": [
                            {"columns": ["a", "zz"], "references": "p", "to_columns": ["a", "b"]}
                        ],
                    },
                }
            ),
            'tables.t.foreign_keys.0.columns.1: names "zz", which is not a column of t',
            id="foreign-key-column",
        ),
        pytest.param(
            _doc(
                {
                    "t": {
                        "columns": TWO,
                        "foreign_keys": [
                            {"columns": ["a", "b"], "references": "nope", "to_columns": ["a", "b"]}
                        ],
                    }
                }
            ),
            'tables.t.foreign_keys.0.references: names the table "nope", which is not in the model',
            id="foreign-key-table",
        ),
        pytest.param(
            _doc(
                {
                    "p": {"columns": TWO},
                    "t": {
                        "columns": TWO,
                        "foreign_keys": [
                            {"columns": ["a", "b"], "references": "p", "to_columns": ["a", "zz"]}
                        ],
                    },
                }
            ),
            'tables.t.foreign_keys.0.to_columns.1: names "zz", which is not a column of p',
            id="foreign-key-to-column",
        ),
        pytest.param(
            _doc({"t": {"columns": COLUMNS}}, relationships=[{"many_to_many": ["t.id", "x.id"]}]),
            'relationships.0.many_to_many.1: names the table "x", which is not in the model',
            id="many-to-many-table",
        ),
        pytest.param(
            _doc(
                {"t": {"columns": COLUMNS}, "u": {"columns": COLUMNS}},
                relationships=[{"many_to_many": ["t.id", "u.zz"]}],
            ),
            'relationships.0.many_to_many.1: names the column "zz", which u does not have',
            id="many-to-many-column",
        ),
        pytest.param(
            _doc(
                {"t": {"columns": COLUMNS}, "u": {"columns": COLUMNS}},
                relationships=[{"many_to_many": ["t.id", "u.id", "t.id"]}],
            ),
            "relationships.0.many_to_many: may have at most 2 items",
            id="max-items",
        ),
        pytest.param(
            _column(
                {"type": "integer", "generate": {"distribution": {"kind": "normal", "stddev": 0}}}
            ),
            "tables.t.columns.x.generate.distribution.stddev: must be greater than 0 (got 0)",
            id="exclusive-minimum",
        ),
        pytest.param(
            _column({"type": "integer", "generate": {"min": "low"}}),
            'tables.t.columns.x.generate.min: must be a number, not the string "low"',
            id="string",
        ),
        pytest.param(
            _column({"type": "integer", "generate": {"min": {"a": 1}}}),
            "tables.t.columns.x.generate.min: must be a number, not a mapping",
            id="mapping",
        ),
        pytest.param(
            _doc({"": {"columns": COLUMNS}}),
            "tables: must be 1 to 200 characters (got 0)",
            id="name-length",
        ),
        pytest.param(
            _run(locale="english!"),
            'run.locale: must be a locale such as en_US or nl_BE (got "english!")',
            id="locale",
        ),
        pytest.param(
            {"model2data": "abc", "tables": {"t": {"columns": COLUMNS}}},
            'model2data: must match the pattern ^0\\.2(\\.(0|[1-9][0-9]*))?$ (got "abc")',
            id="version-pattern",
        ),
        pytest.param(
            {"tables": {"t": {"columns": COLUMNS}}}, "`model2data` is required", id="no-version"
        ),
    ],
)
def test_a_check_is_reported_in_words(document, expected):
    assert expected in _issues(document)


def test_an_enum_type_in_another_case_is_that_enum():
    document = _doc({"t": {"columns": {**COLUMNS, "s": "STATUS"}}}, enums={"status": ["a", "b"]})
    assert _issues(document) == []


def test_a_python_value_that_is_not_json_is_shown_as_itself():
    document = _run(as_of=date(2026, 1, 1))
    assert _issues(document) == ["run.as_of: must be a string, not datetime.date(2026, 1, 1)"]
