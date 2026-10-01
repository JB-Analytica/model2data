"""The words each kind of schema violation is reported in, one case per kind."""

from __future__ import annotations

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
