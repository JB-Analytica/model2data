"""The comparison grammar shared by metrics filters and table rules."""

from datetime import datetime

import pandas as pd
import pytest

from model2data.generate import comparison as cmp
from model2data.generate.comparison import Comparison, Typed


def test_parse_reads_values_lists_and_operators_in_order():
    found = cmp.parse(
        {
            "a": 5,
            "b": ["x", "y"],
            "c": {"gt": 1, "lte": {"column": "d"}, "x-note": "skipped"},
            "x-ext": {"eq": 1},
        }
    )
    assert [(c.column, c.operator, c.operand) for c in found] == [
        ("a", "eq", 5),
        ("b", "in", ["x", "y"]),
        ("c", "gt", 1),
        ("c", "lte", {"column": "d"}),
    ]
    assert found[3].columns() == ["c", "d"]
    assert found[0].columns() == ["a"]


def test_operand_shapes():
    ok = [
        ("eq", 1, False),
        ("eq", {"column": "t.a"}, False),
        ("gte", {"run": "as_of"}, True),
        ("in", [1, 2], False),
        ("between", [1, 2], False),
        ("is_null", True, False),
    ]
    for operator, operand, allow_run in ok:
        assert cmp.operand_problems(operator, operand, allow_run=allow_run) == [], operand
    bad = [
        (("nope", 1), "unknown operator"),
        (("is_null", 1), "true or false"),
        (("in", 1), "list of values"),
        (("in", []), "at least one"),
        (("between", [1]), "two values"),
        (("eq", [1, 2]), "not a list"),
        (("eq", {"column": 5}), "name of a column"),
        (("eq", {"column": "a", "via": "b"}), "name of a column"),
        (("eq", {"run": "as_of"}), "a metric has no run"),
        (("eq", {"other": 1}), "takes a value"),
        (("eq", None), "takes a value"),
    ]
    for (operator, operand), message in bad:
        problems = cmp.operand_problems(operator, operand)
        assert problems and message in problems[0].message, (operator, operand, problems)
    # A list holds literals: a reference in it says so, at its index.
    problems = cmp.operand_problems("in", [1, {"column": "a"}])
    assert problems[0].path == (1,)
    assert "literals only" in problems[0].message
    problems = cmp.operand_problems("eq", {"run": "now"}, allow_run=True)
    assert "only one is `as_of`" in problems[0].message


def test_the_kind_rules():
    number = Typed("a", "integer")
    decimal = Typed("b", "decimal")
    day = Typed("d", "date")
    stamp = Typed("t", "timestamp")
    text = Typed("s", "text")
    status = Typed("st", "enum", "status")
    other = Typed("ot", "enum", "level")
    flag = Typed("f", "boolean")
    assert cmp.kind_problem("lt", number, decimal) is None
    assert cmp.kind_problem("lt", day, stamp) is None
    assert cmp.kind_problem("eq", status, status) is None
    assert cmp.kind_problem("eq", text, text) is None
    assert cmp.kind_problem("eq", flag, flag) is None
    assert cmp.kind_problem("gt", stamp, cmp.AS_OF) is None
    for operator, left, right in [
        ("eq", number, day),
        ("lt", number, stamp),
        ("eq", status, other),
        ("eq", status, text),
        ("eq", flag, number),
        ("gt", day, text),
    ]:
        assert "only things of one kind" in (cmp.kind_problem(operator, left, right) or "")
    for left in (text, status, flag):
        message = cmp.kind_problem("gt", left, left)
        assert message and "compares by order" in message
    # The order rule is the left side's alone: a number cannot be compared with `as_of`.
    assert "only things of one kind" in (cmp.kind_problem("gt", number, cmp.AS_OF) or "")


def test_value_problems_are_the_kind_rules_of_a_literal():
    assert cmp.value_problem("integer", 3, type_name="int") is None
    assert "whole number" in (cmp.value_problem("integer", 2.5, type_name="int") or "")
    assert "must be a number" in (cmp.value_problem("decimal", "3", type_name="numeric") or "")
    assert cmp.value_problem("boolean", True, type_name="bool") is None
    assert cmp.value_problem("boolean", 1, type_name="bool")
    assert cmp.value_problem("date", "2026-01-01", type_name="date") is None
    assert cmp.value_problem("date", "2026-01-01 10:00:00", type_name="date")
    assert cmp.value_problem("timestamp", "2026-01-01 10:00:00", type_name="timestamp") is None
    assert cmp.value_problem("timestamp", "2026-01-01T10:00:00+01:00", type_name="timestamp")
    assert cmp.value_problem("text", 5, type_name="text")
    members = ["open", "1"]
    assert cmp.value_problem("enum", "open", type_name="status", members=members) is None
    assert cmp.value_problem("enum", 1, type_name="status", members=members) is None
    assert "not a member" in (
        cmp.value_problem("enum", "shut", type_name="status", members=members) or ""
    )


FRAME = pd.DataFrame(
    {
        "a": [1, 2, 3, None, 5],
        "b": [1, 3, 2, 4, None],
        "s": ["x", "y", "x", None, "z"],
        "t": ["x", "x", "y", "y", None],
        "d": [datetime(2026, 1, 1).date(), datetime(2026, 2, 1).date(), None, None, None],
        "ts": ["2026-01-01 10:00:00", "2026-03-01 00:00:00", None, None, None],
        "flag": [True, False, True, None, False],
    }
)
KINDS = {
    "a": "integer",
    "b": "integer",
    "s": "text",
    "t": "text",
    "d": "date",
    "ts": "timestamp",
    "flag": "boolean",
}


def _holds(column, operator, operand, as_of=None):
    return cmp.holds(FRAME, Comparison(column, operator, operand), KINDS, as_of=as_of).tolist()


def test_literals_and_lists():
    assert _holds("a", "eq", 2) == [False, True, False, False, False]
    assert _holds("a", "ne", 2) == [True, False, True, False, True]  # the null row is false
    assert _holds("a", "in", [1, 5]) == [True, False, False, False, True]
    assert _holds("a", "not_in", [1, 5]) == [False, True, True, False, False]
    assert _holds("a", "between", [2, 3]) == [False, True, True, False, False]
    assert _holds("a", "is_null", True) == [False, False, False, True, False]
    assert _holds("a", "is_null", False) == [True, True, True, False, True]
    assert _holds("s", "eq", "x") == [True, False, True, False, False]
    assert _holds("flag", "eq", True) == [True, False, True, False, False]


def test_a_column_operand_compares_row_by_row_and_a_null_on_either_side_is_false():
    assert _holds("a", "eq", {"column": "b"}) == [True, False, False, False, False]
    assert _holds("a", "ne", {"column": "b"}) == [False, True, True, False, False]
    assert _holds("a", "lt", {"column": "b"}) == [False, True, False, False, False]
    assert _holds("a", "gte", {"column": "b"}) == [True, False, True, False, False]
    assert _holds("s", "ne", {"column": "t"}) == [False, True, True, False, False]


def test_dates_timestamps_and_the_run_anchor():
    assert _holds("d", "gte", "2026-02-01") == [False, True, False, False, False]
    assert _holds("ts", "between", ["2026-01-01", "2026-02-01 00:00:00"]) == [
        True,
        False,
        False,
        False,
        False,
    ]
    anchor = datetime(2026, 2, 15)
    assert _holds("d", "lte", {"run": "as_of"}, as_of=anchor) == [True, True, False, False, False]
    assert _holds("ts", "lt", {"run": "as_of"}, as_of=anchor) == [True, False, False, False, False]
    # A date and a timestamp compare as moments.
    assert _holds("d", "lt", {"column": "ts"}) == [True, True, False, False, False]
    with pytest.raises(ValueError):
        _holds("d", "lte", {"run": "as_of"})


def test_null_operand_names_the_rows_a_rule_skips():
    mask = cmp.null_operand(FRAME, Comparison("a", "ne", {"column": "b"}), KINDS)
    assert mask.tolist() == [False, False, False, True, True]
    assert cmp.null_operand(FRAME, Comparison("a", "ne", 1), KINDS).tolist() == [
        False,
        False,
        False,
        True,
        False,
    ]


def test_all_hold_and_label():
    both = [Comparison("a", "gt", 1), Comparison("a", "lt", {"column": "b"})]
    assert cmp.all_hold(FRAME, both, KINDS).tolist() == [False, True, False, False, False]
    assert cmp.label(Comparison("a", "ne", {"column": "b"})) == "a ne {column: b}"
    assert cmp.label(Comparison("d", "lte", {"run": "as_of"})) == "d lte {run: as_of}"
    assert cmp.label(Comparison("a", "in", [1, 2])) == "a in [1, 2]"
