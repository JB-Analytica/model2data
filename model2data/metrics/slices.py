"""A run's metrics narrowed to dimension members, or broken down by a dimension.

Both are the same metrics over the same tables, so they cannot come to disagree
with the totals in `known_values`:

- `filtered` adds a condition to each metric's own filter and hands back a
  semantic model, which `known_values` then computes as usual: per month, and
  through ratios and derived metrics, without a second way of doing either.
- `breakdowns` runs a metric's own query with a `group by`. A ratio or derived
  metric is computed per member from its inputs per member, exactly as its total
  is from theirs.

A dimension applies to a metric when the metric's table reaches the dimension's
table along exactly one many-to-one path, or is that table. Anything else would
repeat the metric's rows or guess between paths, so `filtered` lists the metrics
it could not narrow, and a `Breakdown` says why it has no members. A ratio or
derived metric applies only if all of its inputs do.

The cap on members follows what can be added up: the members past `limit`
are summed as `other` for a sum or a count, and left out, with a `note`, for
anything else, since an average of the rest is not the rest of an average.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import Any, Optional

import pandas as pd

from model2data.metrics.columns import ColumnKind, column_kind, find_column
from model2data.metrics.graph import Graph
from model2data.metrics.semantic import ResolvedMetric, SemanticModel
from model2data.metrics.sql import columns_read, query
from model2data.metrics.types import Condition, Where
from model2data.metrics.values import Number, _empty, _evaluate, _exact, _rounded, _tables

DEFAULT_MEMBERS = 12


@dataclass
class Filtered:
    """`semantic` with its metrics narrowed; `unfiltered` names those a dimension did
    not apply to, whose values are not narrowed and should not be read as if they were."""

    semantic: SemanticModel
    unfiltered: tuple[str, ...] = ()


@dataclass
class Member:
    """One member of a dimension and the metric's value there. `member` is the column's
    text, None for rows where it is null."""

    member: Optional[str]
    value: Optional[Number]


@dataclass
class Breakdown:
    metric: str
    by: str
    members: list[Member] = field(default_factory=list)
    # The members past the cap, summed; None when there are none or they cannot be added.
    other: Optional[Number] = None
    # How many members are not in `members`, summed into `other` or left out.
    hidden: int = 0
    # Why there is less here than a person would expect, in words.
    note: Optional[str] = None


def _column(semantic: SemanticModel, path: str) -> tuple[str, ColumnKind]:
    table, column = find_column(semantic.model, path)
    if table is None or column is None:
        raise ValueError(f"no column {path!r} in the model")
    return table, column_kind(semantic.model, column)


def _reaching(semantic: SemanticModel, metric: ResolvedMetric, table: str) -> Optional[dict]:
    """`metric`'s joins with `table` among them, or None when it has no one way there."""
    assert metric.table is not None
    if table == metric.table or table in metric.joins:
        return metric.joins
    paths = Graph(semantic.model).paths(metric.table, table, limit=2)
    return {**metric.joins, table: paths[0]} if len(paths) == 1 else None


def _value(value: Any, kind: ColumnKind, path: str) -> Any:
    """A member as a value of the column's kind. Checked because it may come from a
    request, and a number or a date is written into the query without quotes."""
    try:
        if kind == "integer":
            return int(str(value))
        if kind == "decimal":
            number = Decimal(str(value))
            if not number.is_finite():
                raise ValueError(value)
            return number
        if kind == "date":
            return dt.date.fromisoformat(str(value)).isoformat()
        if kind == "timestamp":
            return dt.datetime.fromisoformat(str(value)).isoformat(sep=" ")
        if kind == "boolean":
            text = str(value).lower()
            if text not in ("true", "false"):
                raise ValueError(value)
            return text == "true"
    except (ValueError, InvalidOperation) as error:
        raise ValueError(f"{value!r} is not a value of {path} ({kind})") from error
    return str(value)


def _keeping(path: str, members: Sequence[Any], kind: ColumnKind) -> Where:
    """The filter for rows whose `path` is one of `members`, None among them meaning null."""
    values = [_value(member, kind, path) for member in members if member is not None]
    alternatives = [Where(terms=[Condition(path, {"in": values})])] if values else []
    if len(values) < len(members):
        alternatives.append(Where(terms=[Condition(path, {"is_null": True})]))
    return Where(terms=alternatives, any=True)


def filtered(semantic: SemanticModel, members: Mapping[str, Sequence[Any]]) -> Filtered:
    """Every metric narrowed to rows whose dimension is one of its members.

    `members` maps a column path to the values to keep, None for null. Dimensions
    together narrow as `and`. A dimension with no members keeps everything.
    """
    wanted = {path: kept for path, kept in members.items() if len(kept) > 0}
    terms = [
        (path.rpartition(".")[0], _keeping(path, kept, _column(semantic, path)[1]))
        for path, kept in wanted.items()
    ]
    metrics = dict(semantic.metrics)
    missed: set[str] = set()
    for name, metric in semantic.metrics.items():
        if metric.kind not in ("simple", "count"):
            continue
        joins = metric.joins
        for table, _ in terms:
            joins = _reaching(semantic, replace(metric, joins=joins), table)
            if joins is None:
                break
        if joins is None:
            missed.add(name)
            continue
        if terms:
            kept = metric.where.terms if metric.where is not None else []
            where = Where(terms=[*kept, *(term for _, term in terms)])
            metrics[name] = replace(metric, joins=joins, where=where)
    unfiltered = [name for name in semantic.metrics if missed & set(semantic.simple_inputs(name))]
    return Filtered(replace(semantic, metrics=metrics), tuple(unfiltered))


def _additive(metric: ResolvedMetric) -> bool:
    return metric.kind == "count" or metric.agg in ("sum", "count")


def _sort_key(member: Member) -> tuple:
    value = member.value
    return (value is None, -value if value is not None else 0, member.member is None, member.member)


def breakdowns(
    semantic: SemanticModel,
    frames: Mapping[str, pd.DataFrame],
    wanted: Sequence[tuple[str, str]],
    *,
    limit: int = DEFAULT_MEMBERS,
) -> list[Breakdown]:
    """Each `(metric, column path)` of `wanted`, its members largest first."""
    out: dict[tuple[str, str], Breakdown] = {}
    # What to query: a simple metric and dimension, with the joins that reach it.
    views: dict[tuple[str, str], ResolvedMetric] = {}
    for name, by in dict.fromkeys(wanted):
        if name not in semantic.metrics:
            raise ValueError(f"no metric {name!r}")
        table = _column(semantic, by)[0]
        inputs = semantic.simple_inputs(name)
        joins = {dep: _reaching(semantic, semantic.metrics[dep], table) for dep in inputs}
        if any(item is None for item in joins.values()):
            out[(name, by)] = Breakdown(
                name, by, note=f"{name} has no single many-to-one path to {table}"
            )
            continue
        out[(name, by)] = Breakdown(name, by)
        for dep in inputs:
            views[(dep, by)] = replace(semantic.metrics[dep], joins=joins[dep] or {})

    def seen(dep: str, by: str) -> SemanticModel:
        return replace(semantic, metrics={**semantic.metrics, dep: views[(dep, by)]})

    read: dict[str, set[str]] = {}
    for dep, by in views:
        for table, columns in columns_read(seen(dep, by), dep, by=by).items():
            read.setdefault(table, set()).update(columns)
    raw: dict[tuple[str, str], dict[Optional[str], Any]] = {}
    if views:
        with _tables(semantic, frames, read) as (connection, relation):
            for dep, by in views:
                sql = query(seen(dep, by), dep, relation, by=by)
                raw[(dep, by)] = dict(connection.execute(sql).fetchall())

    for breakdown in out.values():
        if breakdown.note is None:
            _fill(semantic, breakdown, raw, limit)
    return [out[key] for key in dict.fromkeys(wanted)]


def _fill(
    semantic: SemanticModel,
    breakdown: Breakdown,
    raw: dict[tuple[str, str], dict[Optional[str], Any]],
    limit: int,
) -> None:
    name, by = breakdown.metric, breakdown.by
    metric = semantic.metrics[name]
    inputs = semantic.simple_inputs(name)
    if metric.kind in ("simple", "count"):
        exact = _exact(metric)
        members = [
            Member(member, _rounded(value, exact)) for member, value in raw[(name, by)].items()
        ]
    else:
        names = list(dict.fromkeys(member for dep in inputs for member in raw[(dep, by)]))
        names.sort(key=lambda member: (member is None, member))
        members = []
        for member in names:
            picked = {
                dep: (
                    raw[(dep, by)][member]
                    if member in raw[(dep, by)]
                    else _empty(semantic.metrics[dep])
                )
                for dep in inputs
            }
            with localcontext(Context(prec=40)):
                members.append(Member(member, _rounded(_evaluate(semantic, name, picked), False)))
    members.sort(key=_sort_key)
    breakdown.members = members[:limit]
    rest = members[limit:]
    breakdown.hidden = len(rest)
    if not rest:
        return
    if _additive(metric):
        total = sum((item.value for item in rest if item.value is not None), Decimal(0))
        breakdown.other = _rounded(total, _exact(metric))
    else:
        breakdown.note = f"{len(rest)} smaller members left out: {name} cannot be added up"
