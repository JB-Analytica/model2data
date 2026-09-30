"""The generator's table definitions, and DBML read into them.

`TableDef` and `ColumnDef` are what `generate_data_from_dbml` generates from.
`parse_dbml` reads a DBML file into them by way of the model: DBML is
converted to a spec 0.2.0 model (`model2data.model.from_dbml`, built on
`pydbml`) and the model to the generator's inputs (`model2data.model.to_engine`),
so a DBML file and the `.model2data.yml` converted from it generate the same
data. Until 1.8 this module was a hand-written, line-by-line DBML parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ColumnDef:
    name: str
    data_type: str
    settings: set[str] = field(default_factory=set)
    # The column's generation hints (the model's `generate`, flattened), plus
    # `measure` when the model sets it.
    note: Optional[dict] = None
    description: Optional[str] = None
    enum_values: Optional[list[str]] = None
    default: Optional[object] = None


@dataclass
class TableDef:
    # The table key: `orders`, or `raw.orders` outside the default schema.
    name: str
    columns: list[ColumnDef] = field(default_factory=list)
    description: Optional[str] = None
    composite_keys: list[dict] = field(default_factory=list)
    # Table-level hints: `{"role": "fact"}` when the model sets a role.
    note: Optional[dict] = None


# Many-to-many (`<>`) refs generate nothing, so they are not in the refs
# `parse_dbml` returns; the most recent call's are kept here instead, so its
# 2-tuple return stays what every caller already unpacks.
_last_many_to_many_refs: list[dict] = []


def get_many_to_many_refs() -> list[dict]:
    """Return the `<>` refs captured by the most recent parse_dbml() call."""
    return list(_last_many_to_many_refs)


def reset_parse_warnings() -> None:
    """Kept for callers of the line parser; there is nothing to reset."""


def get_parse_warnings() -> list[str]:
    """Always empty since 1.8.

    The line parser this module used to hold skipped what it could not read and
    warned; DBML is now parsed by a grammar, which reads a file or refuses it
    with the line and column it stopped at (`ModelError`).
    """
    return []


def parse_dbml(dbml_path: Path) -> tuple[dict[str, TableDef], list[dict]]:
    """Read a DBML file into the generator's `(tables, refs)`.

    Raises `model2data.model.ModelError` (a `ValueError`) when the file is not
    DBML that converts to a valid model, listing every issue found.
    """
    from model2data.model import from_dbml, to_engine
    from model2data.model.reader import read_text

    inputs = to_engine(from_dbml(read_text(Path(dbml_path))))
    _last_many_to_many_refs[:] = inputs.many_to_many
    return inputs.tables, inputs.refs
