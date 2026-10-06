"""A derived metric's `expression`: arithmetic over metric names and numbers.

This is the one place the metrics spec encodes something in a string, so it is
kept as small as a grammar can be and read with a parser of its own, never
`eval`:

    expression := term (("+" | "-") term)*
    term       := factor (("*" | "/") factor)*
    factor     := ("+" | "-") factor | NUMBER | NAME | "(" expression ")"
    NAME       := [a-z][a-z0-9_]*          a metric
    NUMBER     := [0-9]+ ("." [0-9]+)?

Whitespace between tokens is ignored. Operators keep the usual precedence and
associate to the left (`a - b - c` is `(a - b) - c`). Division by zero is
null, as a ratio's is, and so is any arithmetic with a null in it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

_TOKEN = re.compile(
    r"\s*(?:(?P<number>[0-9]+(?:\.[0-9]+)?)|(?P<name>[a-z][a-z0-9_]*)|(?P<op>[-+*/()]))"
)


class ExpressionError(ValueError):
    """An expression that does not parse; the message says where."""


@dataclass(frozen=True)
class Number:
    value: Decimal


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Negate:
    operand: Node


@dataclass(frozen=True)
class BinaryOp:
    op: str
    left: Node
    right: Node


Node = Union[Number, Name, Negate, BinaryOp]


def _tokens(text: str) -> list[tuple[str, str, int]]:
    """`(kind, text, position)` for each token; position is 1-based, for messages."""
    out: list[tuple[str, str, int]] = []
    position = 0
    while position < len(text):
        if text[position:].strip() == "":
            break
        match = _TOKEN.match(text, position)
        if match is None or match.end() == position:
            offset = len(text[position:]) - len(text[position:].lstrip())
            char = text[position + offset]
            raise ExpressionError(
                f"unexpected {char!r} at character {position + offset + 1}: an expression "
                "holds metric names, numbers, + - * / and parentheses"
            )
        kind = match.lastgroup or "op"
        out.append((kind, match.group(kind), match.start(kind) + 1))
        position = match.end()
    return out


class _Parser:
    def __init__(self, text: str):
        self.tokens = _tokens(text)
        self.index = 0

    def peek(self) -> Optional[tuple[str, str, int]]:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str, int]:
        token = self.tokens[self.index]
        self.index += 1
        return token

    def expression(self) -> Node:
        node = self.term()
        while (token := self.peek()) is not None and token[1] in ("+", "-"):
            self.take()
            node = BinaryOp(token[1], node, self.term())
        return node

    def term(self) -> Node:
        node = self.factor()
        while (token := self.peek()) is not None and token[1] in ("*", "/"):
            self.take()
            node = BinaryOp(token[1], node, self.factor())
        return node

    def factor(self) -> Node:
        token = self.peek()
        if token is None:
            raise ExpressionError("the expression ends where a metric name or number should be")
        kind, text, position = self.take()
        if text in ("+", "-"):
            operand = self.factor()
            return Negate(operand) if text == "-" else operand
        if kind == "number":
            return Number(Decimal(text))
        if kind == "name":
            return Name(text)
        if text == "(":
            node = self.expression()
            closing = self.peek()
            if closing is None or closing[1] != ")":
                raise ExpressionError(f"the parenthesis at character {position} is never closed")
            self.take()
            return node
        raise ExpressionError(
            f"unexpected {text!r} at character {position}: a metric name or number goes here"
        )


def parse(text: str) -> Node:
    """The expression's tree, or `ExpressionError` saying what is wrong and where."""
    parser = _Parser(text)
    if not parser.tokens:
        raise ExpressionError("the expression is empty")
    node = parser.expression()
    rest = parser.peek()
    if rest is not None:
        raise ExpressionError(
            f"unexpected {rest[1]!r} at character {rest[2]}: an operator goes between "
            "two metric names or numbers"
        )
    return node


def names(node: Node) -> list[str]:
    """The metric names an expression uses, each once, in the order they appear."""
    found: dict[str, None] = {}

    def walk(current: Node) -> None:
        if isinstance(current, Name):
            found[current.name] = None
        elif isinstance(current, Negate):
            walk(current.operand)
        elif isinstance(current, BinaryOp):
            walk(current.left)
            walk(current.right)

    walk(node)
    return list(found)


def evaluate(node: Node, values: Mapping[str, Optional[Decimal]]) -> Optional[Decimal]:
    """The expression's value given each metric's, with SQL's nulls: null in, null out,
    and division by zero is null."""
    if isinstance(node, Number):
        return node.value
    if isinstance(node, Name):
        return values[node.name]
    if isinstance(node, Negate):
        operand = evaluate(node.operand, values)
        return None if operand is None else -operand
    left = evaluate(node.left, values)
    right = evaluate(node.right, values)
    if left is None or right is None:
        return None
    if node.op == "+":
        return left + right
    if node.op == "-":
        return left - right
    if node.op == "*":
        return left * right
    if right == 0:
        return None
    try:
        return left / right
    except InvalidOperation:  # pragma: no cover - 0/0 is caught above
        return None


def to_sql(node: Node, render_name: Callable[[str], str]) -> str:
    """The expression as SQL, each metric name replaced by `render_name(name)`.

    Every operation is parenthesised, so the SQL means what the tree means
    whatever the reader's precedence rules, and a division divides by
    `nullif(..., 0)`, so dividing by zero is null rather than an error.
    """
    if isinstance(node, Number):
        return format(node.value, "f")
    if isinstance(node, Name):
        return render_name(node.name)
    if isinstance(node, Negate):
        return f"(-{to_sql(node.operand, render_name)})"
    left = to_sql(node.left, render_name)
    right = to_sql(node.right, render_name)
    if node.op == "/":
        return f"({left} / nullif({right}, 0))"
    return f"({left} {node.op} {right})"
