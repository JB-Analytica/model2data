"""One singular dbt test per metric: its value over the project's models is its known value.

`write_metric_tests` writes `data-tests/metrics/metric_<name>.sql` into a
generated project (`data-tests` is the project's `test-paths`). Each test
computes the metric over the staging models (`stg_<table>`), with the same SQL
the known values were computed with (`model2data.metrics.sql`), and returns a
row -- failing -- unless the result is within the tolerance of the value in
`metric_values.json`. So `dbt build` proves that the metric's logic, run by
the warehouse over the project's own models, gives the number the run says
it should.

The tolerance is `0.000001 + 0.000000001 * |known value|`: the known value is
rounded to 6 decimal places, and a warehouse may add a column of doubles in
another order than DuckDB did. A known value of null passes only when the
metric is null too.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Optional

from model2data.metrics.semantic import SemanticModel
from model2data.metrics.sql import scalar
from model2data.metrics.values import KnownValues, Number

ABSOLUTE_TOLERANCE = Decimal("0.000001")
RELATIVE_TOLERANCE = Decimal("0.000000001")
TEST_DIRECTORY = Path("data-tests") / "metrics"


def tolerance(value: Number) -> Decimal:
    return ABSOLUTE_TOLERANCE + RELATIVE_TOLERANCE * abs(Decimal(value))


def _number(value: Number) -> str:
    return format(Decimal(value), "f")


def metric_test_sql(semantic: SemanticModel, name: str, expected: Optional[Number]) -> str:
    """The singular test for metric `name`, given its known value."""
    metric = semantic.metrics[name]

    def relation(table: str) -> str:
        return f"{{{{ ref('stg_{semantic.entities[table].name}') }}}}"

    value_sql = scalar(semantic, name, relation).replace("\n", "\n  ")
    label = metric.label.replace("\n", " ")
    if expected is None:
        header = (
            f"-- model2data metric `{name}` ({label}): its known value in metric_values.json is\n"
            "-- null (no row to aggregate, or a division by zero), so it must be null here too.\n"
        )
        check = "value is not null"
    else:
        slack = tolerance(expected)
        header = (
            f"-- model2data metric `{name}` ({label}): its value over the staging models must be\n"
            f"-- its known value in metric_values.json, {_number(expected)}, within "
            f"{_number(slack)}.\n"
        )
        check = f"value is null or abs(value - {_number(expected)}) > {_number(slack)}"
    return (
        f"{header}"
        "with actual as (\n"
        f"  select {value_sql} as value\n"
        ")\n"
        "select value as actual_value\n"
        "from actual\n"
        f"where {check}\n"
    )


def write_metric_tests(dest: Path, semantic: SemanticModel, values: KnownValues) -> list[Path]:
    """One test per metric under `dest/data-tests/metrics/`, in metric order; the paths."""
    directory = dest / TEST_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in semantic.metrics:
        path = directory / f"metric_{name}.sql"
        path.write_text(
            metric_test_sql(semantic, name, values.values[name].value), encoding="utf-8"
        )
        written.append(path)
    return written
