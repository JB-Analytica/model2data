"""What kind of values a model's column holds, as the metrics spec reads it.

The kinds are the model spec's ("Generation hints"), answered by
`model2data.generate.kinds` so the two specs can never disagree: an enum is an
enum whatever its name contains, and is never numeric, boolean or temporal.
Within numeric, an integer and a decimal are told apart because they are cast
differently in SQL; within temporal, a date and a timestamp, for the same
reason.
"""

from __future__ import annotations

from typing import Literal, Optional

from model2data.generate import kinds
from model2data.model.types import Column, Model

ColumnKind = Literal["enum", "boolean", "integer", "decimal", "date", "timestamp", "text"]


def column_kind(model: Model, column: Column) -> ColumnKind:
    if model.enum_for(column.type) is not None:
        return "enum"
    if kinds.is_boolean_type(column.type):
        return "boolean"
    if kinds.is_integer_type(column.type):
        return "integer"
    if kinds.is_decimal_type(column.type):
        return "decimal"
    temporal = kinds.temporal_kind(column.type)
    if temporal is not None:
        return temporal
    return "text"


def is_numeric(kind: ColumnKind) -> bool:
    return kind in ("integer", "decimal")


def is_temporal(kind: ColumnKind) -> bool:
    return kind in ("date", "timestamp")


def find_column(model: Model, path: str) -> tuple[Optional[str], Optional[Column]]:
    """`(table key, column)` a column path names; the column is None when the table has
    no such column, and both are None when no table matches."""
    table_key, _, name = path.rpartition(".")
    table = model.tables.get(table_key)
    if table is None:
        return None, None
    return table_key, table.columns.get(name)


# The SQL type each kind is read as, in the known values and in the dbt tests.
# DuckDB and Postgres both read every one of them. A decimal is an exact
# decimal rather than a double, so a sum is the same whatever order the rows are
# added in -- which is what keeps a known value byte-identical run after run.
SQL_TYPES: dict[str, str] = {
    "enum": "varchar",
    "text": "varchar",
    "boolean": "boolean",
    "integer": "bigint",
    "decimal": "decimal(38, 9)",
    "date": "date",
    "timestamp": "timestamp",
}
