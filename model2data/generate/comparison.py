"""The comparison grammar: one parser, one set of checks, one vectorised evaluation.

A **comparison** is a column and a condition on it, `{<column>: {<operator>: <operand>}}`.
The metrics spec's `where` (a filter on a metric), a table's `rules` (what every
generated row must satisfy) and, in later spec versions, `derive` cases all write
it, and all read it through this module, so the operators, the operand forms and
the kind rules cannot drift apart between them.

- **Operators**: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between`,
  `is_null`. The order operators (`gt`, `gte`, `lt`, `lte`, `between`) compare
  numbers, dates and timestamps only.
- **Operands**: a literal, and, for `eq`, `ne` and the order operators other
  than `between`, one of two references:
  `{column: <name>}`, the value of that column in the same row, and
  `{run: as_of}`, the run's anchor timestamp (only where the caller allows it:
  the model's, not the metrics'). `in`, `not_in` and `between` take literal
  lists.
- **Nulls** behave as in SQL: a comparison with a null operand is false, and
  `is_null` is the null test.

`operand_problems` checks the shape of an operand, `kind_problem` and
`value_problem` check it against the kinds of its columns (the kinds are the
model spec's, `generate.kinds`), and `holds` evaluates a comparison over a
pandas frame. Nothing here reads a model: callers say what kind each column is.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, Optional

import pandas as pd

OPERATORS: tuple[str, ...] = (
    "eq",
    "ne",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
)
# The operators that compare by order, which a text, enum or boolean column has none of.
ORDER_OPERATORS: tuple[str, ...] = ("gt", "gte", "lt", "lte", "between")
# The operators whose operand is a list of literals, never a reference.
LIST_OPERATORS: tuple[str, ...] = ("in", "not_in", "between")
# The operators that take one value, which may be a reference.
VALUE_OPERATORS: tuple[str, ...] = ("eq", "ne", "gt", "gte", "lt", "lte")

ColumnKind = Literal["enum", "boolean", "integer", "decimal", "date", "timestamp", "text"]

RUN_REFERENCES: tuple[str, ...] = ("as_of",)


def show(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= 60 else text[:57] + "..."


def describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return f"the number {value!r}"
    if isinstance(value, str):
        return f"the string {json.dumps(value, ensure_ascii=False)}"
    if isinstance(value, Mapping):
        return "a mapping"
    if isinstance(value, list):
        return "a list"
    return repr(value)


def is_numeric(kind: str) -> bool:
    return kind in ("integer", "decimal")


def is_temporal(kind: str) -> bool:
    return kind in ("date", "timestamp")


# ---------------------------------------------------------------------------
# Operands
# ---------------------------------------------------------------------------
def column_operand(value: Any) -> Optional[str]:
    """The column a `{column: <name>}` operand names, or None when `value` is not one."""
    if isinstance(value, Mapping) and set(value) == {"column"} and isinstance(value["column"], str):
        return value["column"]
    return None


def run_operand(value: Any) -> Optional[str]:
    """The run value a `{run: <name>}` operand names (`as_of`), or None."""
    if isinstance(value, Mapping) and set(value) == {"run"} and isinstance(value["run"], str):
        return value["run"]
    return None


def is_reference(value: Any) -> bool:
    return column_operand(value) is not None or run_operand(value) is not None


def operand_columns(operator: str, operand: Any) -> list[str]:
    """The columns an operand reads besides the comparison's own: `[]` for a literal."""
    name = column_operand(operand) if operator in VALUE_OPERATORS else None
    return [name] if name is not None else []


@dataclass
class Problem:
    """What is wrong with an operand: `path` locates it within the operand (an index of
    a list, or nothing), `message` says what."""

    path: tuple
    message: str


def operand_problems(operator: str, operand: Any, *, allow_run: bool = False) -> list[Problem]:
    """The ways `operand` is not the shape `operator` takes; empty when it is."""
    if operator not in OPERATORS:
        return [
            Problem(
                (),
                f"unknown operator {operator!r}: use one of {', '.join(OPERATORS)}",
            )
        ]
    if operator == "is_null":
        if isinstance(operand, bool):
            return []
        return [Problem((), f"`is_null` takes true or false, not {describe(operand)}")]
    if operator in LIST_OPERATORS:
        if not isinstance(operand, list):
            return [
                Problem(
                    (),
                    f"`{operator}` takes a list of values, not {describe(operand)}"
                    + (" (`[low, high]`)" if operator == "between" else ""),
                )
            ]
        problems = []
        if operator == "between" and len(operand) != 2:
            problems.append(
                Problem((), f"`between` takes [low, high], two values, not {len(operand)}")
            )
        if not operand:
            problems.append(Problem((), f"`{operator}` takes at least one value"))
        for index, item in enumerate(operand):
            if isinstance(item, Mapping):
                problems.append(
                    Problem(
                        (index,),
                        f"a list of values holds literals only, not {describe(item)}: "
                        "`{column: ...}` and `{run: ...}` compare with one value",
                    )
                )
            elif not _is_literal(item):
                problems.append(Problem((index,), f"must be a value, not {describe(item)}"))
        return problems
    if isinstance(operand, Mapping):
        if column_operand(operand) is not None:
            return []
        if "column" in operand:
            return [
                Problem(
                    (),
                    "`{column: name}` takes the name of a column and nothing else, "
                    f"as in {{column: orders.total}} (got {show(operand)})",
                )
            ]
        if "run" in operand:
            name = run_operand(operand)
            if not allow_run:
                return [Problem((), "`{run: ...}` is for a model's rules; a metric has no run")]
            if name is None or name not in RUN_REFERENCES:
                return [
                    Problem(
                        (),
                        f"`run` names a value of the run, and the only one is `as_of` "
                        f"(got {show(operand)})",
                    )
                ]
            return []
        return [
            Problem(
                (),
                f"`{operator}` takes a value, `{{column: <name>}}`"
                + (" or `{run: as_of}`" if allow_run else "")
                + f", not {describe(operand)}",
            )
        ]
    if isinstance(operand, list):
        return [Problem((), f"`{operator}` takes one value, not a list: `in` takes a list")]
    if not _is_literal(operand):
        return [Problem((), f"`{operator}` takes a value, not {describe(operand)}")]
    return []


def _is_literal(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Typed:
    """A side of a comparison, with what the checks need of it: its name (for a message),
    its kind, and the enum it is typed as (the enum's own name), when it is one."""

    name: str
    kind: ColumnKind
    enum: Optional[str] = None

    @property
    def family(self) -> str:
        if self.kind == "enum":
            return f"enum {self.enum}"
        if is_numeric(self.kind):
            return "number"
        if is_temporal(self.kind):
            return "date"
        return self.kind

    @property
    def words(self) -> str:
        if self.kind == "enum":
            return f"an enum ({self.enum})"
        return {
            "integer": "a number",
            "decimal": "a number",
            "date": "a date",
            "timestamp": "a timestamp",
            "boolean": "a boolean",
            "text": "text",
        }[self.kind]


AS_OF = Typed("{run: as_of}", "timestamp")


def order_problem(operator: str, side: Typed) -> Optional[str]:
    """Why `operator` cannot compare `side`, when it is an order operator on a kind with none."""
    if operator in ORDER_OPERATORS and not (is_numeric(side.kind) or is_temporal(side.kind)):
        return (
            f"`{operator}` compares by order, and {side.name} is "
            f"{'an enum' if side.kind == 'enum' else side.kind}: list the values it may hold "
            "with `in` instead"
        )
    return None


def kind_problem(operator: str, left: Typed, right: Typed) -> Optional[str]:
    """Why `left <operator> right` compares two things that cannot be compared, else None.

    `right` is a column (or `{run: as_of}`) the operand names. An order operator
    needs numbers or dates on both sides, and the two sides must be of one
    family: numbers with numbers, dates and timestamps with each other, an enum
    with the same enum, text with text, a boolean with a boolean.
    """
    problem = order_problem(operator, left)
    if problem is not None:
        return problem
    if left.family != right.family:
        return (
            f"compares {left.name}, {left.words}, with {right.name}, {right.words}: "
            "only things of one kind can be compared"
        )
    return None


def value_problem(
    kind: ColumnKind, value: Any, *, type_name: str, members: Optional[Sequence[str]] = None
) -> Optional[str]:
    """Why `value` cannot be a value of a column of `kind` (typed `type_name`), else None.

    `members` are the members of the column's enum.
    """
    if kind == "enum":
        assert members is not None
        text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
        if isinstance(text, str) and text in members:
            return None
        listed = ", ".join(members)
        return f"{show(value)} is not a member of {type_name} ({listed})"
    if kind == "boolean":
        return None if isinstance(value, bool) else f"must be true or false, not {show(value)}"
    if is_numeric(kind):
        if not _number(value):
            return f"must be a number, as {type_name} is numeric, not {describe(value)}"
        if kind == "integer" and isinstance(value, float) and not value.is_integer():
            return f"must be a whole number, as {type_name} is an integer type (got {value!r})"
        return None
    if is_temporal(kind):
        if isinstance(value, str) and parse_temporal(kind, value) is not None:
            return None
        shape = "YYYY-MM-DD" if kind == "date" else "YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
        return f"must be an ISO 8601 {kind} as a string, {shape} (got {show(value)})"
    if not isinstance(value, str):
        return f"must be text, as {type_name} is a text column: quote it (got {show(value)})"
    return None


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_temporal(kind: str, value: str) -> Optional[datetime]:
    """`value` as an ISO 8601 date (or, for a timestamp column, timestamp), else None."""
    try:
        if kind == "date" or len(value) == 10:
            if len(value) != 10:
                return None
            day = date.fromisoformat(value)
            return datetime(day.year, day.month, day.day)
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def comparable(kind: str, value: Any) -> Any:
    """`value` as something a bound can be compared by, or None when it is not one."""
    if is_temporal(kind) and isinstance(value, str):
        return parse_temporal(kind, value)
    return value if _number(value) else None


# ---------------------------------------------------------------------------
# Evaluation over a frame
# ---------------------------------------------------------------------------
@dataclass
class Comparison:
    """One condition on one column of a row: `column <operator> operand`."""

    column: str
    operator: str
    operand: Any

    def columns(self) -> list[str]:
        """Every column the comparison reads: its own, then the one its operand names."""
        return [self.column, *operand_columns(self.operator, self.operand)]


def parse(mapping: Mapping) -> list[Comparison]:
    """A comparison mapping, `{<column>: <condition>}`, as comparisons, in order.

    A condition is a value (`eq`), a list (`in`), or a mapping of operators, every
    one of which must hold; `x-` keys are skipped. The shape is not checked here
    (see `operand_problems`): read a mapping that passed the checks.
    """
    out: list[Comparison] = []
    for column, condition in mapping.items():
        if not isinstance(column, str) or column.startswith("x-"):
            continue
        if isinstance(condition, Mapping):
            out += [
                Comparison(column, operator, operand)
                for operator, operand in condition.items()
                if not str(operator).startswith("x-")
            ]
        elif isinstance(condition, list):
            out.append(Comparison(column, "in", condition))
        else:
            out.append(Comparison(column, "eq", condition))
    return out


def label(comparison: Comparison) -> str:
    """The comparison as short text: `home_team_id ne {column: away_team_id}`."""
    operand = comparison.operand
    if (name := column_operand(operand)) is not None:
        text = f"{{column: {name}}}"
    elif (name := run_operand(operand)) is not None:
        text = f"{{run: {name}}}"
    else:
        text = json.dumps(operand, ensure_ascii=False, default=str)
    return f"{comparison.column} {comparison.operator} {text}"


def _series(frame: pd.DataFrame, column: str, kind: str) -> pd.Series:
    """A column as comparable values: numbers, timestamps, or the column's own."""
    values = frame[column]
    if is_numeric(kind):
        return pd.to_numeric(values, errors="coerce")
    if is_temporal(kind):
        return pd.to_datetime(values, errors="coerce")
    if kind == "enum":
        return values.map(lambda v: v if v is None or v != v else str(v))
    return values


def _literal(kind: str, value: Any) -> Any:
    if is_temporal(kind) and isinstance(value, str):
        return pd.Timestamp(parse_temporal(kind, value) or value)
    if kind == "enum":
        return str(value)
    return value


def null_operand(
    frame: pd.DataFrame, comparison: Comparison, kinds: Mapping[str, str]
) -> pd.Series:
    """The rows on which a column the comparison reads is null."""
    result = pd.Series(False, index=frame.index)
    for column in comparison.columns():
        result |= _series(frame, column, kinds[column]).isna()
    return result


def holds(
    frame: pd.DataFrame,
    comparison: Comparison,
    kinds: Mapping[str, str],
    *,
    as_of: Optional[datetime] = None,
) -> pd.Series:
    """Per row of `frame`: does the comparison hold? A null operand makes it false.

    `kinds` maps each column the comparison reads to its kind. `as_of` is the
    value of `{run: as_of}`.
    """
    kind = kinds[comparison.column]
    left = _series(frame, comparison.column, kind)
    operator, operand = comparison.operator, comparison.operand
    if operator == "is_null":
        return left.isna() == bool(operand)
    known = left.notna()
    if operator in ("in", "not_in"):
        members = [_literal(kind, item) for item in operand]
        inside = left.isin(members)
        return known & (inside if operator == "in" else ~inside)
    if operator == "between":
        low, high = (_literal(kind, item) for item in operand)
        return known & (left >= low) & (left <= high)
    if (name := column_operand(operand)) is not None:
        right = _series(frame, name, kinds[name])
        known = known & right.notna()
    elif run_operand(operand) is not None:
        if as_of is None:
            raise ValueError("`{run: as_of}` needs the run's as_of")
        right = pd.Timestamp(as_of)
    else:
        right = _literal(kind, operand)
    if operator == "eq":
        outcome = left == right
    elif operator == "ne":
        outcome = left != right
    elif operator == "gt":
        outcome = left > right
    elif operator == "gte":
        outcome = left >= right
    elif operator == "lt":
        outcome = left < right
    else:
        outcome = left <= right
    return known & outcome.fillna(False).astype(bool)


def all_hold(
    frame: pd.DataFrame,
    comparisons: Iterable[Comparison],
    kinds: Mapping[str, str],
    *,
    as_of: Optional[datetime] = None,
) -> pd.Series:
    """Per row of `frame`: does every comparison hold?"""
    result = pd.Series(True, index=frame.index)
    for comparison in comparisons:
        result &= holds(frame, comparison, kinds, as_of=as_of)
    return result
