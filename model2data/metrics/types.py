"""A metrics file as typed values: what a `<stem>.metrics.yml` document says, read.

These are the file's own words, before they meet the model: a column is still
the `table.column` path the file wrote, and a ratio still names its inputs.
`model2data.metrics.semantic` resolves them against a model (which table a
filter joins through, which column dates a metric) and adds the metrics the
model implies on its own.

Like the model's types, every class is a plain, comparable dataclass, and the
`x-*` keys of the document, at any level, are kept in `extensions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

if TYPE_CHECKING:
    from model2data.model.errors import Issue

# The aggregations the model spec's `measure` names, in its order.
AGGREGATIONS: tuple[str, ...] = (
    "sum",
    "average",
    "min",
    "max",
    "median",
    "count",
    "count_distinct",
)
# The two that apply to a column of any kind; the rest need a numeric one.
ANY_KIND_AGGREGATIONS: tuple[str, ...] = ("count", "count_distinct")
FORMATS: tuple[str, ...] = ("number", "currency", "percent")
# Each operator of a condition, in the order a condition is written out.
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
# A metric's kind, by the one key that says it.
KIND_KEYS: dict[str, str] = {
    "measure": "simple",
    "count": "count",
    "ratio": "ratio",
    "expression": "derived",
}

MetricKind = Literal["simple", "count", "ratio", "derived"]
Value = Union[str, int, float, bool]


@dataclass
class Condition:
    """What one column must hold: every operator in `operators` must hold at once.

    A scalar in the file reads as `{eq: value}` and a list as `{in: [...]}`, so
    `operators` always maps an operator of `OPERATORS` to its operand: a value,
    a list of values (`in`, `not_in`, and `[low, high]` for `between`), or true
    or false for `is_null`.
    """

    column: str
    operators: dict[str, Any]
    # How the file wrote it: a lone value (`eq`), a list (`in`), or operators.
    written: Literal["value", "list", "operators"] = "operators"
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class Where:
    """A filter: every term must hold (or, with `any`, one of them).

    A term is a `Condition`, or a group: the `any` or `all` key of the file, a
    `Where` whose terms are the filters listed under it, each a `Where` itself.
    `any=True` on the group means one of them must hold, `any=False` (`all`)
    that every one must. Terms are in document order.
    """

    terms: list[Union[Condition, Where]] = field(default_factory=list)
    any: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)

    def conditions(self) -> list[Condition]:
        """Every condition in the filter, nested ones included, in document order."""
        found: list[Condition] = []
        for term in self.terms:
            found.extend(term.conditions() if isinstance(term, Where) else [term])
        return found


@dataclass
class Ratio:
    numerator: str
    denominator: str
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class Metric:
    """One metric. `kind` says which of `measure`, `count`, `ratio` and `expression` is set.

    - `simple`: `measure` is a column path, aggregated by `agg`, or, without
      it, by the column's own `measure` in the model.
    - `count`: `count` is a table key; the metric counts its rows.
    - `ratio`: the numerator metric's total over the denominator's.
    - `derived`: `expression` is arithmetic over metric names and numbers.

    `time` and `where` are for the first two only: a ratio or a derived metric
    is filtered and dated through its inputs. `inferred` is True for a metric
    the model implies (a `measure` column) rather than one the file wrote.
    """

    kind: MetricKind
    measure: Optional[str] = None
    agg: Optional[str] = None
    count: Optional[str] = None
    ratio: Optional[Ratio] = None
    expression: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    ai_context: Optional[str] = None
    time: Optional[str] = None
    where: Optional[Where] = None
    inferred: bool = False
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class DimensionOverride:
    """`dimensions:` for one column: offered (with a label or description) or never."""

    offered: bool = True
    label: Optional[str] = None
    description: Optional[str] = None
    extensions: dict[str, Any] = field(default_factory=dict)


@dataclass
class Metrics:
    """A metrics file: the model it is for, its metrics and dimension overrides.

    `infer` (default True) adds a metric for every `measure` column of the
    model; a metric of the same name here replaces the inferred one.
    """

    model: str
    metrics: dict[str, Metric] = field(default_factory=dict)
    dimensions: dict[str, DimensionOverride] = field(default_factory=dict)
    infer: bool = True
    version: str = "0.1.0"
    extensions: dict[str, Any] = field(default_factory=dict)
    # What the reader pointed out in a file that conforms. Not part of the value.
    warnings: list[Issue] = field(default_factory=list, compare=False, repr=False)
