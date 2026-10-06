"""The known value of every metric over a run's generated tables.

A run knows its data, so it knows what each metric comes to: `known_values`
computes them over the frames the run wrote as seeds, overall and per calendar
month of the metric's time column. The generated dbt project then tests that
its own models give the same numbers (`model2data.metrics.dbt`), and anyone
building a semantic layer over the data can check theirs against them.

How the values are computed:

- Each table is read the way its seed is: the frame is written as CSV, exactly
  as the seed file is, and read back as text, an empty field and `null` (in
  any case) being null as dbt's seed loader reads them. Each column a metric
  touches is then cast to the SQL type of its kind, in the same SQL the dbt
  tests run (`model2data.metrics.sql`). DuckDB runs it, on one thread.
- Simple metrics and row counts are SQL aggregates. A ratio is its
  numerator's total over its denominator's, null when the denominator is null
  or zero; a derived metric is its expression over its inputs' totals, with
  nulls in giving null out and division by zero giving null. Both are computed
  from their inputs' values in decimal arithmetic.
- `by_month` holds a value for each calendar month (`YYYY-MM`) in which a row
  of the metric's table has a time; rows with no time count only in the
  total. A month in which a filter keeps no row has the value of an aggregate
  over no rows: 0 for a count, null for anything else. A ratio or derived
  metric has `by_month` when every metric it is computed from has a time,
  each input taken in its own months.
- Values are rounded once, at the end: counts, and the sum, minimum and
  maximum of an integer column, are exact integers; everything else is
  rounded to 6 decimal places, half to even, and written without trailing
  zeros. The same frames give the same JSON, byte for byte.
"""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any, Optional, Union

import pandas as pd

from model2data.metrics import expression as expr
from model2data.metrics.check import SPEC_VERSION
from model2data.metrics.semantic import ResolvedMetric, SemanticModel
from model2data.metrics.sql import query, quote

Number = Union[int, Decimal]
_PLACES = Decimal("0.000001")
ROUNDING = (
    "counts, and sums, minimums and maximums of integer columns, are exact; every other "
    "value is rounded to 6 decimal places, half to even"
)


@dataclass
class KnownValue:
    """One metric's value over the run's data, and per month when it has a time."""

    name: str
    kind: str
    label: str
    value: Optional[Number]
    time: Optional[str] = None
    by_month: Optional[dict[str, Optional[Number]]] = None


@dataclass
class KnownValues:
    model: str
    values: dict[str, KnownValue] = field(default_factory=dict)
    seed: Optional[int] = None
    as_of: Optional[str] = None

    def to_json(self) -> str:
        """`metric_values.json`: stable text, keys in a fixed order, two-space indent."""
        document: dict[str, Any] = {
            "model2data-metrics": SPEC_VERSION,
            "model": self.model,
        }
        if self.seed is not None or self.as_of is not None:
            document["run"] = {
                key: value
                for key, value in (("seed", self.seed), ("as_of", self.as_of))
                if value is not None
            }
        document["rounding"] = ROUNDING
        metrics: dict[str, Any] = {}
        for name, known in self.values.items():
            entry: dict[str, Any] = {"kind": known.kind, "label": known.label, "value": known.value}
            if known.time is not None:
                entry["time"] = known.time
            if known.by_month is not None:
                entry["by_month"] = known.by_month
            metrics[name] = entry
        document["metrics"] = metrics
        return _dump(document) + "\n"


def _dump(value: Any, indent: int = 0) -> str:
    """JSON text with a decimal written as its exact digits (the `json` module has no
    way to write a `Decimal` but as a float, which would round it again)."""
    pad = "  " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [
            f"{pad}{json.dumps(key, ensure_ascii=False)}: {_dump(item, indent + 1)}"
            for key, item in value.items()
        ]
        return "{\n" + ",\n".join(items) + "\n" + "  " * indent + "}"
    if isinstance(value, Decimal):
        return format(value, "f")
    return json.dumps(value, ensure_ascii=False)


def _rounded(value: Any, exact: bool) -> Optional[Number]:
    """A value from DuckDB or the arithmetic, as `known_values` writes it."""
    if value is None:
        return None
    if exact:
        return int(value)
    if isinstance(value, float):
        value = Decimal(repr(value))
    elif not isinstance(value, Decimal):
        value = Decimal(value)
    rounded = value.quantize(_PLACES, rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        return Decimal(0)
    normal = rounded.normalize()
    # normalize() writes 1200 as 1.2E+3; quantize back to no decimals for those.
    return normal if normal.as_tuple().exponent < 0 else normal.quantize(Decimal(1))  # type: ignore[operator]


def _exact(metric: ResolvedMetric) -> bool:
    if metric.kind == "count" or metric.agg in ("count", "count_distinct"):
        return True
    return metric.agg in ("sum", "min", "max") and metric.column_kind == "integer"


def _empty(metric: ResolvedMetric) -> Optional[Number]:
    """The value of a metric over no rows: 0 for a count, null otherwise."""
    return 0 if metric.kind == "count" or metric.agg in ("count", "count_distinct") else None


def _as_text(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` as its seed file reads back: every column text, `""` and `null` null."""
    buffer = io.StringIO(frame.to_csv(index=False))
    text = pd.read_csv(buffer, dtype=str, keep_default_na=False, na_values=[], na_filter=False)
    return text.apply(lambda column: column.map(_null_or_text)).astype(object)


def _null_or_text(value: Any) -> Any:
    return None if value == "" or str(value).lower() == "null" else value


def known_values(
    semantic: SemanticModel,
    frames: Mapping[str, pd.DataFrame],
    *,
    seed: Optional[int] = None,
    as_of: Optional[str] = None,
) -> KnownValues:
    """Every metric of `semantic` over `frames` (by table key: what the seeds hold)."""
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        connection.execute("set threads = 1")
        names: dict[str, str] = {}
        used = {
            table
            for metric in semantic.metrics.values()
            if metric.table is not None
            for table in (metric.table, *metric.joins)
        }
        for index, key in enumerate(table for table in semantic.entities if table in used):
            if key not in frames:
                raise ValueError(f"no generated rows for table {key!r}, which a metric reads")
            names[key] = f"m2d_{index}"
            text = _as_text(frames[key])
            connection.register(f"{names[key]}_frame", text)
            columns = ", ".join(f"cast({quote(c)} as varchar) as {quote(c)}" for c in text.columns)
            connection.execute(
                f"create table {names[key]} as select {columns} from {names[key]}_frame"
            )
            connection.unregister(f"{names[key]}_frame")
        raw: dict[str, tuple[Any, Optional[dict[str, Any]]]] = {}
        for name, metric in semantic.metrics.items():
            if metric.kind not in ("simple", "count"):
                continue
            relation = names.__getitem__
            total = connection.execute(query(semantic, name, relation)).fetchone()
            by_month = None
            if metric.time is not None:
                rows = connection.execute(query(semantic, name, relation, by_month=True)).fetchall()
                by_month = {
                    month.strftime("%Y-%m"): value for month, value in rows if month is not None
                }
            raw[name] = (total[0] if total else None, by_month)
    finally:
        connection.close()
    return _assemble(semantic, raw, seed=seed, as_of=as_of)


def _assemble(
    semantic: SemanticModel,
    raw: dict[str, tuple[Any, Optional[dict[str, Any]]]],
    *,
    seed: Optional[int],
    as_of: Optional[str],
) -> KnownValues:
    out = KnownValues(model=semantic.name, seed=seed, as_of=as_of)
    for name, metric in semantic.metrics.items():
        if metric.kind in ("simple", "count"):
            total, months = raw[name]
            exact = _exact(metric)
            value = _rounded(total, exact)
            by_month = (
                {month: _rounded(item, exact) for month, item in months.items()}
                if months is not None
                else None
            )
        else:
            value, by_month = _composite(semantic, name, raw)
        out.values[name] = KnownValue(
            name=name,
            kind=metric.kind,
            label=metric.label,
            value=value,
            time=metric.time,
            by_month=by_month,
        )
    return out


def _composite(
    semantic: SemanticModel, name: str, raw: dict[str, tuple[Any, Optional[dict[str, Any]]]]
) -> tuple[Optional[Number], Optional[dict[str, Optional[Number]]]]:
    inputs = semantic.simple_inputs(name)

    def compute(pick: Mapping[str, Any]) -> Optional[Number]:
        with localcontext(Context(prec=40)):
            result = _evaluate(semantic, name, pick)
        return _rounded(result, exact=False)

    total = compute({dep: raw[dep][0] for dep in inputs})
    if not all(raw[dep][1] is not None for dep in inputs):
        return total, None
    months = sorted({month for dep in inputs for month in (raw[dep][1] or {})})
    by_month = {}
    for month in months:
        picked = {}
        for dep in inputs:
            values = raw[dep][1] or {}
            picked[dep] = values[month] if month in values else _empty(semantic.metrics[dep])
        by_month[month] = compute(picked)
    return total, by_month


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def _evaluate(semantic: SemanticModel, name: str, values: Mapping[str, Any]) -> Optional[Decimal]:
    metric = semantic.metrics[name]
    if metric.kind in ("simple", "count"):
        return _decimal(values[name])
    if metric.kind == "ratio":
        assert metric.numerator is not None and metric.denominator is not None
        numerator = _evaluate(semantic, metric.numerator, values)
        denominator = _evaluate(semantic, metric.denominator, values)
        if numerator is None or denominator is None or denominator == 0:
            return None
        return numerator / denominator
    assert metric.node is not None
    return expr.evaluate(
        metric.node, {dep: _evaluate(semantic, dep, values) for dep in metric.inputs}
    )
