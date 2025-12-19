from __future__ import annotations

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


@dataclass
class TableDef:
    name: str
    columns: list[ColumnDef] = field(default_factory=list)


# -------------------------------
# Helpers
# -------------------------------


def _strip_quotes(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _parse_column_settings(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    parts = [part.strip() for part in raw.split(",")]
    return {part.strip("'").strip('"').lower() for part in parts if part}


def normalize_identifier(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()
    if not cleaned:
        cleaned = "table"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned


def parse_dbml(dbml_path: Path) -> tuple[dict[str, TableDef], list[dict]]:
    text = dbml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    tables: dict[str, TableDef] = {}
    refs: list[dict] = []

    current_table: Optional[TableDef] = None
    in_indexes_block = False
    note_block_depth = 0
    in_ref_block = False  # NEW

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue

        cleaned = line.split("//", 1)[0].strip()
        if not cleaned:
            continue

        triple_quote_count = cleaned.count("'''")
        if triple_quote_count:
            note_block_depth = (note_block_depth + triple_quote_count) % 2
            if cleaned.startswith("Note:"):
                continue
        if note_block_depth:
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
                continue
            if cleaned.startswith("}"):
                tables[current_table.name] = current_table
                current_table = None
                continue
            if cleaned.startswith("Note:"):
                continue

            col_match = re.match(
                r'(".*?"|`.*?`|[\w]+)\s+([^\[]+?)(?:\s+\[(.+)\])?$',
                cleaned,
            )
            if not col_match:
                continue

            col_name = _strip_quotes(col_match.group(1))
            col_type = col_match.group(2).strip()
            settings = _parse_column_settings(col_match.group(3))

            current_table.columns.append(
                ColumnDef(
                    name=col_name,
                    data_type=col_type,
                    settings=settings,
                    note=None,
                )
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
                r'(".*?"|`.*?`|[\w]+)\.(".*?"|`.*?`|[\w]+)\s*([<>])\s*'
                r'(".*?"|`.*?`|[\w]+)\.(".*?"|`.*?`|[\w]+)',
                cleaned,
            )
            if not ref_match:
                continue

            left_table, left_column, operator, right_table, right_column = ref_match.groups()

            # Ignore <> and other non-FK relations
            if operator not in (">", "<"):
                continue

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

    return tables, refs
