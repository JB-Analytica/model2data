"""The words each kind of schema violation is reported in, one case per kind."""

from __future__ import annotations

import copy
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
    issues = _issues({"model2data": "0.5.0", "tables": {"t": {"columns": COLUMNS}}})
    assert issues == [
        "model2data: the document is written against spec 0.5.0, and this reader implements "
        "spec 0.4.0 (0.2.x, 0.3.x and 0.4.x). Upgrade model2data to read it."
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
            'model2data: must match the pattern ^0\\.[234](\\.(0|[1-9][0-9]*))?$ (got "abc")',
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


# ---------------------------------------------------------------------------
# Defects (spec 0.3.0)
# ---------------------------------------------------------------------------
SHOP = {
    "c": {
        "columns": {
            "id": {"type": "integer", "pk": True},
            "email": {"type": "email", "unique": True},
            "name": {"type": "text", "not_null": True},
            "nick": "text",
            "tier": "tier",
            "born": "date",
        }
    },
    "o": {
        "incremental": {"new_per_day": 5, "update_rate": 0.1, "updated_at": "at"},
        "columns": {
            "id": {"type": "integer", "pk": True},
            "c_id": {"type": "integer", "references": "c.id"},
            "at": {"type": "timestamp", "not_null": True},
        },
    },
    "p": {"columns": TWO, "keys": [{"pk": ["a", "b"]}]},
    "n": {"columns": {"label": "text"}},
}


def _defects(table: str, *entries, run: dict | None = None) -> dict:
    tables = copy.deepcopy(SHOP)
    tables[table]["defects"] = list(entries)
    document = {"model2data": "0.3.0", "tables": tables, "enums": {"tier": ["a", "b"]}}
    if run is not None:
        document["run"] = run
    return document


def test_the_shop_with_every_defect_is_valid():
    document = _defects(
        "o",
        {"type": "duplicate_keys", "count": 1},
        {"type": "orphan_foreign_keys", "column": "c_id", "share": 0.1},
        {"type": "nulls", "column": "at", "count": 0},
        {"type": "late_arriving", "column": "at", "count": 1},
        {"type": "late_updates", "count": 1},
    )
    assert _issues(document) == []
    document = _defects(
        "c",
        {"type": "duplicate_keys", "column": "email", "count": 1},
        {"type": "invalid_values", "column": "tier", "count": 1},
        {"type": "messy_text", "column": "nick", "count": 1},
        {"type": "nulls", "column": "id", "count": 1},
        run={"defects": "training", "rows": 10},
    )
    assert _issues(document) == []


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            _defects("c", {"type": "typo", "count": 1}),
            'tables.c.defects.0.type: must be one of "duplicate_keys", "orphan_foreign_keys", '
            '"nulls", "invalid_values", "late_arriving", "late_updates", "messy_text", '
            '"overlapping_history", not "typo"',
            id="unknown-type",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "nope", "count": 1}),
            'tables.c.defects.0.column: names "nope", which is not a column of c',
            id="unknown-column",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "name", "share": 1.5}),
            "tables.c.defects.0.share: must be 1 or less (got 1.5)",
            id="share-above-one",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "name", "count": -1}),
            "tables.c.defects.0.count: must be 0 or more (got -1)",
            id="negative-count",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "name", "count": 1, "share": 0.1}),
            "tables.c.defects.0: gives both `count` and `share`: give one",
            id="count-and-share",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "name"}),
            "tables.c.defects.0: needs `count` (rows) or `share` (a fraction of the table's rows)",
            id="neither-count-nor-share",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "name", "count": 1, "rows": 3}),
            'tables.c.defects.0.rows: unknown key "rows": the keys allowed here are type, '
            "column, count, share",
            id="unknown-key",
        ),
        pytest.param(
            _defects(
                "c",
                {"type": "nulls", "column": "name", "count": 1},
                {"type": "nulls", "column": "name", "count": 2},
            ),
            "tables.c.defects.1: repeats defects.0 (nulls on name): give each type and column once",
            id="repeated",
        ),
        pytest.param(
            _defects("c", {"type": "duplicate_keys", "count": 10}, run={"rows": 10}),
            "tables.c.defects.0.count: asks for 10 duplicates, and the run gives c 10 rows: a "
            "duplicate repeats another row's key, so at most 9 can",
            id="more-duplicates-than-rows",
        ),
        pytest.param(
            _defects("c", {"type": "duplicate_keys", "count": 4}, run={"rows_per_table": {"c": 3}}),
            "tables.c.defects.0.count: asks for 4 duplicates, and the run gives c 3 rows: a "
            "duplicate repeats another row's key, so at most 2 can",
            id="more-duplicates-than-table-rows",
        ),
        pytest.param(
            _defects("c", {"type": "duplicate_keys", "column": "nick", "count": 1}),
            "tables.c.defects.0.column: names nick, which is not a key of c: duplicate_keys "
            "repeats a primary key or a unique column (it has a `unique` test to break)",
            id="duplicate-not-a-key",
        ),
        pytest.param(
            _defects("n", {"type": "duplicate_keys", "count": 1}),
            "tables.n.defects.0: n has no primary key to repeat: name a unique column with "
            "`column`",
            id="duplicate-no-primary-key",
        ),
        pytest.param(
            _defects("c", {"type": "orphan_foreign_keys", "column": "name", "count": 1}),
            "tables.c.defects.0.column: names name, which is not a foreign key: "
            "orphan_foreign_keys needs a column with `references` (or in `foreign_keys`), "
            "whose relationships test it breaks",
            id="orphan-not-a-foreign-key",
        ),
        pytest.param(
            _defects("c", {"type": "orphan_foreign_keys", "count": 1}),
            "tables.c.defects.0: `column` is required: orphan_foreign_keys breaks one column",
            id="orphan-no-column",
        ),
        pytest.param(
            _defects("c", {"type": "nulls", "column": "nick", "count": 1}),
            "tables.c.defects.0.column: names nick, which has no not_null test (it is not "
            "`not_null: true`, nor the table's one-column primary key), so nulls in it break "
            "nothing; `generate.null_rate` makes a nullable column null",
            id="nulls-nullable",
        ),
        pytest.param(
            _defects("p", {"type": "nulls", "column": "a", "count": 1}),
            "tables.p.defects.0.column: names a, which has no not_null test (it is not "
            "`not_null: true`, nor the table's one-column primary key), so nulls in it break "
            "nothing; `generate.null_rate` makes a nullable column null",
            id="nulls-composite-key-member",
        ),
        pytest.param(
            _defects("c", {"type": "invalid_values", "column": "nick", "count": 1}),
            "tables.c.defects.0.column: names nick, which has no allowed set: invalid_values "
            "needs a column typed with an enum, whose accepted_values test it breaks",
            id="invalid-no-allowed-set",
        ),
        pytest.param(
            _defects("c", {"type": "messy_text", "column": "tier", "count": 1}),
            "tables.c.defects.0.column: names tier, an enum column: its accepted_values test "
            "would fail; use invalid_values for that",
            id="messy-enum",
        ),
        pytest.param(
            _defects("c", {"type": "messy_text", "column": "born", "count": 1}),
            "tables.c.defects.0.column: names born, which is not a text column (date is not text)",
            id="messy-not-text",
        ),
        pytest.param(
            _defects("o", {"type": "messy_text", "column": "c_id", "count": 1}),
            "tables.o.defects.0.column: names c_id, which is not a text column (integer is not "
            "text)",
            id="messy-integer",
        ),
        pytest.param(
            _defects("n", {"type": "messy_text", "column": "label", "count": 1})
            | {
                "tables": {
                    **SHOP,
                    "n": {
                        "columns": {"label": {"type": "text", "pk": True}},
                        "defects": [{"type": "messy_text", "column": "label", "count": 1}],
                    },
                }
            },
            "tables.n.defects.0.column: names label, a key or foreign key: messy_text is for "
            "descriptive text, and changing a key's text breaks the joins on it",
            id="messy-key",
        ),
        pytest.param(
            _defects("c", {"type": "late_arriving", "count": 1}),
            "tables.c.defects.0: c inserts no rows after the first day: late_arriving needs "
            "`incremental.new_per_day`, and a run of several days",
            id="late-arriving-not-incremental",
        ),
        pytest.param(
            _defects("o", {"type": "late_arriving", "column": "c_id", "count": 1}),
            "tables.o.defects.0.column: names c_id, which is not a date or timestamp column",
            id="late-arriving-not-temporal",
        ),
        pytest.param(
            _defects("c", {"type": "late_updates", "count": 1}),
            "tables.c.defects.0: c has no updates to backdate: late_updates needs "
            "`incremental.update_rate` and `incremental.updated_at`, and a run of several days",
            id="late-updates-not-incremental",
        ),
        pytest.param(
            _defects("o", {"type": "late_updates", "column": "at", "count": 1}),
            "tables.o.defects.0.column: late_updates takes no column: it backdates "
            "`incremental.updated_at`",
            id="late-updates-column",
        ),
        pytest.param(
            _defects("c", run={"defects": "chaos"}),
            'run.defects: must be one of "clean", "messy", "training", "none", not "chaos"',
            id="unknown-preset",
        ),
    ],
)
def test_a_defect_check_is_reported_in_words(document, expected):
    assert expected in _issues(document)


def test_late_arriving_needs_a_temporal_column():
    document = {
        "model2data": "0.3.0",
        "tables": {
            "t": {
                "incremental": {"new_per_day": 2},
                "columns": {"id": {"type": "integer", "pk": True}},
                "defects": [{"type": "late_arriving", "count": 1}],
            }
        },
    }
    assert _issues(document) == [
        "tables.t.defects.0: t has no date or timestamp column for rows to arrive late on"
    ]


def test_defects_need_spec_0_3():
    document = _defects(
        "c", {"type": "nulls", "column": "name", "count": 1}, run={"defects": "messy"}
    )
    document["model2data"] = "0.2.0"
    assert _issues(document) == [
        "tables.c.defects: `defects` is spec 0.3.0, and the document is written against 0.2: "
        "write `model2data: 0.3.0`",
        "run.defects: `defects` is spec 0.3.0, and the document is written against 0.2: "
        "write `model2data: 0.3.0`",
    ]


def test_a_defect_that_is_not_a_mapping_is_left_to_the_schema():
    document = _defects("c", "nulls")
    assert _issues(document) == ['tables.c.defects.0: must be a mapping, not the string "nulls"']


@pytest.mark.parametrize("kind", ["nulls", "invalid_values", "messy_text"])
def test_a_defect_on_one_column_needs_it(kind):
    assert _issues(_defects("c", {"type": kind, "count": 1})) == [
        f"tables.c.defects.0: `column` is required: {kind} breaks one column"
    ]


def test_a_column_that_is_not_a_name_is_left_to_the_schema():
    issues = _issues(_defects("c", {"type": "nulls", "column": 5, "count": 1}))
    assert issues == ["tables.c.defects.0.column: must be a string, not the number 5"]


def test_late_arriving_on_an_unknown_column_is_reported_once():
    issues = _issues(_defects("o", {"type": "late_arriving", "column": "nope", "count": 1}))
    assert issues == ['tables.o.defects.0.column: names "nope", which is not a column of o']


def _history(table: str, **incremental) -> dict:
    document = _defects("o")
    del document["tables"]["o"]["defects"]
    target = document["tables"][table]
    target["incremental"] = {**target.get("incremental", {}), "history": True, **incremental}
    return document


def test_a_history_on_a_keyed_table_is_valid():
    document = _history("o")
    document["tables"]["o"]["defects"] = [{"type": "overlapping_history", "count": 1}]
    assert _issues(document) == []


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            _history("n"),
            "tables.n.incremental.history: n has no primary key: a history keeps the versions "
            "of each key, so it needs one",
            id="no-primary-key",
        ),
        pytest.param(
            _history("o")
            | {
                "tables": {
                    **_history("o")["tables"],
                    "o_history": {"columns": {"id": "int"}},
                }
            },
            "tables.o.incremental.history: writes the table o_history, and the model already has "
            "a table of that name",
            id="table-taken",
        ),
        pytest.param(
            _defects("o", {"type": "overlapping_history", "column": "at", "count": 1}),
            "tables.o.defects.0.column: overlapping_history takes no column: it breaks the "
            "validity of versions in o_history",
            id="overlap-column",
        ),
        pytest.param(
            _defects("o", {"type": "overlapping_history", "count": 1}),
            "tables.o.defects.0: o keeps no history: overlapping_history needs "
            "`incremental.history: true`, and a run of several days",
            id="overlap-without-history",
        ),
    ],
)
def test_a_history_check_is_reported_in_words(document, expected):
    assert expected in _issues(document)


def test_history_columns_must_be_free():
    document = _history("o")
    document["tables"]["o"]["columns"]["valid_to"] = "timestamp"
    document["tables"]["o"]["columns"]["is_current"] = "boolean"
    assert _issues(document) == [
        "tables.o.incremental.history: adds the columns valid_from, valid_to, is_current to "
        "o_history, and o already has valid_to, is_current"
    ]


def test_history_needs_spec_0_3():
    document = _history("o")
    document["model2data"] = "0.2.0"
    assert _issues(document) == [
        "tables.o.incremental.history: `incremental.history` is spec 0.3.0, and the document "
        "is written against 0.2: write `model2data: 0.3.0`"
    ]
