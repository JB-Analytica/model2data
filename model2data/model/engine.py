"""A model as the generator's inputs: `(tables, refs)` and the run settings.

`generate_data_from_dbml(tables, refs, ...)` predates the model document and
takes `TableDef`s and child-first ref dicts; this module is the one place a
`Model` becomes those, so the DBML front-end and a `.model2data.yml` file can
only ever generate the same thing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from model2data.generate import kinds
from model2data.model.types import Column, Model, Run, Table
from model2data.parse.dbml import ColumnDef, TableDef


@dataclass
class EngineInputs:
    """What the generator reads from a model.

    `tables` and `refs` are exactly what `generate_data_from_dbml` takes:
    tables keyed (and named) by table key -- `orders`, or `raw.orders` outside
    the default schema -- in document order, and one child-first ref dict per
    foreign-key column pair. A one-to-one ref carries `"one_to_one": True`.
    `many_to_many` holds the `relationships`, which generate nothing. `run` is
    the document's `run` (empty when it has none).
    """

    tables: dict[str, TableDef]
    refs: list[dict]
    many_to_many: list[dict] = field(default_factory=list)
    run: Run = field(default_factory=Run)


def to_engine(model: Model) -> EngineInputs:
    """The generator's inputs for `model`, which must be valid (as `load` returns it)."""
    refs: list[dict] = []
    tables: dict[str, TableDef] = {}
    for key, table in model.tables.items():
        tables[key] = _table_def(model, key, table)
        for name, column in table.columns.items():
            if column.references is None:
                continue
            ref: dict[str, Any] = {
                "source_table": key,
                "source_column": name,
                "target_table": column.references.table,
                "target_column": column.references.column,
            }
            if column.references.one_to_one:
                ref["one_to_one"] = True
            refs.append(ref)
        for fk in table.foreign_keys:
            for child, parent in zip(fk.columns, fk.to_columns, strict=True):
                ref = {
                    "source_table": key,
                    "source_column": child,
                    "target_table": fk.references,
                    "target_column": parent,
                }
                if fk.one_to_one:
                    ref["one_to_one"] = True
                refs.append(ref)
    many_to_many = []
    for relationship in model.relationships:
        left, right = relationship.many_to_many
        left_table, _, left_column = left.rpartition(".")
        right_table, _, right_column = right.rpartition(".")
        many_to_many.append(
            {
                "source_table": left_table,
                "source_column": left_column,
                "target_table": right_table,
                "target_column": right_column,
            }
        )
    return EngineInputs(tables, refs, many_to_many, model.run or Run())


def _table_def(model: Model, key: str, table: Table) -> TableDef:
    pk_columns = [name for name, column in table.columns.items() if column.pk]
    pk_key = next((k for k in table.keys if k.kind == "pk"), None)
    # One `pk: true` column is the primary key; several are one composite key,
    # exactly as if listed under `keys` (spec 0.2.0, "Keys").
    single_pk = pk_columns[0] if len(pk_columns) == 1 and pk_key is None else None
    composite_keys: list[dict] = []
    if len(pk_columns) > 1 and pk_key is None:
        composite_keys.append({"columns": pk_columns, "type": "pk"})
    composite_keys += [{"columns": list(k.columns), "type": k.kind} for k in table.keys]

    one_to_one_children = {
        name
        for name, column in table.columns.items()
        if column.references is not None and column.references.one_to_one
    }
    columns = [
        _column_def(model, name, column, name == single_pk, name in one_to_one_children)
        for name, column in table.columns.items()
    ]
    return TableDef(
        name=key,
        columns=columns,
        description=table.description,
        composite_keys=composite_keys,
        note=_table_hints(table),
    )


def _table_hints(table: Table) -> Optional[dict]:
    """The table's `role` and `grain` as its flat note, None when it has neither.

    The generator reads neither; consumers of the engine's inputs (the dbt
    export's grain test) do.
    """
    hints: dict[str, Any] = {}
    if table.role is not None:
        hints["role"] = table.role
    if table.grain:
        hints["grain"] = list(table.grain)
    return hints or None


def _column_def(model: Model, name: str, column: Column, pk: bool, one_to_one: bool) -> ColumnDef:
    settings = set()
    if pk:
        settings.add("pk")
    if column.not_null:
        settings.add("not null")
    # A one-to-one child holds each parent at most once: unique, in the
    # generator's terms (spec 0.2.0, `references.one_to_one`).
    if column.unique or one_to_one:
        settings.add("unique")
    if column.increment:
        settings.add("increment")
    enum = model.enum_for(column.type)
    return ColumnDef(
        name=name,
        data_type=column.type,
        settings=settings,
        note=_hints(column, integer=enum is None and kinds.is_integer_type(column.type)),
        description=column.description,
        enum_values=list(enum.members) if enum is not None else None,
        default=column.default,
    )


def _hints(column: Column, *, integer: bool) -> Optional[dict]:
    """`generate` as the generator's flat hint dict, plus `measure` for consumers.

    `distribution: {kind: lognormal, median: 120}` becomes
    `{"distribution": "lognormal", "median": 120}`, the shape the generator
    has always read from a note. Extensions (`x-*`) are not hints.
    """
    hints: dict[str, Any] = {}
    for key, value in column.generate.items():
        if key.startswith("x-"):
            continue
        if key == "distribution" and isinstance(value, dict):
            hints["distribution"] = value["kind"]
            hints.update({k: v for k, v in value.items() if k != "kind"})
        elif key in ("min", "max") and integer and isinstance(value, float):
            hints[key] = int(value)  # validated whole
        else:
            hints[key] = value
    if column.measure is not None:
        hints["measure"] = column.measure
    return hints or None


def run_as_of(run: Run) -> Optional[date]:
    """`run.as_of` as a date, or None when the run leaves it to the reader."""
    return date.fromisoformat(run.as_of) if run.as_of else None
