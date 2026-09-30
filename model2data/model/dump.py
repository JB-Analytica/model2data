"""Writing a model as its `.model2data.yml` document, in the canonical style.

The style is the reference example's (model2data/spec/examples/): a
`yaml-language-server` schema comment first, then `model2data`, `name`,
`description`, and the sections `enums`, `tables`, `relationships`, `groups`,
`run` separated by blank lines, with a blank line between tables.

- A column that is only a type is written as its type: `country: country`.
- A column without `generate`, `description` or a nested extension fits on one
  line as a flow mapping: `id: {type: bigint, pk: true}`.
- Any other column is a block mapping, and its `generate` is a flow mapping when
  every hint is a plain value, a block mapping when one is itself a mapping.
- A scalar is quoted only when a plain one would not read back as the same
  value under the spec's YAML profile, or would read as a boolean to a YAML
  1.1 reader (`no`, `on`, `NO`), and then with double quotes.
- A string ending in a newline is written as a folded (`>`) block when it is one
  line, and a literal (`|`) block when it is several.

`dump` writes by hand rather than through `yaml.dump`, which can do neither
the mixed flow/block layout nor the quoting rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from model2data.model._yaml import scalar
from model2data.model.document import column_to_dict, to_dict
from model2data.model.types import Model
from model2data.model.validate import SCHEMA_URL

WIDTH = 100
_INDENT = "  "
_SECTIONS = ("enums", "tables", "relationships", "groups", "run")


def dump(model: Model) -> str:
    """The model as YAML text in the canonical style; `load(dump(m)) == m`."""
    document = to_dict(model)
    lines = [f"# yaml-language-server: $schema={SCHEMA_URL}"]
    for key in ("model2data", "name", "description"):
        if key in document:
            lines += _entry(key, document[key], 0)
    for section in _SECTIONS:
        if section not in document:
            continue
        lines.append("")
        if section == "tables":
            lines += _tables(model)
        elif section == "relationships":
            lines.append("relationships:")
            lines += _block_list(document[section], 1)
        else:
            lines.append(f"{section}:")
            lines += _block_mapping(document[section], 1)
    extensions = {key: value for key, value in document.items() if key.startswith("x-")}
    if extensions:
        lines.append("")
        lines += _block_mapping(extensions, 0)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Tables and columns
# ---------------------------------------------------------------------------
def _tables(model: Model) -> list[str]:
    lines = ["tables:"]
    for position, (key, table) in enumerate(model.tables.items()):
        if position:
            lines.append("")
        lines.append(f"{_INDENT}{_key(key)}:")
        pad = _INDENT * 2
        for name, value in (
            ("description", table.description),
            ("color", table.color),
            ("role", table.role),
        ):
            if value is not None:
                lines += _entry(name, value, 2)
        lines.append(f"{pad}columns:")
        for name, column in table.columns.items():
            lines += _column(name, column_to_dict(column), 3)
        if table.keys:
            lines.append(f"{pad}keys:")
            lines += _block_list([{key.kind: list(key.columns)} for key in table.keys], 3)
        if table.foreign_keys:
            lines.append(f"{pad}foreign_keys:")
            entries = []
            for fk in table.foreign_keys:
                entry: dict[str, Any] = {
                    "columns": list(fk.columns),
                    "references": fk.references,
                    "to_columns": list(fk.to_columns),
                }
                if fk.one_to_one:
                    entry["one_to_one"] = True
                entries.append(entry)
            lines += _block_list(entries, 3, flow_items=False)
        for name, value in table.extensions.items():
            lines += _entry(name, value, 2)
    return lines


def _column(name: str, column: dict[str, Any], level: int) -> list[str]:
    pad = _INDENT * level
    if list(column) == ["type"]:
        return [f"{pad}{_key(name)}: {scalar(column['type'])}"]
    simple = "generate" not in column and "description" not in column
    if simple:
        flow = _flow(column)
        if flow is not None and len(pad) + len(_key(name)) + 2 + len(flow) <= WIDTH:
            return [f"{pad}{_key(name)}: {flow}"]
    lines = [f"{pad}{_key(name)}:"]
    for key, value in column.items():
        if key == "generate":
            lines += _generate(value, level + 1)
        else:
            lines += _entry(key, value, level + 1)
    return lines


def _generate(generate: dict[str, Any], level: int) -> list[str]:
    pad = _INDENT * level
    if all(not isinstance(value, (Mapping, list)) for value in generate.values()):
        flow = _flow(generate)
        if flow is not None and len(pad) + len("generate: ") + len(flow) <= WIDTH:
            return [f"{pad}generate: {flow}"]
    return [f"{pad}generate:", *_block_mapping(generate, level + 1)]


# ---------------------------------------------------------------------------
# Generic values
# ---------------------------------------------------------------------------
def _key(key: Any) -> str:
    return scalar(key)


def _flow(value: Any) -> Optional[str]:
    """`value` as one line of flow YAML, or None when it cannot be one (a multi-line string)."""
    if isinstance(value, Mapping):
        parts = []
        for key, item in value.items():
            text = _flow(item)
            if text is None:
                return None
            parts.append(f"{scalar(key, flow=True)}: {text}")
        return "{" + ", ".join(parts) + "}"
    if isinstance(value, list):
        items = [_flow(item) for item in value]
        if any(item is None for item in items):
            return None
        return "[" + ", ".join(item for item in items if item is not None) + "]"
    if isinstance(value, str) and "\n" in value:
        return None
    return scalar(value, flow=True)


def _entry(key: Any, value: Any, level: int) -> list[str]:
    """`key: value` at `level`, on one line when it fits and a block otherwise."""
    pad = _INDENT * level
    head = f"{pad}{_key(key)}:"
    if isinstance(value, str) and "\n" in value:
        block = _block_scalar(value, level + 1)
        if block is not None:
            return [f"{head} {block[0]}", *block[1:]]
    if not isinstance(value, (Mapping, list)):
        return [f"{head} {scalar(value)}"]
    flow = _flow(value)
    if flow is not None and (not value or len(head) + 1 + len(flow) <= WIDTH):
        return [f"{head} {flow}"]
    if isinstance(value, Mapping):
        return [head, *_block_mapping(value, level + 1)]
    return [head, *_block_list(value, level + 1)]


def _block_mapping(mapping: Mapping, level: int) -> list[str]:
    lines = []
    for key, value in mapping.items():
        lines += _entry(key, value, level)
    return lines


def _block_list(items: list, level: int, *, flow_items: bool = True) -> list[str]:
    """`- item` lines; an item that is a mapping stays a block when `flow_items` is False."""
    pad = _INDENT * level
    lines: list[str] = []
    for item in items:
        container = isinstance(item, (Mapping, list))
        if flow_items or not isinstance(item, Mapping):
            flow = _flow(item)
            if flow is not None and (not container or len(pad) + 2 + len(flow) <= WIDTH):
                lines.append(f"{pad}- {flow}")
                continue
        if isinstance(item, Mapping) and item:
            entries = _block_mapping(item, level + 1)
            lines.append(f"{pad}- {entries[0][len(pad) + len(_INDENT) :]}")
            lines += entries[1:]
        elif isinstance(item, list) and item:
            lines.append(f"{pad}-")
            lines += _block_list(item, level + 1)
        elif isinstance(item, str) and (block := _block_scalar(item, level + 1)) is not None:
            lines.append(f"{pad}- {block[0]}")
            lines += block[1:]
        else:
            lines.append(f"{pad}- {scalar(item) if not container else _flow(item)}")
    return lines


def _block_scalar(text: str, level: int) -> Optional[list[str]]:
    """A multi-line string as a `>` or `|` block, or None when only quoting can hold it."""
    if text.startswith((" ", "\n")) or any(
        not (char.isprintable() or char in "\n\t") for char in text
    ):
        return None
    pad = _INDENT * level
    body = text.rstrip("\n")
    trailing = len(text) - len(body)
    lines = body.split("\n")
    if any(line != line.rstrip() for line in lines):
        return None
    if trailing == 1 and len(lines) == 1:
        return [">", f"{pad}{body}"]
    indicator = {0: "|-", 1: "|"}.get(trailing, "|+")
    out = [indicator]
    out += [f"{pad}{line}" if line else "" for line in lines]
    out += [""] * (trailing - 1 if trailing > 1 else 0)
    return out
