"""The foreign keys a model leaves nullable, and how many rows the engine leaves empty.

A foreign key column that is neither a key nor `not_null` is nullable: the engine leaves a
share of its rows without a parent, by default up to a fifth, fewer in a small table
(`generate.faker`), or what its `null_rate` says. That is easy to overlook, and a report that
joins on the column then loses those rows. This is a note, not a warning: a model that means
it is not wrong.
"""

from __future__ import annotations

from typing import Optional

from model2data.model.types import Column, Model, Table

_DEFAULT_ROWS = 100


def nullable_foreign_keys(
    model: Model, *, rows: Optional[int] = None
) -> list[tuple[str, str, str, float]]:
    """Every nullable foreign key column: `(table, column, parent table, share of empty rows)`.

    In document order: a table's columns with `references`, then the columns of its
    `foreign_keys` (each column of a composite foreign key is one entry, as the engine leaves
    each of them empty on its own). `share` is what the engine will use: the column's
    `null_rate` when it has one, else the default, which depends on the table's rows: `rows`
    when given, else `run.rows_per_table`, else `run.rows`, else 100 (the engine's default).

    Left out: a column in a primary key, `pk`, `not_null`, a table's `combinations` columns
    (every combination of parents is a row), a column derived by `derive`, and a column that
    copies a value from its parent (a reference onto a parent's plain column beside a foreign
    key to that parent: its value follows the foreign key).
    """
    found: list[tuple[str, str, str, float]] = []
    for key, table in model.tables.items():
        count = _rows(model, key, rows)
        seen: set[str] = set()
        for name, column in table.columns.items():
            if column.references is None or model.copies(key, name):
                continue
            seen.add(name)
            if _nullable(table, name, column):
                found.append((key, name, column.references.table, _share(column, count)))
        for fk in table.foreign_keys:
            for name in fk.columns:
                if name in seen:
                    continue
                column = table.columns[name]
                seen.add(name)
                if _nullable(table, name, column):
                    found.append((key, name, fk.references, _share(column, count)))
    return found


def _nullable(table: Table, name: str, column: Column) -> bool:
    if column.pk or column.not_null or name in table.combinations:
        return False
    if "derive" in column.generate:
        return False
    return not any(key.kind == "pk" and name in key.columns for key in table.keys)


def _rows(model: Model, key: str, rows: Optional[int]) -> int:
    if rows is not None:
        return rows
    run = model.run
    if run is not None:
        if run.rows_per_table and key in run.rows_per_table:
            return run.rows_per_table[key]
        if run.rows is not None:
            return run.rows
    return _DEFAULT_ROWS


def _share(column: Column, rows: int) -> float:
    """The fraction of `rows` the engine nulls (`generate.faker._null_fraction_for`)."""
    rate = column.generate.get("null_rate")
    if rate is not None:
        return float(rate)
    return max(0.0, min(0.2, 50 / (rows + 50)))
