"""Between a parsed document (plain dicts and lists) and the typed `Model`."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from model2data.model.errors import ModelError
from model2data.model.types import (
    Column,
    Enum,
    ForeignKey,
    Group,
    Incremental,
    Key,
    Model,
    Reference,
    Relationship,
    Run,
    Shape,
    Table,
)
from model2data.model.validate import _json_keys, check


def from_dict(data: Mapping[str, Any]) -> Model:
    """The model a parsed document describes, or `ModelError` listing every error in it.

    `data` is what a YAML or JSON reader returns for the document: the studio
    calls this with the JSON it holds, `load` with what it read from a file.
    The warnings of a document that conforms are on the model's `warnings`.
    """
    issues = check(data)
    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    if errors:
        raise ModelError(errors, warnings)
    model = _build(_json_keys(data))
    model.warnings = warnings
    return model


def _extensions(mapping: Mapping) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in mapping.items()
        if isinstance(key, str) and key.startswith("x-")
    }


def _build(data: Mapping) -> Model:
    return Model(
        version=data["model2data"],
        name=data.get("name"),
        description=data.get("description"),
        enums={name: _enum(members) for name, members in (data.get("enums") or {}).items()},
        tables={key: _table(table) for key, table in data["tables"].items()},
        relationships=[
            Relationship(list(item["many_to_many"]), item.get("description"))
            for item in data.get("relationships") or []
        ],
        groups={
            name: Group(list(group["tables"]), group.get("color"), group.get("description"))
            for name, group in (data.get("groups") or {}).items()
        },
        run=_run(data["run"]) if "run" in data else None,
        extensions=_extensions(data),
    )


def _member(value: Any) -> str:
    return value if isinstance(value, str) else str(value)


def _enum(members: Any) -> Enum:
    if isinstance(members, Mapping):
        return Enum(
            members=[_member(member) for member in members],
            descriptions={_member(member): note for member, note in members.items()},
        )
    return Enum(members=[_member(member) for member in members])


def _table(table: Mapping) -> Table:
    return Table(
        columns={name: _column(column) for name, column in table["columns"].items()},
        description=table.get("description"),
        color=table.get("color"),
        role=table.get("role"),
        grain=list(table["grain"]) if table.get("grain") is not None else None,
        incremental=_incremental(table["incremental"]) if "incremental" in table else None,
        keys=[
            Key("pk" if "pk" in key else "unique", list(key.get("pk") or key.get("unique")))
            for key in table.get("keys") or []
        ],
        foreign_keys=[
            ForeignKey(
                list(fk["columns"]),
                fk["references"],
                list(fk["to_columns"]),
                bool(fk.get("one_to_one", False)),
            )
            for fk in table.get("foreign_keys") or []
        ],
        extensions=_extensions(table),
    )


def _incremental(incremental: Mapping) -> Incremental:
    return Incremental(
        new_per_day=incremental.get("new_per_day"),
        update_rate=incremental.get("update_rate"),
        changes=list(incremental["changes"]) if incremental.get("changes") is not None else None,
        updated_at=incremental.get("updated_at"),
    )


def incremental_to_dict(incremental: Incremental) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _put(out, "new_per_day", incremental.new_per_day)
    _put(out, "update_rate", incremental.update_rate)
    _put(out, "changes", list(incremental.changes) if incremental.changes is not None else None)
    _put(out, "updated_at", incremental.updated_at)
    return out


def _column(column: Any) -> Column:
    if isinstance(column, str):
        return Column(type=column)
    reference = column.get("references")
    if isinstance(reference, Mapping):
        reference = Reference(reference["to"], bool(reference.get("one_to_one", False)))
    elif isinstance(reference, str):
        reference = Reference(reference)
    return Column(
        type=column["type"],
        pk=bool(column.get("pk", False)),
        unique=bool(column.get("unique", False)),
        not_null=bool(column.get("not_null", False)),
        increment=bool(column.get("increment", False)),
        default=column.get("default"),
        description=column.get("description"),
        references=reference,
        measure=column.get("measure"),
        generate=copy.deepcopy(dict(column.get("generate") or {})),
        extensions=_extensions(column),
    )


def _run(run: Mapping) -> Run:
    shape = run.get("shape")
    return Run(
        rows=run.get("rows"),
        rows_per_table=dict(run["rows_per_table"]) if "rows_per_table" in run else None,
        seed=run.get("seed"),
        table_seeds=dict(run["table_seeds"]) if "table_seeds" in run else None,
        as_of=run.get("as_of"),
        locale=run.get("locale"),
        shape=Shape(**shape) if shape is not None else None,
    )


# ---------------------------------------------------------------------------
# Model -> document
# ---------------------------------------------------------------------------
def _put(out: dict, key: str, value: Any) -> None:
    """Set `key` unless `value` is its default (None, False, empty)."""
    if value is None or value is False or value == {} or value == []:
        return
    out[key] = value


def to_dict(model: Model) -> dict[str, Any]:
    """The document a model is: plain dicts, lists and scalars in canonical key order.

    Every column is written as a mapping (`{"type": "int"}`, never the string
    shorthand) and a value at its default is left out. `from_dict(to_dict(m))`
    is `m`.
    """
    out: dict[str, Any] = {"model2data": model.version}
    _put(out, "name", model.name)
    _put(out, "description", model.description)
    _put(out, "enums", {name: enum_to_value(enum) for name, enum in model.enums.items()})
    out["tables"] = {key: table_to_dict(table) for key, table in model.tables.items()}
    _put(
        out,
        "relationships",
        [
            {"many_to_many": list(item.many_to_many)}
            | ({"description": item.description} if item.description is not None else {})
            for item in model.relationships
        ],
    )
    groups = {}
    for name, group in model.groups.items():
        entry: dict[str, Any] = {"tables": list(group.tables)}
        _put(entry, "color", group.color)
        _put(entry, "description", group.description)
        groups[name] = entry
    _put(out, "groups", groups)
    if model.run is not None:
        out["run"] = run_to_dict(model.run)
    out.update(copy.deepcopy(model.extensions))
    return out


def enum_to_value(enum: Enum) -> Any:
    if enum.descriptions is None:
        return list(enum.members)
    return {member: enum.descriptions.get(member) for member in enum.members}


def table_to_dict(table: Table) -> dict[str, Any]:
    out: dict[str, Any] = {}
    _put(out, "description", table.description)
    _put(out, "color", table.color)
    _put(out, "role", table.role)
    _put(out, "grain", list(table.grain) if table.grain is not None else None)
    if table.incremental is not None:
        out["incremental"] = incremental_to_dict(table.incremental)
    out["columns"] = {name: column_to_dict(column) for name, column in table.columns.items()}
    _put(out, "keys", [{key.kind: list(key.columns)} for key in table.keys])
    foreign_keys = []
    for fk in table.foreign_keys:
        entry: dict[str, Any] = {
            "columns": list(fk.columns),
            "references": fk.references,
            "to_columns": list(fk.to_columns),
        }
        _put(entry, "one_to_one", fk.one_to_one)
        foreign_keys.append(entry)
    _put(out, "foreign_keys", foreign_keys)
    out.update(copy.deepcopy(table.extensions))
    return out


def column_to_dict(column: Column) -> dict[str, Any]:
    out: dict[str, Any] = {"type": column.type}
    _put(out, "pk", column.pk)
    _put(out, "unique", column.unique)
    _put(out, "not_null", column.not_null)
    _put(out, "increment", column.increment)
    if column.default is not None:
        out["default"] = column.default
    _put(out, "description", column.description)
    if column.references is not None:
        out["references"] = (
            {"to": column.references.to, "one_to_one": True}
            if column.references.one_to_one
            else column.references.to
        )
    if column.measure is not None:
        out["measure"] = column.measure
    _put(out, "generate", copy.deepcopy(column.generate))
    out.update(copy.deepcopy(column.extensions))
    return out


def run_to_dict(run: Run) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("rows", "rows_per_table", "seed", "table_seeds", "as_of", "locale"):
        value = getattr(run, name)
        if value is not None:
            out[name] = copy.deepcopy(value)
    if run.shape is not None:
        out["shape"] = {
            name: getattr(run.shape, name)
            for name in ("business_hours", "growth", "seasonality", "skew")
            if getattr(run.shape, name) is not None
        }
    return out
