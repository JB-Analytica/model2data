"""One semantic representation of a model and its metrics, which every exporter reads.

`resolve(model, metrics)` turns a model and its metrics file (or none: then
the metrics are the ones the model implies) into a `SemanticModel`:

- **entities**, one per table: its keys, grain, role and description, and the
  dbt name its seed and staging model go by (`stg_<name>`);
- **relationships**, one per foreign key: many side to one side, the columns
  paired in order;
- **dimensions**: by default every enum, boolean, date and timestamp column
  that is not a key, a foreign key or a measure (dates and timestamps are
  time dimensions whether keyed or not); `dimensions:` in the metrics file
  adds a column (`true`, or a label) or takes one away (`false`);
- **measures**: every column with a `measure`, and how it aggregates;
- **metrics**: the file's metrics and the inferred ones it does not replace,
  each resolved -- the table it aggregates, the aggregation, the join path to
  every table its filter and time column are on, and its time column, the
  default one filled in.

The known values (`model2data.metrics.values`), the dbt tests
(`model2data.metrics.dbt`) and the Ossie export (`model2data.metrics.ossie`)
all read this and nothing else, so they cannot disagree about what a metric
is. model2data studio's semantic-layer export is meant to move onto it too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

from model2data.dbt.naming import dbt_identifier
from model2data.metrics import expression as expr
from model2data.metrics.check import all_metrics, check_against
from model2data.metrics.columns import ColumnKind, column_kind, find_column, is_temporal
from model2data.metrics.graph import Graph, Join, Path
from model2data.metrics.infer import _label, _Names, aggregation_of
from model2data.metrics.reader import MetricsError
from model2data.metrics.types import Metric, Metrics, Where
from model2data.model.types import Model


@dataclass(frozen=True)
class Entity:
    """A table as a business entity. `name` is its dbt name (`stg_<name>` in the project)."""

    table: str
    name: str
    primary_key: tuple[str, ...]
    unique_keys: tuple[tuple[str, ...], ...]
    grain: Optional[tuple[str, ...]]
    role: Optional[str]
    description: Optional[str]


@dataclass(frozen=True)
class Relationship:
    """A foreign key: rows of `from_table` (the many side) to one row of `to_table`."""

    name: str
    from_table: str
    from_columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    one_to_one: bool = False


@dataclass(frozen=True)
class Dimension:
    """A column to group or filter by: `time` for a date or timestamp, else `categorical`."""

    table: str
    column: str
    kind: Literal["categorical", "time"]
    data_kind: ColumnKind
    label: str
    description: Optional[str] = None
    members: Optional[tuple[str, ...]] = None

    @property
    def path(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True)
class Measure:
    table: str
    column: str
    agg: str


@dataclass
class ResolvedMetric:
    """A metric with everything a consumer needs to compute it.

    For `simple` and `count`: `table` is aggregated -- `agg` over `column`, or
    `count` over its rows -- after joining, along `joins`, every table its
    `where` or `time` is on (by table key; the metric's own table is not in
    it). `time` is the column path it is dated by, None for none. For `ratio`
    and `derived`: `inputs` are the metrics it is computed from, `ratio` or
    `node` how.
    """

    name: str
    kind: Literal["simple", "count", "ratio", "derived"]
    label: str
    description: Optional[str] = None
    format: Optional[str] = None
    ai_context: Optional[str] = None
    inferred: bool = False
    table: Optional[str] = None
    column: Optional[str] = None
    column_kind: Optional[ColumnKind] = None
    agg: Optional[str] = None
    where: Optional[Where] = None
    time: Optional[str] = None
    time_kind: Optional[ColumnKind] = None
    joins: dict[str, Path] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    numerator: Optional[str] = None
    denominator: Optional[str] = None
    node: Optional[expr.Node] = None
    expression: Optional[str] = None


@dataclass
class SemanticModel:
    name: str
    description: Optional[str]
    entities: dict[str, Entity]
    relationships: list[Relationship]
    dimensions: list[Dimension]
    measures: list[Measure]
    metrics: dict[str, ResolvedMetric]
    model: Model = field(repr=False, compare=False)

    def simple_inputs(self, name: str) -> list[str]:
        """The simple metrics and row counts `name` is computed from, itself if it is one,
        each once, in the order they are first used."""
        found: dict[str, None] = {}

        def walk(current: str) -> None:
            metric = self.metrics[current]
            if metric.kind in ("simple", "count"):
                found[current] = None
            for dep in metric.inputs:
                walk(dep)

        walk(name)
        return list(found)


def resolve(
    model: Model, metrics: Optional[Metrics] = None, *, model_name: Optional[str] = None
) -> SemanticModel:
    """The semantic model of `model` and `metrics` (None: the inferred metrics only).

    `metrics` should be what `load(..., model)` returned; one that does not fit
    the model raises `MetricsError`.
    """
    if metrics is None:
        metrics = Metrics(model=model.name or model_name or "model")
    else:
        issues = check_against(metrics, model, model_name=model_name)
        errors = [issue for issue in issues if issue.is_error]
        if errors:
            raise MetricsError(errors)
    graph = Graph(model)
    return SemanticModel(
        name=model.name or model_name or metrics.model,
        description=model.description,
        entities=_entities(model),
        relationships=_relationships(graph),
        dimensions=_dimensions(model, metrics),
        measures=[
            Measure(key, name, agg)
            for key, table in model.tables.items()
            for name, column in table.columns.items()
            if (agg := aggregation_of(column.measure)) is not None
        ],
        metrics={
            name: _resolve_metric(model, graph, name, metric)
            for name, metric in all_metrics(metrics, model).items()
        },
        model=model,
    )


def _entities(model: Model) -> dict[str, Entity]:
    out = {}
    for key, table in model.tables.items():
        unique: list[tuple[str, ...]] = [
            (name,) for name, column in table.columns.items() if column.unique
        ]
        unique += [tuple(k.columns) for k in table.keys if k.kind == "unique"]
        out[key] = Entity(
            table=key,
            name=dbt_identifier(key),
            primary_key=tuple(table.primary_key()),
            unique_keys=tuple(unique),
            grain=tuple(table.grain) if table.grain else None,
            role=table.role,
            description=table.description,
        )
    return out


def _relationships(graph: Graph) -> list[Relationship]:
    names = _Names()
    out = []
    for steps in graph.steps.values():
        for step in steps:
            out.append(
                Relationship(
                    name=names.take(
                        f"{dbt_identifier(step.child)}_{'_'.join(step.child_columns)}"
                        f"_to_{dbt_identifier(step.parent)}"
                    ),
                    from_table=step.child,
                    from_columns=step.child_columns,
                    to_table=step.parent,
                    to_columns=step.parent_columns,
                    one_to_one=_one_to_one(graph.model, step),
                )
            )
    return out


def _one_to_one(model: Model, step: Join) -> bool:
    table = model.tables[step.child]
    if len(step.child_columns) == 1:
        reference = table.columns[step.child_columns[0]].references
        if reference is not None and reference.table == step.parent:
            return reference.one_to_one
    return any(
        fk.one_to_one
        for fk in table.foreign_keys
        if tuple(fk.columns) == step.child_columns and fk.references == step.parent
    )


def _dimensions(model: Model, metrics: Metrics) -> list[Dimension]:
    out = []
    for key, table in model.tables.items():
        keyed = set(table.primary_key())
        for k in table.keys:
            keyed.update(k.columns)
        for fk in table.foreign_keys:
            keyed.update(fk.columns)
        for name, column in table.columns.items():
            kind = column_kind(model, column)
            override = metrics.dimensions.get(f"{key}.{name}")
            if override is not None and not override.offered:
                continue
            temporal = is_temporal(kind)
            default = temporal or (
                kind in ("enum", "boolean")
                and not (
                    name in keyed
                    or column.pk
                    or column.unique
                    or column.references is not None
                    or column.measure not in (None, False)
                )
            )
            if not default and override is None:
                continue
            enum = model.enum_for(column.type)
            out.append(
                Dimension(
                    table=key,
                    column=name,
                    kind="time" if temporal else "categorical",
                    data_kind=kind,
                    label=(override.label if override and override.label else None) or _label(name),
                    description=(override.description if override else None) or column.description,
                    members=tuple(enum.members) if enum is not None else None,
                )
            )
    return out


def _resolve_metric(model: Model, graph: Graph, name: str, metric: Metric) -> ResolvedMetric:
    out = ResolvedMetric(
        name=name,
        kind=metric.kind,
        label=metric.label or _label(name),
        description=metric.description,
        format=metric.format,
        ai_context=metric.ai_context,
        inferred=metric.inferred,
    )
    if metric.kind == "ratio":
        assert metric.ratio is not None
        out.numerator, out.denominator = metric.ratio.numerator, metric.ratio.denominator
        out.inputs = tuple(dict.fromkeys((out.numerator, out.denominator)))
        return out
    if metric.kind == "derived":
        assert metric.expression is not None
        out.node = expr.parse(metric.expression)
        out.expression = metric.expression
        out.inputs = tuple(expr.names(out.node))
        return out
    if metric.kind == "simple":
        assert metric.measure is not None
        table_key, column = find_column(model, metric.measure)
        assert table_key is not None and column is not None
        out.table = table_key
        out.column = metric.measure.rpartition(".")[2]
        out.column_kind = column_kind(model, column)
        out.agg = metric.agg or aggregation_of(column.measure)
    else:
        assert metric.count is not None
        out.table = metric.count
        out.agg = "count"
    out.where = metric.where
    joined: list[str] = []
    if metric.time is not None:
        out.time = metric.time
        joined.append(metric.time.rpartition(".")[0])
    else:
        found = graph.first_time_column(out.table)
        if found is not None:
            out.time = found[0]
            joined.append(found[0].rpartition(".")[0])
    if out.time is not None:
        time_table, time_column = find_column(model, out.time)
        assert time_table is not None and time_column is not None
        out.time_kind = column_kind(model, time_column)
    if metric.where is not None:
        joined.extend(
            condition.column.rpartition(".")[0] for condition in metric.where.conditions()
        )
    for table in joined:
        if table != out.table and table not in out.joins:
            out.joins[table] = graph.paths(out.table, table, limit=1)[0]
    return out
