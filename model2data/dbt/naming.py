"""A model's table keys as the seed and model names they get in a dbt project.

The CLI and `model2data.defects` both name tables this way, so a test name the
defects report predicts is the one dbt gives the test in the generated project.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from model2data.parse.dbml import ColumnDef, TableDef


def dbt_identifier(key: str) -> str:
    """A table key as the seed and model name it gets in the dbt project.

    Spec 0.2.0 leaves a name as written (`user accounts`, `raw.orders`) and has
    a consumer normalise it where it reaches a file or a dbt identifier, to
    `[a-z0-9_]`. Underscores are kept as they are, so `_dlt_loads` and
    `stories__kids` stay what they were.
    """
    cleaned = re.sub(r"[^0-9a-z_]+", "_", key.lower())
    if cleaned.strip("_") == "":
        cleaned = "table"
    elif cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


def dbt_names(keys: Iterable[str]) -> dict[str, str]:
    """Each table key's dbt identifier; ValueError when two keys normalise alike."""
    names: dict[str, str] = {}
    owners: dict[str, str] = {}
    for key in keys:
        name = dbt_identifier(key)
        if name in owners:
            raise ValueError(
                f"Tables {owners[name]!r} and {key!r} would both be the dbt seed {name!r}. "
                "Rename one of them."
            )
        owners[name] = key
        names[key] = name
    return names


def for_dbt(
    tables: Mapping[str, TableDef], refs: list[dict], names: Mapping[str, str]
) -> tuple[dict[str, TableDef], list[dict]]:
    """`(tables, refs)` renamed to their dbt names, as `generate_dbt_yml` takes them."""
    dbt_tables = {
        names[key]: replace(
            table,
            name=names[key],
            columns=[_hints_renamed(column, names) for column in table.columns],
        )
        for key, table in tables.items()
    }
    dbt_refs = [
        {
            **ref,
            "source_table": names.get(ref["source_table"], ref["source_table"]),
            "target_table": names.get(ref["target_table"], ref["target_table"]),
        }
        for ref in refs
    ]
    return dbt_tables, dbt_refs


def _hints_renamed(column: ColumnDef, names: Mapping[str, str]) -> ColumnDef:
    """`column` with the tables its hints name (`rollup`'s `of`, `after`) under their dbt names."""
    if not isinstance(column.note, dict):
        return column
    note = dict(column.note)
    spec = note.get("rollup")
    if isinstance(spec, dict) and spec.get("of") in names:
        note["rollup"] = {**spec, "of": names[spec["of"]]}
    after = note.get("after")
    if isinstance(after, list):
        note["after"] = [_after_renamed(entry, names) for entry in after]
    elif after is not None:
        note["after"] = _after_renamed(after, names)
    return column if note == column.note else replace(column, note=note)


def _after_renamed(entry: Any, names: Mapping[str, str]) -> Any:
    """An `after` entry naming a parent's column (`raw.users.created_at`), for dbt."""
    if not isinstance(entry, str) or "." not in entry:
        return entry
    table, _, column = entry.rpartition(".")
    return f"{names[table]}.{column}" if table in names else entry
