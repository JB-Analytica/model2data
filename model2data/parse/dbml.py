from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# -------------------------------
# Dataclasses
# -------------------------------


@dataclass
class ColumnDef:
    name: str
    data_type: str
    settings: set[str] = field(default_factory=set)
    note: Optional[dict] = None
    description: Optional[str] = None
    enum_values: Optional[list[str]] = None
    default: Optional[object] = None


@dataclass
class TableDef:
    name: str
    columns: list[ColumnDef] = field(default_factory=list)
    description: Optional[str] = None
    composite_keys: list[dict] = field(default_factory=list)


# -------------------------------
# Helpers
# -------------------------------


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _parse_default_value(raw: str) -> Optional[object]:
    """Parse a `default:` setting value into a native Python type.

    Backtick expressions (e.g. `now()`) are SQL expressions, not static
    values, so they are deliberately left unparsed (returns None).
    """
    raw = raw.strip()
    if not raw:
        return None
    if raw.startswith("`") and raw.endswith("`"):
        return None
    if (raw.startswith("'") and raw.endswith("'")) or (raw.startswith('"') and raw.endswith('"')):
        return _strip_quotes(raw)
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return None


# Matches the "> table.column" / "< table.column" / "<> table.column" part of
# an inline `ref: > table.column` column setting. Table/column names may be
# bare, double-quoted, or backtick-quoted, mirroring the standalone `Ref {}`
# block parser's identifier handling.
_INLINE_REF_RE = re.compile(r"^(<>|[<>])\s*(\".*?\"|`.*?`|[\w]+)\.(\".*?\"|`.*?`|[\w]+)$")


def _parse_inline_ref(value: str) -> Optional[dict]:
    """Parse the value of a `ref:` column setting into operator + target table/column."""
    match = _INLINE_REF_RE.match(value.strip())
    if not match:
        return None
    operator, target_table, target_column = match.groups()
    return {
        "operator": operator,
        "target_table": _strip_quotes(target_table),
        "target_column": _strip_quotes(target_column),
    }


def _parse_column_settings(
    raw: Optional[str],
) -> tuple[set[str], Optional[dict], Optional[str], Optional[object], Optional[dict]]:
    """Parse column settings, extracting note/description/default/inline ref if present."""
    if not raw:
        return set(), None, None, None, None

    settings = set()
    note_dict = None
    description = None
    default_value = None
    inline_ref = None

    # Split by comma, but be careful with nested structures
    parts = []
    current = []
    depth = 0

    for char in raw:
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)

    if current:
        parts.append("".join(current).strip())

    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Check if this is a note
        if part.lower().startswith("note:"):
            note_str = part[5:].strip()
            # Remove surrounding quotes if present
            note_str = _strip_quotes(note_str)
            try:
                # Try to parse as JSON (min/max convention)
                note_dict = json.loads(note_str)
            except json.JSONDecodeError:
                # Otherwise treat the raw text as a plain-text description
                if note_str:
                    description = note_str
        elif part.lower().startswith("default:"):
            default_str = part[len("default:") :].strip()
            default_value = _parse_default_value(default_str)
        elif part.lower().startswith("ref:"):
            inline_ref = _parse_inline_ref(part[len("ref:") :])
        else:
            # Regular setting (pk, not null, unique, etc.)
            settings.add(part.strip("'").strip('"').lower())

    return settings, note_dict, description, default_value, inline_ref


# Many-to-many (`<>`) refs are captured but not fed into FK-based generation.
# Exposed out-of-band (mirroring generate.faker's stats-tracking pattern) so
# `parse_dbml`'s existing 2-tuple return signature stays backward compatible.
_last_many_to_many_refs: list[dict] = []


def get_many_to_many_refs() -> list[dict]:
    """Return the `<>` refs captured by the most recent parse_dbml() call."""
    return list(_last_many_to_many_refs)


def normalize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()
    if not cleaned:
        cleaned = "table"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


def _parse_composite_key_line(cleaned: str) -> Optional[dict]:
    """Parse an `indexes {}` block line like `(a, b) [pk]` or `(a) [unique]`."""
    match = re.match(r"^\(([^)]+)\)\s*(?:\[(.+)\])?$", cleaned)
    if not match:
        return None

    columns = [_strip_quotes(c) for c in match.group(1).split(",")]
    columns = [c for c in columns if c]
    if not columns:
        return None

    settings_str = match.group(2) or ""
    settings_tokens = [s.strip().strip("'").strip('"').lower() for s in settings_str.split(",")]

    key_type = None
    if "pk" in settings_tokens:
        key_type = "pk"
    elif "unique" in settings_tokens:
        key_type = "unique"

    if key_type is None:
        return None

    return {"columns": columns, "type": key_type}


def parse_dbml(dbml_path: Path) -> tuple[dict[str, TableDef], list[dict]]:
    text = dbml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tables: dict[str, TableDef] = {}
    refs: list[dict] = []
    enums: dict[str, list[str]] = {}
    many_to_many_refs: list[dict] = []

    current_table: Optional[TableDef] = None
    current_enum_name: Optional[str] = None
    current_enum_values: list[str] = []
    in_indexes_block = False
    in_note_block = False
    note_block_lines: list[str] = []
    in_ref_block = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        cleaned = line.split("//", 1)[0].strip()
        if not cleaned:
            continue

        # ----------------------
        # ENUM PARSING
        # ----------------------
        if current_enum_name is not None:
            if cleaned.startswith("}"):
                enums[current_enum_name.lower()] = list(current_enum_values)
                current_enum_name = None
                current_enum_values = []
                continue
            enum_val = cleaned.split("[", 1)[0].strip()
            enum_val = _strip_quotes(enum_val)
            if enum_val:
                current_enum_values.append(enum_val)
            continue

        if current_table is None and re.match(r"^enum\s+", cleaned, re.IGNORECASE):
            enum_name_section = (
                re.sub(r"^enum\s+", "", cleaned, flags=re.IGNORECASE).split("{", 1)[0].strip()
            )
            current_enum_name = _strip_quotes(enum_name_section)
            current_enum_values = []
            continue

        if in_note_block:
            if "'''" in cleaned:
                # Closing (or opening+closing) triple-quote in this line.
                before = cleaned.split("'''", 1)[0].strip()
                if before:
                    note_block_lines.append(_strip_quotes(before))
                in_note_block = False
                text_note = "\n".join(note_block_lines).strip()
                if current_table is not None and text_note:
                    current_table.description = text_note
                note_block_lines = []
                continue
            if cleaned == "}":
                text_note = "\n".join(note_block_lines).strip()
                if current_table is not None and text_note:
                    current_table.description = text_note
                note_block_lines = []
                in_note_block = False
                continue
            note_block_lines.append(_strip_quotes(cleaned))
            continue

        # ----------------------
        # TABLE PARSING
        # ----------------------
        if cleaned.lower().startswith("table "):
            table_name_section = cleaned[6:].split("{", 1)[0].strip()
            if "[" in table_name_section:
                table_name_section = table_name_section.split("[", 1)[0].strip()
            table_name = _strip_quotes(table_name_section)
            current_table = TableDef(name=table_name)
            continue

        if current_table:
            if cleaned.startswith("indexes"):
                in_indexes_block = True
                continue
            if in_indexes_block:
                if cleaned.endswith("}"):
                    in_indexes_block = False
                    inner = cleaned[:-1].strip()
                    if inner:
                        key = _parse_composite_key_line(inner)
                        if key:
                            current_table.composite_keys.append(key)
                    continue
                key = _parse_composite_key_line(cleaned)
                if key:
                    current_table.composite_keys.append(key)
                continue
            if cleaned.startswith("}"):
                tables[current_table.name] = current_table
                current_table = None
                continue
            if cleaned.lower().startswith("note:"):
                note_str = cleaned.split(":", 1)[1].strip()
                if note_str.startswith("'''"):
                    remainder = note_str[3:]
                    if remainder.endswith("'''") and len(remainder) >= 3:
                        text_note = remainder[:-3].strip()
                        if text_note:
                            current_table.description = text_note
                    else:
                        in_note_block = True
                        note_block_lines = [remainder.strip()] if remainder.strip() else []
                    continue
                note_str = _strip_quotes(note_str.strip("'"))
                if note_str:
                    current_table.description = note_str
                continue
            # Multi-line table note: `Note {` ... `}` (not a column definition)
            if cleaned.startswith("Note") and cleaned.rstrip().endswith("{"):
                in_note_block = True
                note_block_lines = []
                continue

            col_match = re.match(
                r'^(".*?"|`.*?`|[A-Za-z_][\w]*)\s+(.+?)(?:\s+\[(.+)\])?$',
                cleaned,
            )
            if not col_match:
                continue

            col_name = _strip_quotes(col_match.group(1))
            col_type = col_match.group(2).strip()

            # 🚨 Reject invalid / sentence-like column definitions
            if len(col_type.split()) > 3:
                continue

            settings, note_dict, description, default_value, inline_ref = _parse_column_settings(
                col_match.group(3)
            )

            current_table.columns.append(
                ColumnDef(
                    name=col_name,
                    data_type=col_type,
                    settings=settings,
                    note=note_dict,
                    description=description,
                    default=default_value,
                )
            )

            if inline_ref is not None:
                target_table = inline_ref["target_table"]
                target_column = inline_ref["target_column"]

                if inline_ref["operator"] == "<>":
                    many_to_many_refs.append(
                        {
                            "source_table": current_table.name,
                            "source_column": col_name,
                            "target_table": target_table,
                            "target_column": target_column,
                        }
                    )
                elif inline_ref["operator"] == "<":
                    # "this column is referenced by target.column": the
                    # target is the actual FK-holding (child) side, so it
                    # becomes source; this column becomes target, mirroring
                    # the standalone `Ref { a < b }` swap below.
                    refs.append(
                        {
                            "source_table": target_table,
                            "source_column": target_column,
                            "target_table": current_table.name,
                            "target_column": col_name,
                        }
                    )
                else:
                    refs.append(
                        {
                            "source_table": current_table.name,
                            "source_column": col_name,
                            "target_table": target_table,
                            "target_column": target_column,
                        }
                    )

            continue

        # ----------------------
        # REF BLOCK START
        # ----------------------
        if cleaned.startswith("Ref"):
            in_ref_block = True
            continue

        if in_ref_block:
            if cleaned.startswith("}"):
                in_ref_block = False
                continue

            # Match: "table"."column" > "table"."column"
            ref_match = re.match(
                r'(".*?"|`.*?`|[\w]+)\.(".*?"|`.*?`|[\w]+)\s*(<>|[<>])\s*'
                r'(".*?"|`.*?`|[\w]+)\.(".*?"|`.*?`|[\w]+)',
                cleaned,
            )
            if not ref_match:
                continue

            left_table, left_column, operator, right_table, right_column = ref_match.groups()

            if operator == "<>":
                many_to_many_refs.append(
                    {
                        "source_table": _strip_quotes(left_table),
                        "source_column": _strip_quotes(left_column),
                        "target_table": _strip_quotes(right_table),
                        "target_column": _strip_quotes(right_column),
                    }
                )
                continue

            # The regex only ever captures "<>", "<", or ">"; "<>" is handled
            # above, so any remaining operator here is "<" or ">".
            if operator == "<":
                left_table, right_table = right_table, left_table
                left_column, right_column = right_column, left_column

            refs.append(
                {
                    "source_table": _strip_quotes(left_table),
                    "source_column": _strip_quotes(left_column),
                    "target_table": _strip_quotes(right_table),
                    "target_column": _strip_quotes(right_column),
                }
            )
            continue

    # ----------------------
    # RESOLVE ENUM TYPES
    # ----------------------
    if enums:
        for table in tables.values():
            for column in table.columns:
                enum_values = enums.get(column.data_type.strip().lower())
                if enum_values is not None:
                    column.enum_values = list(enum_values)

    global _last_many_to_many_refs
    _last_many_to_many_refs = many_to_many_refs

    return tables, refs
