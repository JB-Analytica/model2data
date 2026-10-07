"""A semantic model as Lightdash metrics and dimensions: the `meta` of the dbt project's YAML.

Lightdash reads its semantic layer from a dbt project's model properties, under
`config.meta` (dbt 1.10 and later) at model and column level. The generated
project has one dbt model per table, the staging model `stg_<name>`, so each
becomes a Lightdash table with its explore. The mapping:

| model2data | Lightdash (dbt `config.meta`) |
|---|---|
| a table | its staging model: `label`, `primary_key`, and `joins` |
| a foreign key | a `many-to-one` (or `one-to-one`) join from the many side, `sql_on` its columns |
| a table reached along exactly one path | joined in the explore, a step at a time |
| a dimension | the column's `dimension: {label}`; a column that is not one is `hidden` |
| a simple metric | a metric on its column: `type` its aggregation |
| a row count | `count_distinct` on a one-column primary key (the same number, and safe in an explore that joins the table), else a model-level `count` of `1` |
| a filter | the metric's `filters`, or, where they cannot say it, a `CASE WHEN` in its `sql` |
| `time` on the metric's table | the metric's `default_time_dimension`, by `MONTH` |
| a ratio or derived metric | a model-level `type: number` metric over its inputs, `${name}` |
| `label`, `description`, `ai_context` | `label`, `description`, `ai_hint` |
| `format: percent`, `currency` | `format: '0.00%'`, `'#,##0.00'` (spreadsheet-style) |

Lightdash's own filter grammar (`packages/common/src/types/filterGrammar.grammar.ts`)
decides what goes in `filters`. A list of values is passed through as an `IN`
and never parsed, so `eq` and `in` are written as lists. Lightdash's not-equal
lets nulls through, where model2data's lets them fail, so `ne` and `not_in` add
`'!null'`. Comparisons on numbers are `'> 5'` and `'between 0 and 50'`.
Anything else -- an `any` or `all` group, a comparison on a date or timestamp,
a value the grammar would read as something else -- is written into the
metric's `sql` as a `CASE WHEN`, which Lightdash ANDs with the filters, so the
metric still counts exactly what model2data counts.

What Lightdash has no place for, and what this export leaves out, is listed in
the export's lossiness report: a table's role and grain, an enum's members, a
currency, a metric dated by a column of another table, a ratio or derived
metric whose inputs are on different tables (a Lightdash metric belongs to
one table and sees another only through a join from it), a table an explore
cannot join because two paths reach it, and a column name that is not a plain
lowercase identifier.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Union

import yaml

from model2data.metrics import expression as expr
from model2data.metrics.columns import ColumnKind, column_kind, find_column, is_temporal
from model2data.metrics.graph import Graph
from model2data.metrics.graph import Path as JoinPath
from model2data.metrics.infer import _label
from model2data.metrics.ossie import Loss
from model2data.metrics.semantic import Entity, ResolvedMetric, SemanticModel
from model2data.metrics.sql import identifier, where_sql
from model2data.metrics.types import Condition, Where

# The interval a metric's default time dimension is explored by: Lightdash's own default.
DEFAULT_INTERVAL = "MONTH"
# model2data's `format` as a Lightdash spreadsheet-style format; `number` is Lightdash's default.
FORMATS = {"percent": "0.00%", "currency": "#,##0.00"}
# The aggregation of a simple metric as a Lightdash metric type: the same words but one.
TYPES = {
    "sum": "sum",
    "average": "average",
    "min": "min",
    "max": "max",
    "median": "median",
    "count": "count",
    "count_distinct": "count_distinct",
}

_PLAIN = re.compile(r"^[a-z_][a-z0-9_]*$")
# A value Lightdash's filter grammar reads back as itself after a leading `!`: no
# symbol it treats specially (`%`, `,`, `^`), no quote, backslash or control
# character, no space at either end, and no keyword (`null`, `empty`) at the start.
_UNSAFE = re.compile(r'[%,^"\\\x00-\x1f]')
_KEYWORDS = ("null", "NULL", "empty", "EMPTY")


def default_source(entity: Entity) -> str:
    """The dbt model a table is in the generated project: its staging model."""
    return f"stg_{entity.name}"


@dataclass
class LightdashExport:
    """The dbt model properties that carry the Lightdash meta, and what they could not say.

    `models` holds one dbt properties entry per model (`name`, `description`,
    `config.meta`, `columns`), `metrics` the names of the metrics exported, in
    order.
    """

    models: list[dict[str, Any]]
    lossiness: list[Loss] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)

    @property
    def document(self) -> dict[str, Any]:
        """A dbt properties file (`version: 2`) describing every model."""
        return {"version": 2, "models": self.models}

    def to_yaml(self) -> str:
        """The properties file as YAML, the lossiness report as comments above it."""
        lines = [
            "# Lightdash metrics and dimensions (dbt config.meta) for the staging models,",
            "# written by model2data from the model and its metrics.",
        ]
        if self.lossiness:
            lines.append("#")
            lines.append("# Not expressible in Lightdash, left out:")
            for loss in self.lossiness:
                lines.extend(
                    textwrap.wrap(
                        str(loss),
                        width=100,
                        initial_indent="#   - ",
                        subsequent_indent="#     ",
                        break_on_hyphens=False,
                    )
                )
        return "\n".join(lines) + "\n" + _dump(self.document)


def _dump(data: dict[str, Any]) -> str:
    # One line per SQL expression, however long: folded SQL is hard to read and to diff.
    return yaml.safe_dump(
        data, default_flow_style=False, sort_keys=False, allow_unicode=True, width=1_000_000
    )


class _Export:
    """The state of one export: the models' meta as it is built, and the losses."""

    def __init__(self, semantic: SemanticModel, source: Callable[[Entity], str]):
        self.semantic = semantic
        self.model = semantic.model
        self.graph = Graph(semantic.model)
        self.names = {key: source(entity) for key, entity in semantic.entities.items()}
        self.losses: list[Loss] = []
        self.model_metrics: dict[str, dict[str, Any]] = {key: {} for key in self.names}
        self.column_metrics: dict[tuple[str, str], dict[str, Any]] = {}
        # What a metric needs joined besides its own table, by metric.
        self.needs: dict[str, set[str]] = {}
        # The metrics dated by a column of another table, by that column.
        self.dated_elsewhere: dict[str, list[str]] = {}

    # -- references -----------------------------------------------------------
    def ref(self, table: str, home: str, column: str) -> str:
        """A column as Lightdash SQL names it, from a metric on table `home`."""
        if table == home:
            return "${" + column + "}"
        return "${" + f"{self.names[table]}.{column}" + "}"

    def filter_key(self, table: str, home: str, column: str) -> str:
        return column if table == home else f"{self.names[table]}.{column}"

    def kind(self, path: str) -> ColumnKind:
        _, column = find_column(self.model, path)
        assert column is not None
        return column_kind(self.model, column)


def to_lightdash(
    semantic: SemanticModel, *, source: Optional[Callable[[Entity], str]] = None
) -> LightdashExport:
    """The Lightdash meta of `semantic` as dbt model properties, and what it could not express.

    `source` names the dbt model each table is; by default the staging model of
    the dbt project `model2data generate` writes, `stg_<name>`.
    """
    state = _Export(semantic, source or default_source)
    # Simple metrics and row counts first: a ratio or derived metric reads what they need.
    for name, metric in semantic.metrics.items():
        if metric.kind in ("simple", "count"):
            _simple(state, name, metric)
    for name, metric in semantic.metrics.items():
        if metric.kind not in ("simple", "count"):
            _number(state, name, metric)
    for time, names in state.dated_elsewhere.items():
        state.losses.append(
            Loss(
                f"{'metric' if len(names) == 1 else 'metrics'} {', '.join(names)}",
                "time",
                f"dated by {time}, a column of {state.names[time.rpartition('.')[0]]}; a "
                "Lightdash metric's default time dimension is a field of its own table",
            )
        )
    models = [_model(state, key) for key in semantic.entities]
    _general_losses(state)
    exported = [name for name in semantic.metrics if name in state.needs]
    return LightdashExport(models, state.losses, exported)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def _common(metric: ResolvedMetric) -> dict[str, Any]:
    entry: dict[str, Any] = {"label": metric.label}
    if metric.description:
        entry["description"] = metric.description.strip()
    if metric.ai_context:
        entry["ai_hint"] = metric.ai_context.strip()
    if metric.format in FORMATS:
        entry["format"] = FORMATS[metric.format]
    return entry


def _simple(state: _Export, name: str, metric: ResolvedMetric) -> None:
    assert metric.table is not None
    home = metric.table
    entity = state.semantic.entities[home]
    filters, sql_where = _where(state, metric.where, home) if metric.where else ([], None)
    entry: dict[str, Any] = {}
    if metric.kind == "simple":
        assert metric.column is not None and metric.agg is not None
        column = metric.column
        entry["type"] = TYPES[metric.agg]
        value = state.ref(home, home, column)
    elif len(entity.primary_key) == 1:
        # A one-column key is never null and never repeats: counting it distinctly
        # counts the rows, and stays right in an explore that joins this table.
        column = entity.primary_key[0]
        entry["type"] = "count_distinct"
        value = state.ref(home, home, column)
    else:
        column = None
        entry["type"] = "count"
        value = "1"
    if sql_where is not None:
        entry["sql"] = f"CASE WHEN {sql_where} THEN {value} END"
    elif column is None:
        entry["sql"] = value
    entry.update(_common(metric))
    if filters:
        entry["filters"] = filters
    if metric.time is not None:
        time_table, _, time_column = metric.time.rpartition(".")
        if time_table == home:
            entry["default_time_dimension"] = {"field": time_column, "interval": DEFAULT_INTERVAL}
        else:
            state.dated_elsewhere.setdefault(metric.time, []).append(name)
    state.needs[name] = set(metric.joins)
    if column is None:
        state.model_metrics[home][name] = entry
    else:
        state.column_metrics.setdefault((home, column), {})[name] = entry


def _number(state: _Export, name: str, metric: ResolvedMetric) -> None:
    semantic = state.semantic
    inputs = semantic.simple_inputs(name)
    tables = list(dict.fromkeys(semantic.metrics[i].table for i in inputs))
    if len(tables) != 1:
        named = ", ".join(state.names[t] for t in tables if t is not None)
        state.losses.append(
            Loss(
                f"metric {name}",
                "inputs",
                f"its inputs are on {named}; a Lightdash metric belongs to one table and sees "
                "another only through a join from it, which leaves out rows nothing joins to "
                "and repeats rows joined more than once, so it is not exported",
            )
        )
        return
    home = tables[0]
    assert home is not None
    entry: dict[str, Any] = {"type": "number", "sql": _number_sql(state, name, top=True)}
    entry.update(_common(metric))
    state.needs[name] = set().union(*(state.needs[i] for i in inputs))
    state.model_metrics[home][name] = entry


def _number_sql(state: _Export, name: str, top: bool = False) -> str:
    """A ratio or derived metric over its simple inputs, `${input}`, inner ones written out."""
    metric = state.semantic.metrics[name]
    if metric.kind in ("simple", "count"):
        return "${" + name + "}"
    if metric.kind == "ratio":
        assert metric.numerator is not None and metric.denominator is not None
        text = (
            f"{_number_sql(state, metric.numerator)} / "
            f"NULLIF({_number_sql(state, metric.denominator)}, 0)"
        )
        return text if top else f"({text})"
    assert metric.node is not None
    return expr.to_sql(metric.node, lambda dep: _number_sql(state, dep)).replace(
        "nullif(", "NULLIF("
    )


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def _where(state: _Export, where: Where, home: str) -> tuple[list[dict[str, Any]], Optional[str]]:
    """A filter as Lightdash `filters`, and the SQL condition of the terms they cannot say."""
    filters: list[dict[str, Any]] = []
    rest: list[Union[Condition, Where]] = []
    for term in where.terms:
        found = _condition_filters(state, term, home) if isinstance(term, Condition) else None
        if found is None:
            rest.append(term)
        else:
            filters.extend(found)
    if not rest:
        return filters, None

    def column_of(path: str) -> tuple[str, ColumnKind]:
        table, _, column = path.rpartition(".")
        return state.ref(table, home, column), state.kind(path)

    return filters, where_sql(Where(terms=rest), column_of)


def _text(value: Any, kind: ColumnKind) -> str:
    """A value as Lightdash's filters hold it: always a string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if kind in ("integer", "decimal") and isinstance(value, float):
        return format(Decimal(repr(value)), "f")
    return str(value)


def _negatable(text: str) -> bool:
    return (
        text != ""
        and text == text.strip()
        and not _UNSAFE.search(text)
        and not text.startswith(_KEYWORDS)
    )


def _condition_filters(
    state: _Export, condition: Condition, home: str
) -> Optional[list[dict[str, Any]]]:
    """A condition as Lightdash filters (every one must hold), or None when they cannot say it."""
    table, _, column = condition.column.rpartition(".")
    key = state.filter_key(table, home, column)
    kind = state.kind(condition.column)
    out: list[dict[str, Any]] = []
    for operator, operand in condition.operators.items():
        if operator == "is_null":
            out.append({key: "null" if operand else "!null"})
            continue
        if is_temporal(kind):
            return None
        if operator in ("eq", "in"):
            values = [operand] if operator == "eq" else list(operand)
            if kind == "boolean" and len(set(values)) > 1:
                return None  # Lightdash compares a boolean with the first value only
            out.append({key: [_text(value, kind) for value in values]})
        elif operator in ("ne", "not_in"):
            values = [operand] if operator == "ne" else list(operand)
            texts = [_text(value, kind) for value in values]
            if not all(_negatable(text) for text in texts):
                return None
            out.extend({key: f"!{text}"} for text in texts)
            out.append({key: "!null"})
        elif operator == "between":
            low, high = (_text(value, kind) for value in operand)
            out.append({key: f"between {low} and {high}"})
        else:  # gt, gte, lt, lte: on a number, as the spec allows no other kind here
            sign = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
            out.append({key: f"{sign} {_text(operand, kind)}"})
    # `ne` and `is_null: false` on one column both say `!null`: once is enough.
    return [item for i, item in enumerate(out) if item not in out[:i]]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
def _joins(state: _Export, key: str) -> list[tuple[str, JoinPath]]:
    """Every table `key` reaches along exactly one path, nearest first, with the path."""
    found: list[tuple[str, JoinPath]] = []
    ambiguous: list[str] = []
    for other in state.semantic.entities:
        if other == key:
            continue
        paths = state.graph.paths(key, other)
        if len(paths) == 1:
            found.append((other, paths[0]))
        elif paths:
            ambiguous.append(other)
    if ambiguous:
        state.losses.append(
            Loss(
                f"table {state.names[key]}",
                "joins",
                f"{', '.join(state.names[t] for t in ambiguous)} "
                f"{'is' if len(ambiguous) == 1 else 'are'} reached along more than one path; "
                "a Lightdash explore joins a table once, so its explore does not join "
                f"{'it' if len(ambiguous) == 1 else 'them'}",
            )
        )
    found.sort(key=lambda item: len(item[1]))
    return found


def _model(state: _Export, key: str) -> dict[str, Any]:
    semantic = state.semantic
    entity = semantic.entities[key]
    table = semantic.model.tables[key]
    meta: dict[str, Any] = {"label": _label(key)}
    if entity.primary_key:
        meta["primary_key"] = (
            entity.primary_key[0] if len(entity.primary_key) == 1 else list(entity.primary_key)
        )
    joins = _joins(state, key)
    joined = {other for other, _ in joins}
    entries = []
    for other, path in joins:
        step = path[-1]
        on = " AND ".join(
            "${" + f"{state.names[step.child]}.{child}" + "} = ${"
            f"{state.names[other]}.{parent}" + "}"
            for child, parent in zip(step.child_columns, step.parent_columns, strict=True)
        )
        join: dict[str, Any] = {
            "join": state.names[other],
            "sql_on": on,
            "relationship": "one-to-one" if _one_to_one(semantic, step) else "many-to-one",
        }
        hidden = [
            name for name in _metrics_on(state, other) if not state.needs[name] <= joined | {key}
        ]
        if hidden:
            join["fields"] = [
                *semantic.model.tables[other].columns,
                *(name for name in _metrics_on(state, other) if name not in hidden),
            ]
            state.losses.append(
                Loss(
                    f"table {state.names[key]}",
                    "joined metrics",
                    f"its explore joins {state.names[other]} without "
                    f"{', '.join(hidden)}: {'it filters' if len(hidden) == 1 else 'they filter'} "
                    "on a table the explore does not join",
                )
            )
        entries.append(join)
    if entries:
        meta["joins"] = entries
    if state.model_metrics[key]:
        meta["metrics"] = state.model_metrics[key]

    out: dict[str, Any] = {"name": state.names[key]}
    if entity.description:
        out["description"] = entity.description.strip()
    out["config"] = {"meta": meta}
    dimensions = {d.column: d for d in semantic.dimensions if d.table == key}
    columns = []
    for name, column in table.columns.items():
        item: dict[str, Any] = {"name": identifier(name)}
        if column.description:
            item["description"] = column.description.strip()
        column_meta: dict[str, Any] = {}
        dimension = dimensions.get(name)
        if dimension is None:
            column_meta["dimension"] = {"hidden": True}
        else:
            column_meta["dimension"] = {"label": dimension.label}
            if dimension.description and dimension.description != column.description:
                column_meta["dimension"]["description"] = dimension.description.strip()
        if (key, name) in state.column_metrics:
            column_meta["metrics"] = state.column_metrics[(key, name)]
        item["config"] = {"meta": column_meta}
        columns.append(item)
    out["columns"] = columns
    return out


def _metrics_on(state: _Export, key: str) -> list[str]:
    names = [
        n for (table, _), metrics in state.column_metrics.items() if table == key for n in metrics
    ]
    return names + list(state.model_metrics[key])


def _one_to_one(semantic: SemanticModel, step: Any) -> bool:
    return any(
        r.one_to_one
        for r in semantic.relationships
        if (r.from_table, r.from_columns, r.to_table)
        == (step.child, step.child_columns, step.parent)
    )


def _general_losses(state: _Export) -> None:
    semantic = state.semantic
    for key, entity in semantic.entities.items():
        extra = [item for item, value in (("role", entity.role), ("grain", entity.grain)) if value]
        if extra:
            state.losses.append(
                Loss(
                    f"table {state.names[key]}",
                    " and ".join(extra),
                    "Lightdash tables have no " + " or ".join(extra),
                )
            )
    enums = [
        f"{state.names[d.table]}.{d.column}" for d in semantic.dimensions if d.members is not None
    ]
    if enums:
        state.losses.append(
            Loss(
                f"{'dimension' if len(enums) == 1 else 'dimensions'} {', '.join(enums)}",
                "values",
                "Lightdash dimensions have no list of allowed values (the generated project's "
                "accepted_values tests still check them)",
            )
        )
    currency = [
        name
        for name, metric in semantic.metrics.items()
        if name in state.needs and metric.format == "currency"
    ]
    if currency:
        state.losses.append(
            Loss(
                f"{'metric' if len(currency) == 1 else 'metrics'} {', '.join(currency)}",
                "currency",
                "the metrics file names no currency, so the format is '#,##0.00' with no "
                "symbol; write one in, as '[$€]#,##0.00'",
            )
        )
    awkward = [
        f"{state.names[key]}.{name}"
        for key, table in semantic.model.tables.items()
        for name in table.columns
        if not _PLAIN.match(name)
    ]
    if awkward:
        state.losses.append(
            Loss(
                f"{'column' if len(awkward) == 1 else 'columns'} {', '.join(awkward)}",
                "name",
                "Lightdash refers to a field by a plain lowercase name, in ${...} and in filters; "
                "a reference to this one may not resolve",
            )
        )


# ---------------------------------------------------------------------------
# Into a generated dbt project
# ---------------------------------------------------------------------------
STAGING = Path("models") / "staging"


def _first(entry: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    """`entry` with `keys` moved to the front, in that order, the rest as they were."""
    head = {k: entry[k] for k in keys if k in entry}
    return {**head, **{k: v for k, v in entry.items() if k not in head}}


def _unquoted(name: str) -> str:
    if len(name) >= 2 and name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('""', '"')
    return name


def write_lightdash(dest: Path, export: LightdashExport) -> list[Path]:
    """Merge the export into the staging models' own YAML in the dbt project at `dest`.

    dbt reads a model's properties from one file only, so the meta goes into
    `models/staging/<model>.yml`, the file `generate` wrote for it: the model's
    `description` and `config.meta`, and each column's `config.meta`. Its tests
    and everything else are kept. The paths written, in model order.
    """
    written = []
    for model in export.models:
        path = dest / STAGING / f"{model['name']}.yml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = document["models"]
        index = next(i for i, e in enumerate(entries) if e["name"] == model["name"])
        entry = dict(entries[index])
        if "description" in model:
            entry.setdefault("description", model["description"])
        entry["config"] = {**entry.get("config", {}), **model["config"]}
        by_name = {_unquoted(c["name"]): c for c in model["columns"]}
        columns = []
        for column in entry.get("columns", []):
            extra = by_name.get(_unquoted(column["name"]))
            if extra is not None:
                column = {**column, "config": {**column.get("config", {}), **extra["config"]}}
                column = _first(column, ("name", "description", "config"))
            columns.append(column)
        entry["columns"] = columns
        entries[index] = _first(entry, ("name", "description", "config", "columns"))
        path.write_text(_dump(document), encoding="utf-8")
        written.append(path)
    return written
