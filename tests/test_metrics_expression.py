"""A derived metric's expression: the grammar, the arithmetic, the SQL (metrics spec, "Metrics")."""

from decimal import Decimal

import pytest

from model2data.metrics.expression import (
    BinaryOp,
    ExpressionError,
    Name,
    Negate,
    Number,
    evaluate,
    names,
    parse,
    to_sql,
)


def test_precedence_and_left_association():
    assert parse("a - b - c") == BinaryOp("-", BinaryOp("-", Name("a"), Name("b")), Name("c"))
    assert parse("a + b * c") == BinaryOp("+", Name("a"), BinaryOp("*", Name("b"), Name("c")))
    assert parse("(a + b) / 2.5") == BinaryOp(
        "/", BinaryOp("+", Name("a"), Name("b")), Number(Decimal("2.5"))
    )


def test_unary_signs():
    assert parse("-a") == Negate(Name("a"))
    assert parse("+a") == Name("a")
    assert parse("--a") == Negate(Negate(Name("a")))
    assert parse("a * -2") == BinaryOp("*", Name("a"), Negate(Number(Decimal(2))))


def test_whitespace_is_ignored():
    assert parse("  a\t+\nb  ") == parse("a+b")


@pytest.mark.parametrize(
    "text, message",
    [
        ("", "the expression is empty"),
        ("a +", "ends where a metric name or number should be"),
        ("a b", "unexpected 'b' at character 3"),
        ("a)", "unexpected ')' at character 2"),
        ("(a", "the parenthesis at character 1 is never closed"),
        ("a * )", "unexpected ')' at character 5: a metric name or number goes here"),
        ("Revenue", "unexpected 'R' at character 1"),
        ("a % b", "unexpected '%' at character 3"),
        ("1.", "unexpected '.' at character 2"),
        ("a.b", "unexpected '.' at character 2"),
    ],
)
def test_what_does_not_parse(text, message):
    with pytest.raises(ExpressionError) as raised:
        parse(text)
    assert message in str(raised.value)


def test_names_in_order_once_each():
    assert names(parse("b / (a + b) - -c * 2")) == ["b", "a", "c"]


def test_evaluate_in_decimals_with_sql_nulls():
    node = parse("(a - b) / c * 100 + -1")
    values = {"a": Decimal(30), "b": Decimal(10), "c": Decimal(40)}
    assert evaluate(node, values) == Decimal(49)
    assert evaluate(node, {**values, "c": Decimal(0)}) is None
    assert evaluate(node, {**values, "a": None}) is None
    assert evaluate(parse("-a"), {"a": None}) is None
    assert evaluate(parse("a / 3"), {"a": Decimal(1)}) == Decimal(1) / Decimal(3)


def test_to_sql_parenthesises_and_never_divides_by_zero():
    sql = to_sql(parse("a - b / c * 2.50 + -d"), lambda name: f"[{name}]")
    assert sql == "(([a] - (([b] / nullif([c], 0)) * 2.50)) + (-[d]))"
