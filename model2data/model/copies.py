"""Copied values and the foreign key each is read through (spec 0.10.0, `via`).

A column that references a plain column of a parent, beside a foreign key to that parent,
holds a copy of the value from the row's own parent. When the table has two or more foreign
keys to that parent the document must say which one with `via`. Before spec 0.10.0 the
engine took the first one in document order; `fill_copy_paths` writes that choice in, so a
model made before keeps generating the rows it did.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Union

from model2data.model.document import _build, copy_paths_of
from model2data.model.errors import ModelError
from model2data.model.types import Model
from model2data.model.validate import _json_keys, check, minor_numbers

SPEC = "0.10.0"


def fill_copy_paths(model: Union[Model, Mapping[str, Any]]) -> tuple[Any, list[str]]:
    """The model with a `via` on every copy that needs one and has none, and what was added.

    Each copied value that can be read through more than one foreign key and says none gets the
    first foreign key of its table onto the parent in document order, which is what the engine
    read before spec 0.10.0, so the generated rows do not change. The notes read
    `"fixtures.venue_id -> home_team_id"`. The spec version becomes 0.10.0 only when something
    was added and the document said an earlier one; a model with nothing to add comes back
    equal, with no notes.

    `model` is a `Model` or a document as parsed from YAML or JSON (a dict): the document does
    not have to conform otherwise than for the missing `via`, which is the error this repairs
    (a `ModelError` lists anything else wrong with it). The input is not changed; the result
    is the same kind of value.
    """
    if isinstance(model, Model):
        typed = copy.deepcopy(model)
        added = _fill_model(typed)
        if added and minor_numbers(typed.version) < (0, 10):
            typed.version = SPEC
        return typed, _notes(added)

    document = copy.deepcopy(dict(model))
    errors = [issue for issue in check(document) if issue.is_error]
    if errors:
        raise ModelError(errors)
    typed = _build(_json_keys(document))
    added = _fill_model(typed)
    for table, name, via in added:
        _write_via(document, table, name, via)
    if added and minor_numbers(document.get("model2data")) < (0, 10):
        document["model2data"] = SPEC
    return document, _notes(added)


def _notes(added: list[tuple[str, str, str]]) -> list[str]:
    return [f"{table}.{name} -> {via}" for table, name, via in added]


def _fill_model(model: Model) -> list[tuple[str, str, str]]:
    """Set `via` on every ambiguous copy of `model`; what it set, as (table, column, via)."""
    notes = []
    for (table, name), paths in copy_paths_of(model).items():
        reference = model.tables[table].columns[name].references
        if reference is None or reference.via is not None or len(paths) < 2:
            continue
        model.tables[table].columns[name].references = replace(reference, via=paths[0])
        notes.append((table, name, paths[0]))
    return notes


def _write_via(document: dict, table: str, name: str, via: str) -> None:
    """Put `via` into the `references` of `table.name` in a document, as an object."""
    tables = document["tables"]
    column = tables[table]["columns"][name]
    reference = column["references"]
    if isinstance(reference, str):
        column["references"] = {"to": reference, "via": via}
    else:
        ordered = {"to": reference["to"], "via": via}
        ordered.update({k: v for k, v in reference.items() if k not in ("to", "via")})
        column["references"] = ordered
