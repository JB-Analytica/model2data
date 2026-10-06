"""Does a metrics file conform: the schema, the checks within the file, then against its model.

`check_document(document)` is what a reader can say from the file alone: the
schema, the version, each metric having exactly one kind, every `expression`
parsing, and no metric depending on itself. `check_against(metrics, model)`
is the rest, which needs the model: every table and column named exists, every
aggregation fits its column, every filter value fits its column and every
filter and time column is reachable along exactly one path. Both return every
issue they find, each with the document path of the value at fault, like the
model's own checks (`model2data.model.validate`), whose schema messages they
reuse.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from datetime import date, datetime
from functools import lru_cache
from importlib import resources
from typing import Any, Optional

import jsonschema
from jsonschema.exceptions import ValidationError

from model2data.metrics import expression as expr
from model2data.metrics.columns import (
    ColumnKind,
    column_kind,
    find_column,
    is_numeric,
    is_temporal,
)
from model2data.metrics.graph import Graph, describe
from model2data.metrics.infer import aggregation_of, inferred_metrics
from model2data.metrics.types import (
    ANY_KIND_AGGREGATIONS,
    KIND_KEYS,
    ORDER_OPERATORS,
    Condition,
    Metric,
    Metrics,
    Where,
)
from model2data.model.errors import Issue, PathPart, format_path
from model2data.model.types import Column, Model
from model2data.model.validate import _describe, _schema_issues, _show

SPEC_VERSION = "0.1.0"
# The minor versions this reader implements.
READS = ("0.1",)
SCHEMA_URL = (
    f"https://www.jbanalytica.com/model2data/spec/metrics/{SPEC_VERSION}/metrics.schema.json"
)

_PATTERN_WORDS = {
    "metricName": "must be a metric name: a lower-case letter, then lower-case letters, "
    "digits and underscores",
    "columnPath": "must name a column as `table.column` or `schema.table.column`",
    "tableKey": "must name a table as `table` or `schema.table`",
}


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    """The packaged, normative JSON Schema of the metrics spec."""
    text = (
        resources.files("model2data")
        .joinpath("spec/metrics/metrics.schema.json")
        .read_text("utf-8")
    )
    return json.loads(text)


@lru_cache(maxsize=1)
def _validator() -> Any:
    return jsonschema.Draft202012Validator(schema())


def _mapping(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


# ---------------------------------------------------------------------------
# The file alone
# ---------------------------------------------------------------------------
def check_document(document: Any) -> list[Issue]:
    """Every way `document` (a parsed YAML or JSON value) fails to conform on its own."""
    if not isinstance(document, Mapping):
        return [
            Issue("", f"a metrics file is a mapping at the top level, not {_describe(document)}")
        ]
    issues = _version_issues(document)
    for error in _validator().iter_errors(document):
        path = list(error.absolute_path)
        if error.validator == "oneOf" and len(path) == 2 and path[0] == "metrics":
            continue  # said better by _kind_issues
        issues.extend(_schema_error(error))
    metrics = _mapping(document.get("metrics"))
    for name, metric in metrics.items():
        if isinstance(metric, Mapping):
            issues.extend(_kind_issues(str(name), metric))
            issues.extend(_expression_issues(str(name), metric))
    issues.extend(_cycle_issues(metrics))
    return list(dict.fromkeys(issues))


def _schema_error(error: ValidationError) -> list[Issue]:
    if error.validator == "pattern":
        for name, words in _PATTERN_WORDS.items():
            if schema()["$defs"][name]["pattern"] == error.validator_value:
                where = format_path(error.absolute_path)
                if error.relative_schema_path and "propertyNames" in error.relative_schema_path:
                    return [Issue(where, f"{_show(error.instance)} {words}")]
                return [Issue(where, f"{words} (got {_show(error.instance)})")]
    return _schema_issues(error)


def _version_issues(document: Mapping) -> list[Issue]:
    version = document.get("model2data-metrics")
    if not isinstance(version, str):
        return []  # missing, or not a string: the schema says so
    parts = version.split(".")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        if ".".join(parts[:2]) in READS:
            return []
        return [
            Issue(
                "model2data-metrics",
                f"the file is written against metrics spec {version}, and this reader "
                f"implements metrics spec {SPEC_VERSION} (0.1.x). Upgrade model2data to read it.",
            )
        ]
    return []


def _kind_issues(name: str, metric: Mapping) -> list[Issue]:
    where = f"metrics.{name}"
    given = [key for key in KIND_KEYS if key in metric]
    if not given:
        return [
            Issue(
                where,
                "a metric needs one of `measure` (a column), `count` (a table's rows), "
                "`ratio` or `expression`",
            )
        ]
    if len(given) > 1:
        both = " and ".join(f"`{key}`" for key in given)
        return [Issue(where, f"a metric is of one kind, but {both} are both given: keep one")]
    kind = KIND_KEYS[given[0]]
    issues = []
    if "agg" in metric and kind != "simple":
        issues.append(
            Issue(f"{where}.agg", "`agg` says how a `measure` aggregates; this metric has none")
        )
    if kind in ("ratio", "derived"):
        for key in ("time", "where"):
            if key in metric:
                issues.append(
                    Issue(
                        f"{where}.{key}",
                        f"`{key}` is for a metric with `measure` or `count`: a "
                        f"{'ratio' if kind == 'ratio' else 'derived metric'} is "
                        f"{'dated' if key == 'time' else 'filtered'} through the metrics it uses",
                    )
                )
    return issues


def _expression_issues(name: str, metric: Mapping) -> list[Issue]:
    text = metric.get("expression")
    if not isinstance(text, str) or not text:
        return []
    try:
        node = expr.parse(text)
    except expr.ExpressionError as error:
        return [Issue(f"metrics.{name}.expression", str(error))]
    if not expr.names(node):
        return [
            Issue(
                f"metrics.{name}.expression",
                "the expression uses no metric: a derived metric is arithmetic over metrics",
            )
        ]
    return []


def dependencies(metric: Mapping) -> list[str]:
    """The metrics a metric (as written in the file) is computed from."""
    ratio = metric.get("ratio")
    if isinstance(ratio, Mapping):
        return [
            value
            for value in (ratio.get("numerator"), ratio.get("denominator"))
            if isinstance(value, str)
        ]
    text = metric.get("expression")
    if isinstance(text, str):
        try:
            return expr.names(expr.parse(text))
        except expr.ExpressionError:
            return []
    return []


def _cycle_issues(metrics: Mapping) -> list[Issue]:
    """A metric that depends on itself, directly or through others: one issue per cycle."""
    graph = {
        str(name): [dep for dep in dependencies(metric) if dep in metrics]
        for name, metric in metrics.items()
        if isinstance(metric, Mapping)
    }
    issues: list[Issue] = []
    reported: set[frozenset[str]] = set()
    state: dict[str, int] = {}

    def visit(name: str, stack: list[str]) -> None:
        state[name] = 1
        for dep in graph.get(name, []):
            if state.get(dep) == 1:
                cycle = [*stack[stack.index(dep) :], dep]
                members = frozenset(cycle)
                if members not in reported:
                    reported.add(members)
                    issues.append(
                        Issue(
                            f"metrics.{cycle[0]}",
                            "metrics depend on each other in a cycle: " + " -> ".join(cycle),
                        )
                    )
            elif state.get(dep) is None:
                visit(dep, [*stack, dep])
        state[name] = 2

    for name in graph:
        if state.get(name) is None:
            visit(name, [name])
    return issues


# ---------------------------------------------------------------------------
# Against the model
# ---------------------------------------------------------------------------
def all_metrics(metrics: Metrics, model: Optional[Model]) -> dict[str, Metric]:
    """The file's metrics and, with `infer`, the model's: explicit ones first, in file
    order, then the inferred ones they do not replace, in model order."""
    out = dict(metrics.metrics)
    if metrics.infer and model is not None:
        for name, metric in inferred_metrics(model).items():
            out.setdefault(name, metric)
    return out


def check_references(metrics: Metrics, known: Mapping[str, Metric]) -> list[Issue]:
    """Every ratio and expression names metrics that exist."""
    issues = []
    for name, metric in metrics.metrics.items():
        if metric.ratio is not None:
            for part in ("numerator", "denominator"):
                target = getattr(metric.ratio, part)
                if target not in known:
                    issues.append(
                        Issue(f"metrics.{name}.ratio.{part}", _unknown_metric(target, known))
                    )
        elif metric.expression is not None:
            for target in expr.names(expr.parse(metric.expression)):
                if target not in known:
                    issues.append(
                        Issue(f"metrics.{name}.expression", _unknown_metric(target, known))
                    )
    return issues


def _unknown_metric(name: str, known: Mapping[str, Metric]) -> str:
    return f"there is no metric {name!r}{_did_you_mean(name, list(known))}"


def _did_you_mean(name: str, options: list[str]) -> str:
    close = difflib.get_close_matches(name, options, n=1)
    return f" (did you mean {close[0]!r}?)" if close else ""


def check_against(
    metrics: Metrics, model: Model, *, model_name: Optional[str] = None
) -> list[Issue]:
    """Every way `metrics` does not fit `model`: errors and warnings.

    `model_name` is the name the model goes by when it has no `name` of its own
    (the CLI passes the file's stem).
    """
    return _ModelChecks(metrics, model, model_name).run()


class _ModelChecks:
    def __init__(self, metrics: Metrics, model: Model, model_name: Optional[str]):
        self.metrics = metrics
        self.model = model
        self.model_name = model.name or model_name
        self.graph = Graph(model)
        self.issues: list[Issue] = []

    def error(self, path: list[PathPart], message: str) -> None:
        self.issues.append(Issue(format_path(path), message))

    def warn(self, path: list[PathPart], message: str) -> None:
        self.issues.append(Issue(format_path(path), message, severity="warning"))

    def run(self) -> list[Issue]:
        if self.model_name is not None and self.metrics.model != self.model_name:
            self.error(
                ["model"],
                f"the metrics are for model {self.metrics.model!r}, and the model is "
                f"{self.model_name!r}",
            )
        known = all_metrics(self.metrics, self.model)
        self.issues.extend(check_references(self.metrics, known))
        for name, metric in self.metrics.metrics.items():
            self._metric(name, metric)
        for column in self.metrics.dimensions:
            self._column(["dimensions", column], column)
        return self.issues

    # -- tables and columns --------------------------------------------------
    def _table(self, path: list[PathPart], key: str) -> bool:
        if key in self.model.tables:
            return True
        self.error(
            path, f"the model has no table {key!r}{_did_you_mean(key, list(self.model.tables))}"
        )
        return False

    def _column(self, path: list[PathPart], column_path: str) -> Optional[Column]:
        table_key, column = find_column(self.model, column_path)
        if table_key is None:
            key = column_path.rpartition(".")[0]
            self.error(
                path,
                f"the model has no table {key!r}{_did_you_mean(key, list(self.model.tables))}",
            )
            return None
        if column is None:
            name = column_path.rpartition(".")[2]
            options = list(self.model.tables[table_key].columns)
            self.error(
                path, f"table {table_key!r} has no column {name!r}{_did_you_mean(name, options)}"
            )
            return None
        return column

    def _reach(self, path: list[PathPart], start: str, column_path: str, what: str) -> None:
        target = column_path.rpartition(".")[0]
        paths = self.graph.paths(start, target)
        if not paths:
            self.error(
                path,
                f"{what} {column_path} cannot be reached from {start}: a column has to be on "
                f"the metric's table, or on one its foreign keys lead to (many-to-one)",
            )
        elif len(paths) > 1:
            self.error(
                path,
                f"{what} {column_path} is reached from {start} along two paths, so which "
                f"{target} it means is unclear: {describe(paths[0])}; or {describe(paths[1])}",
            )

    # -- one metric ------------------------------------------------------------
    def _metric(self, name: str, metric: Metric) -> None:
        base = ["metrics", name]
        table: Optional[str] = None
        if metric.kind == "simple" and metric.measure is not None:
            column = self._column([*base, "measure"], metric.measure)
            if column is not None:
                table = metric.measure.rpartition(".")[0]
                self._aggregation(base, metric, column)
        elif metric.kind == "count" and metric.count is not None:
            if self._table([*base, "count"], metric.count):
                table = metric.count
        if table is None:
            return
        if metric.time is not None:
            column = self._column([*base, "time"], metric.time)
            if column is not None:
                if not is_temporal(column_kind(self.model, column)):
                    self.error(
                        [*base, "time"],
                        f"{metric.time} is {column.type}, not a date or timestamp column",
                    )
                else:
                    self._reach([*base, "time"], table, metric.time, "time column")
        elif self.graph.first_time_column(table) is None:
            self.warn(
                base,
                f"no date or timestamp column on {table}, or on a table it reaches, dates this "
                "metric: it has a total but no value per month. Set `time` to date it",
            )
        if metric.where is not None:
            self._where([*base, "where"], table, metric.where)

    def _aggregation(self, base: list[PathPart], metric: Metric, column: Column) -> None:
        assert metric.measure is not None
        kind = column_kind(self.model, column)
        if metric.agg is None:
            implied = aggregation_of(column.measure)
            if implied is None:
                self.error(
                    [*base, "measure"],
                    f"{metric.measure} has no `measure` in the model, so say how it "
                    "aggregates with `agg` (sum, average, min, max, median, count, "
                    "count_distinct)",
                )
            return
        if metric.agg not in ANY_KIND_AGGREGATIONS and not is_numeric(kind):
            self.error(
                [*base, "agg"],
                f"{metric.agg} needs a numeric column, and {metric.measure} is "
                f"{column.type}: count and count_distinct apply to any column",
            )

    # -- filters ----------------------------------------------------------------
    def _where(self, path: list[PathPart], table: str, where: Where) -> None:
        for term in where.terms:
            if isinstance(term, Where):
                group = "any" if term.any else "all"
                for index, alternative in enumerate(term.terms):
                    assert isinstance(alternative, Where)
                    self._where([*path, group, index], table, alternative)
            else:
                self._condition(path, table, term)

    def _condition(self, path: list[PathPart], table: str, condition: Condition) -> None:
        where = [*path, condition.column]
        column = self._column(where, condition.column)
        if column is None:
            return
        self._reach(where, table, condition.column, "filter column")
        kind = column_kind(self.model, column)
        for operator, operand in condition.operators.items():
            at = [*where, operator] if condition.written == "operators" else where
            if operator in ORDER_OPERATORS and not (is_numeric(kind) or is_temporal(kind)):
                self.error(
                    at,
                    f"`{operator}` compares by order, and {condition.column} is "
                    f"{'an enum' if kind == 'enum' else kind}: list the values it may hold "
                    "with `in` instead",
                )
                continue
            if operator == "is_null":
                if operand is True and _never_null(column):
                    self.warn(
                        at,
                        f"can never match: {condition.column} is never null in the generated data",
                    )
                continue
            values = operand if isinstance(operand, list) else [operand]
            ok = True
            for index, value in enumerate(values):
                message = _value_problem(self.model, column, kind, value)
                if message is not None:
                    ok = False
                    value_at = [*at, index] if isinstance(operand, list) else at
                    self.error(value_at, message)
            if operator == "between" and ok and len(values) == 2:
                low, high = (_comparable(kind, value) for value in values)
                if low is not None and high is not None and low > high:
                    self.warn(
                        at, f"can never match: {_show(values[0])} is above {_show(values[1])}"
                    )
                    continue
            if ok and is_numeric(kind):
                self._never_matches(at, condition.column, column, operator, operand)

    def _never_matches(
        self, at: list[PathPart], name: str, column: Column, operator: str, operand: Any
    ) -> None:
        low, high = column.generate.get("min"), column.generate.get("max")
        low = low if _number(low) else None
        high = high if _number(high) else None
        if low is None and high is None:
            return
        bounds = (
            f"between {low} and {high}"
            if low is not None and high is not None
            else (f"at least {low}" if low is not None else f"at most {high}")
        )

        def outside(value: Any) -> bool:
            return (low is not None and value < low) or (high is not None and value > high)

        never = False
        if operator == "eq":
            never = outside(operand)
        elif operator == "in":
            never = all(outside(value) for value in operand)
        elif operator == "gt":
            never = high is not None and operand >= high
        elif operator == "gte":
            never = high is not None and operand > high
        elif operator == "lt":
            never = low is not None and operand <= low
        elif operator == "lte":
            never = low is not None and operand < low
        elif operator == "between":
            never = (high is not None and operand[0] > high) or (
                low is not None and operand[1] < low
            )
        if never:
            self.warn(at, f"can never match: {name} is generated {bounds}")


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _never_null(column: Column) -> bool:
    return column.pk or column.not_null


def _parse_temporal(kind: ColumnKind, value: str) -> Optional[datetime]:
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


def _comparable(kind: ColumnKind, value: Any) -> Any:
    if is_temporal(kind) and isinstance(value, str):
        return _parse_temporal(kind, value)
    return value if _number(value) else None


def _value_problem(model: Model, column: Column, kind: ColumnKind, value: Any) -> Optional[str]:
    """Why `value` cannot be a value of `column`, or None when it can."""
    if kind == "enum":
        enum = model.enum_for(column.type)
        assert enum is not None
        text = str(value) if isinstance(value, int) and not isinstance(value, bool) else value
        if isinstance(text, str) and text in enum.members:
            return None
        members = ", ".join(enum.members)
        hint = _did_you_mean(text, enum.members) if isinstance(text, str) else ""
        return f"{_show(value)} is not a member of {column.type} ({members}){hint}"
    if kind == "boolean":
        return None if isinstance(value, bool) else f"must be true or false, not {_show(value)}"
    if is_numeric(kind):
        if not _number(value):
            return f"must be a number, as {column.type} is numeric, not {_describe(value)}"
        if kind == "integer" and isinstance(value, float) and not value.is_integer():
            return f"must be a whole number, as {column.type} is an integer type (got {value!r})"
        return None
    if is_temporal(kind):
        if isinstance(value, str) and _parse_temporal(kind, value) is not None:
            return None
        shape = "YYYY-MM-DD" if kind == "date" else "YYYY-MM-DD or YYYY-MM-DD HH:MM:SS"
        return f"must be an ISO 8601 {kind} as a string, {shape} (got {_show(value)})"
    if not isinstance(value, str):
        return f"must be text, as {column.type} is a text column: quote it (got {_show(value)})"
    return None
