"""The metrics a model implies on its own, without a metrics file.

They are named the way model2data studio's semantic-layer export names its
metrics (`api/modelling/semantic.py` there), so the studio can move onto this
module without renaming a metric anyone already uses:

- one simple metric for each column with a `measure`, named
  `<table>_<column>`, with `_<aggregation>` appended when it is not `sum`
  (`orders_total_amount`, `order_items_unit_price_average`);
- one row count for each fact, named `<table>_count`. A fact is a table whose
  `role` is `fact`, or which has no `role` and at least one measure; the
  studio's classifier decides the second case from the data too, which the
  engine does not have here, and a table with a measure is the case it decides
  as a fact.

A name is made of lower-case letters, digits and single underscores, starts
with a letter (else `c_` is put in front) and is at least two characters; a
name already handed out gets `_2`, `_3`. Columns with `measure: false`, or no
`measure`, imply nothing: the studio also infers measures for facts from the
data, which the engine leaves to the metrics file.
"""

from __future__ import annotations

import re

from model2data.metrics.types import Metric
from model2data.model.types import Model

_AGG_WORDS = {
    "sum": "Sum",
    "average": "Average",
    "min": "Smallest",
    "max": "Largest",
    "median": "Median",
    "count": "Count",
    "count_distinct": "Distinct count",
}


def metric_name(text: str) -> str:
    """`text` as a metric name: lower case, digits and single underscores, starting with a
    letter, at least two characters, not ending in an underscore."""
    name = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]+", "_", text.lower())).strip("_")
    if not name or not name[0].isalpha():
        name = f"c_{name}" if name else "unnamed"
    return name if len(name) >= 2 else f"{name}_x"


class _Names:
    """Names handed out once each, the second taker of a name getting `_2`."""

    def __init__(self) -> None:
        self.taken: set[str] = set()

    def take(self, text: str) -> str:
        base = metric_name(text)
        name, suffix = base, 1
        while name in self.taken:
            suffix += 1
            name = f"{base}_{suffix}"
        self.taken.add(name)
        return name


def _label(text: str) -> str:
    words = text.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def aggregation_of(measure: object) -> str | None:
    """How a column's `measure` aggregates: `True` is `sum`; None when it is no measure."""
    if measure is True:
        return "sum"
    if isinstance(measure, str):
        return measure
    return None


def inferred_metrics(model: Model) -> dict[str, Metric]:
    """Every metric the model implies, by name, tables and columns in document order."""
    names = _Names()
    out: dict[str, Metric] = {}
    for key, table in model.tables.items():
        measured = False
        for column_name, column in table.columns.items():
            aggregation = aggregation_of(column.measure)
            if aggregation is None:
                continue
            measured = True
            suffix = "" if aggregation == "sum" else f"_{aggregation}"
            name = names.take(f"{key}_{column_name}{suffix}")
            label = f"{_label(key)} {column_name.replace('_', ' ')}"
            if aggregation != "sum":
                label += f" ({_AGG_WORDS[aggregation].lower()})"
            out[name] = Metric(
                kind="simple",
                measure=f"{key}.{column_name}",
                agg=aggregation,
                label=label,
                description=f"{_AGG_WORDS[aggregation]} of {key}.{column_name}.",
                inferred=True,
            )
        if table.role == "fact" or (table.role is None and measured):
            name = names.take(f"{key}_count")
            out[name] = Metric(
                kind="count",
                count=key,
                label=f"{_label(key)} count",
                description=f"Rows of {key}.",
                inferred=True,
            )
    return out
