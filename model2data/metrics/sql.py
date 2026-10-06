"""A resolved metric as SQL: one way of writing it for every place it is computed.

Two dialects of the same thing:

- **warehouse SQL** (`query`, `scalar`), which the known values run on DuckDB
  and the generated dbt tests run on DuckDB or Postgres. It reads relations a
  caller names (a DuckDB table, a dbt `ref()`), joins the tables a metric's
  filter and time are on along their many-to-one path with `left join` (a row
  whose parent is missing stays, and fails any filter on the parent, as a null
  does), and casts every column it reads to the type its kind is read as
  (`model2data.metrics.columns.SQL_TYPES`), so the answer does not depend on
  how a CSV loader guessed the column's type.
- **ANSI SQL for Ossie** (`ansi`), over `dataset.field` names, with no joins:
  an Ossie consumer joins along the relationships the document declares.

A filter on a simple metric goes inside its aggregate, `sum(case when <filter>
then <column> end)`, so rows that fail it count as nulls: `sum`, `avg` and
the rest skip them, and an aggregate over no rows at all is null (a count is
0). Conditions follow SQL's three-valued logic, which is exactly the spec's
null rule: a null fails every condition but `is_null: true`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from model2data.metrics import expression as expr
from model2data.metrics.columns import SQL_TYPES, ColumnKind, column_kind, find_column
from model2data.metrics.semantic import ResolvedMetric, SemanticModel
from model2data.metrics.types import Condition, Where

Relation = Callable[[str], str]

_BARE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote(name: str) -> str:
    """An identifier, double-quoted the ANSI way (a `"` inside doubled)."""
    return '"' + name.replace('"', '""') + '"'


def identifier(name: str) -> str:
    """A name as an SQL identifier: bare when it can be, else double-quoted."""
    return name if _BARE.match(name) else quote(name)


def _text(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def literal(value: Any, kind: ColumnKind, upper: bool = False) -> str:
    """A filter value as an SQL literal of the column's kind."""
    if isinstance(value, bool):
        text = "true" if value else "false"
        return text.upper() if upper else text
    if kind in ("date", "timestamp"):
        word = "date" if kind == "date" else "timestamp"
        return f"{word.upper() if upper else word} {_text(str(value))}"
    if kind in ("integer", "decimal"):
        if isinstance(value, float):
            return format(Decimal(repr(value)), "f")
        return str(value)
    return _text(str(value))


def _kw(word: str, upper: bool) -> str:
    return word.upper() if upper else word


def condition_sql(condition: Condition, column: str, kind: ColumnKind, upper: bool = False) -> str:
    """Every operator of `condition` on `column` (SQL text), all of them holding."""
    k = lambda word: _kw(word, upper)  # noqa: E731
    lit = lambda value: literal(value, kind, upper)  # noqa: E731
    parts = []
    for operator, operand in condition.operators.items():
        if operator == "eq":
            parts.append(f"{column} = {lit(operand)}")
        elif operator == "ne":
            parts.append(f"{column} <> {lit(operand)}")
        elif operator in ("in", "not_in"):
            values = ", ".join(lit(value) for value in operand)
            word = k("in") if operator == "in" else f"{k('not')} {k('in')}"
            parts.append(f"{column} {word} ({values})")
        elif operator in ("gt", "gte", "lt", "lte"):
            sign = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
            parts.append(f"{column} {sign} {lit(operand)}")
        elif operator == "between":
            parts.append(f"{column} {k('between')} {lit(operand[0])} {k('and')} {lit(operand[1])}")
        else:  # is_null
            parts.append(
                f"{column} {k('is')} {k('null') if operand else k('not') + ' ' + k('null')}"
            )
    return parts[0] if len(parts) == 1 else "(" + f" {k('and')} ".join(parts) + ")"


ColumnOf = Callable[[str], tuple[str, ColumnKind]]


def where_sql(where: Where, column_of: ColumnOf, upper: bool = False) -> str:
    """A filter as one SQL condition. `column_of(path)` is `(SQL for the column, its kind)`."""
    parts = []
    for term in where.terms:
        if isinstance(term, Where):
            joiner = f" {_kw('or' if term.any else 'and', upper)} "
            alternatives = [
                where_sql(alt, column_of, upper) for alt in term.terms if isinstance(alt, Where)
            ]
            parts.append(
                alternatives[0] if len(alternatives) == 1 else "(" + joiner.join(alternatives) + ")"
            )
        else:
            column, kind = column_of(term.column)
            parts.append(condition_sql(term, column, kind, upper))
    return parts[0] if len(parts) == 1 else "(" + f" {_kw('and', upper)} ".join(parts) + ")"


def aggregate_sql(
    metric: ResolvedMetric, value: str | None, condition: str | None, upper: bool = False
) -> str:
    """The aggregate of a simple metric (`value` is its column's SQL) or a row count
    (`value` None), with `condition` inside it when it has a filter."""
    k = lambda word: _kw(word, upper)  # noqa: E731
    if metric.kind == "count":
        if condition is None:
            return f"{k('count')}(*)"
        return f"{k('count')}({k('case')} {k('when')} {condition} {k('then')} 1 {k('end')})"
    assert value is not None and metric.agg is not None
    inner = (
        value
        if condition is None
        else (f"{k('case')} {k('when')} {condition} {k('then')} {value} {k('end')}")
    )
    if metric.agg == "sum":
        return f"{k('sum')}({inner})"
    if metric.agg == "average":
        return f"{k('avg')}({inner})"
    if metric.agg in ("min", "max"):
        return f"{k(metric.agg)}({inner})"
    if metric.agg == "median":
        return f"{k('percentile_cont')}(0.5) {k('within')} {k('group')} ({k('order')} {k('by')} {inner})"
    if metric.agg == "count":
        return f"{k('count')}({inner})"
    return f"{k('count')}({k('distinct')} {inner})"


# ---------------------------------------------------------------------------
# Warehouse SQL
# ---------------------------------------------------------------------------
def _cast(alias: str, column: str, kind: ColumnKind) -> str:
    return f"cast({alias}.{quote(column)} as {SQL_TYPES[kind]})"


class _From:
    """The `from` and `join` clauses of a simple metric, and the alias of each table in them."""

    def __init__(self, metric: ResolvedMetric, relation: Relation):
        assert metric.table is not None
        self.lines = [f"from {relation(metric.table)} as t0"]
        self.alias = {metric.table: "t0"}
        by_prefix: dict[tuple, str] = {(): "t0"}
        for table, path in metric.joins.items():
            for length in range(1, len(path) + 1):
                prefix = path[:length]
                if prefix in by_prefix:
                    continue
                alias = f"t{len(by_prefix)}"
                previous = by_prefix[path[: length - 1]]
                step = path[length - 1]
                on = " and ".join(
                    f"cast({previous}.{quote(child)} as varchar) = cast({alias}.{quote(parent)} as varchar)"
                    for child, parent in zip(step.child_columns, step.parent_columns, strict=True)
                )
                self.lines.append(f"left join {relation(step.parent)} as {alias} on {on}")
                by_prefix[prefix] = alias
            self.alias[table] = by_prefix[path]


def _parts(
    semantic: SemanticModel, metric: ResolvedMetric, relation: Relation
) -> tuple[str, _From]:
    clauses = _From(metric, relation)

    def column_of(path: str) -> tuple[str, ColumnKind]:
        table, column = find_column(semantic.model, path)
        assert table is not None and column is not None
        kind = column_kind(semantic.model, column)
        return _cast(clauses.alias[table], path.rpartition(".")[2], kind), kind

    condition = where_sql(metric.where, column_of) if metric.where is not None else None
    value = None
    if metric.kind == "simple":
        assert metric.column is not None and metric.column_kind is not None
        value = _cast("t0", metric.column, metric.column_kind)
    return aggregate_sql(metric, value, condition), clauses


def query(semantic: SemanticModel, name: str, relation: Relation, *, by_month: bool = False) -> str:
    """A simple metric or row count as a query: one row with its `value`, or, `by_month`, a
    row per calendar month of its time column (`month` the month's first day)."""
    metric = semantic.metrics[name]
    aggregate, clauses = _parts(semantic, metric, relation)
    joins = "\n".join(clauses.lines)
    if not by_month:
        return f"select {aggregate} as value\n{joins}"
    assert metric.time is not None
    table, column = metric.time.rpartition(".")[0], metric.time.rpartition(".")[2]
    month = f"cast(date_trunc('month', cast({clauses.alias[table]}.{quote(column)} as timestamp)) as date)"
    return f"select {month} as month, {aggregate} as value\n{joins}\ngroup by 1\norder by 1"


def scalar(semantic: SemanticModel, name: str, relation: Relation) -> str:
    """Any metric as one scalar SQL expression: a simple one as a subquery, a ratio or a
    derived one composed from its inputs' subqueries."""
    metric = semantic.metrics[name]
    if metric.kind in ("simple", "count"):
        indented = query(semantic, name, relation).replace("\n", "\n  ")
        return f"(\n  {indented}\n)"
    if metric.kind == "ratio":
        assert metric.numerator is not None and metric.denominator is not None
        numerator = scalar(semantic, metric.numerator, relation)
        denominator = scalar(semantic, metric.denominator, relation)
        return f"({numerator} / nullif({denominator}, 0))"
    assert metric.node is not None
    return expr.to_sql(metric.node, lambda dep: scalar(semantic, dep, relation))


# ---------------------------------------------------------------------------
# ANSI SQL over dataset.field, for Ossie
# ---------------------------------------------------------------------------
def field_ref(semantic: SemanticModel, path: str) -> str:
    """`orders.total_amount` as Ossie names it: the dataset (the table's dbt name), a dot,
    the field (the column), each quoted only when it has to be."""
    table, column = path.rpartition(".")[0], path.rpartition(".")[2]
    return f"{identifier(semantic.entities[table].name)}.{identifier(column)}"


def ansi(semantic: SemanticModel, name: str) -> str:
    """A metric as one ANSI SQL aggregate expression over `dataset.field` names, its inputs
    written out in full when it is a ratio or derived."""
    metric = semantic.metrics[name]
    if metric.kind == "ratio":
        assert metric.numerator is not None and metric.denominator is not None
        return f"({ansi(semantic, metric.numerator)}) / NULLIF({ansi(semantic, metric.denominator)}, 0)"
    if metric.kind == "derived":
        assert metric.node is not None
        text = expr.to_sql(metric.node, lambda dep: ansi(semantic, dep))
        return text.replace("nullif(", "NULLIF(")

    def column_of(path: str) -> tuple[str, ColumnKind]:
        _, column = find_column(semantic.model, path)
        assert column is not None
        return field_ref(semantic, path), column_kind(semantic.model, column)

    condition = where_sql(metric.where, column_of, upper=True) if metric.where is not None else None
    value = None
    if metric.kind == "simple":
        assert metric.table is not None and metric.column is not None
        value = field_ref(semantic, f"{metric.table}.{metric.column}")
    elif metric.table is not None:
        # COUNT(*) names no dataset, so a reader could not tell whose rows it counts:
        # a one-column primary key, never null, counts the same rows and says whose.
        entity = semantic.entities[metric.table]
        if len(entity.primary_key) == 1:
            key = field_ref(semantic, f"{metric.table}.{entity.primary_key[0]}")
            inner = key if condition is None else f"CASE WHEN {condition} THEN {key} END"
            return f"COUNT({inner})"
    return aggregate_sql(metric, value, condition, upper=True)
