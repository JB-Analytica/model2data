"""Metrics next to the model: metrics spec 0.1.0, read, checked, resolved, computed and exported.

A metrics file is `<stem>.metrics.yml` beside `<stem>.model2data.yml`, as
`model2data/spec/metrics/README.md` and `metrics.schema.json` define it. The
generator never reads it: a model generates the same data with or without one.

    >>> from model2data.model import load as load_model
    >>> from model2data import metrics
    >>> model = load_model("coffee_webshop.model2data.yml")
    >>> found = metrics.load("coffee_webshop.metrics.yml", model)   # MetricsError on errors
    >>> semantic = metrics.resolve(model, found)    # entities, relationships, dimensions, metrics
    >>> values = metrics.known_values(semantic, frames)     # over a run's generated tables
    >>> export = metrics.to_ossie(semantic)                 # Apache Ossie 0.1.1
    >>> export.to_yaml(), export.lossiness
    >>> metrics.to_lightdash(semantic).to_yaml()           # Lightdash meta, dbt YAML

`resolve(model)` with no metrics file gives the metrics the model implies on
its own: one per `measure` column, and a row count per fact.
"""

from model2data.metrics.check import SCHEMA_URL, SPEC_VERSION, check_against, check_document
from model2data.metrics.dbt import metric_test_sql, write_metric_tests
from model2data.metrics.expression import ExpressionError
from model2data.metrics.expression import parse as parse_expression
from model2data.metrics.graph import Graph, Join
from model2data.metrics.infer import inferred_metrics
from model2data.metrics.lightdash import LightdashExport, to_lightdash, write_lightdash
from model2data.metrics.ossie import OSSIE_VERSION, Loss, OssieExport, to_ossie
from model2data.metrics.reader import (
    MetricsError,
    from_dict,
    is_metrics_file,
    load,
    metrics_stem,
    sibling_model,
    validate,
)
from model2data.metrics.semantic import (
    Dimension,
    Entity,
    Measure,
    Relationship,
    ResolvedMetric,
    SemanticModel,
    resolve,
)
from model2data.metrics.types import (
    AGGREGATIONS,
    FORMATS,
    OPERATORS,
    Condition,
    DimensionOverride,
    Metric,
    Metrics,
    Ratio,
    Where,
)
from model2data.metrics.values import KnownValue, KnownValues, known_values

__all__ = [
    "AGGREGATIONS",
    "FORMATS",
    "OPERATORS",
    "OSSIE_VERSION",
    "SCHEMA_URL",
    "SPEC_VERSION",
    "Condition",
    "Dimension",
    "DimensionOverride",
    "Entity",
    "ExpressionError",
    "Graph",
    "Join",
    "KnownValue",
    "KnownValues",
    "LightdashExport",
    "Loss",
    "Measure",
    "Metric",
    "Metrics",
    "MetricsError",
    "OssieExport",
    "Ratio",
    "Relationship",
    "ResolvedMetric",
    "SemanticModel",
    "Where",
    "check_against",
    "check_document",
    "from_dict",
    "inferred_metrics",
    "is_metrics_file",
    "known_values",
    "load",
    "metric_test_sql",
    "metrics_stem",
    "parse_expression",
    "resolve",
    "sibling_model",
    "to_lightdash",
    "to_ossie",
    "validate",
    "write_lightdash",
    "write_metric_tests",
]
