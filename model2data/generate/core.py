from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Optional

import pandas as pd
from faker import Faker

from model2data.generate.faker import generate_column_values
from model2data.generate.relationships import (
    build_fk_lookup,
    classify_refs,
)
from model2data.parse.dbml import TableDef

fake = Faker()


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def generate_data_from_dbml(
    tables: dict[str, TableDef],
    refs: list[dict],
    base_rows: int = 100,
    seed: Optional[int] = None,
) -> dict[str, pd.DataFrame]:
    """
    Generate synthetic datasets from parsed DBML definitions.

    This function is deterministic if a seed is provided.
    It performs no filesystem I/O and returns pandas DataFrames.
    """
    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)

    # ---------------------------------------------------------
    # Classify references
    # ---------------------------------------------------------
    fk_refs, attribute_refs = classify_refs(tables, refs)
    fk_lookup = build_fk_lookup(fk_refs)

    # ---------------------------------------------------------
    # Generate tables in dependency order
    # ---------------------------------------------------------
    ordered_tables = _topological_table_order(tables, fk_refs)
    generated: dict[str, pd.DataFrame] = {}

    for table_name in ordered_tables:
        table_def = tables[table_name]
        row_count = _determine_row_count(table_def.name, base_rows)

        data: dict[str, list] = {}

        # -----------------------
        # First pass: columns + FKs
        # -----------------------
        for column in table_def.columns:
            fk_series = None
            fk_target = fk_lookup.get((table_name, column.name))

            if fk_target:
                parent_table, parent_column = fk_target
                parent_df = generated.get(parent_table)
                if parent_df is not None and parent_column in parent_df.columns:
                    fk_series = parent_df[parent_column]

            ensure_unique = "pk" in column.settings
            data[column.name] = generate_column_values(
                column=column,
                row_count=row_count,
                fk_series=fk_series,
                ensure_unique=ensure_unique,
            )

        df = pd.DataFrame(data)
        df = _resolve_self_referencing_fks(df, table_def, table_name, fk_lookup, row_count)
        df = _deduplicate_composite_keys(df, table_def)

        # -----------------------------------------------------
        # Second pass: attribute mirroring (non-FK refs)
        # -----------------------------------------------------
        for ref in attribute_refs:
            if ref["source_table"] != table_name:
                continue

            parent_table = ref["target_table"]
            parent_column = ref["target_column"]
            child_column = ref["source_column"]

            parent_df = generated.get(parent_table)
            if parent_df is None:
                continue

            # find FK linking child → parent
            fk_column = next(
                (
                    r["source_column"]
                    for r in fk_refs
                    if r["source_table"] == table_name and r["target_table"] == parent_table
                ),
                None,
            )

            if not fk_column or fk_column not in df.columns:
                continue

            lookup = parent_df.groupby("id")[parent_column].first().to_dict()

            df[child_column] = df[fk_column].map(lookup)

        df = _coerce_integer_dtypes(df, table_def)
        generated[table_name] = df

    return generated


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _coerce_integer_dtypes(df: pd.DataFrame, table_def: TableDef) -> pd.DataFrame:
    """
    Cast int/bigint/smallint-typed columns to pandas' nullable "Int64" dtype.

    `generate_column_values` fills "empty" nullable columns with `None`
    (absent an explicit default). Building a plain `pd.DataFrame` from a
    Python list mixing ints and `None` silently upcasts that column to
    float64, so whole numbers round-trip through the CSV seed as "70.0"
    instead of "70" and blanks. Int64 keeps them as integers and renders
    nulls as empty cells, matching the DBML-declared type.
    """
    for column in table_def.columns:
        base_type = column.data_type.lower().split("(")[0].strip()
        if any(key in base_type for key in ["int", "integer", "bigint", "smallint"]):
            df[column.name] = df[column.name].astype("Int64")

    return df


def _deduplicate_composite_keys(
    df: pd.DataFrame,
    table_def: TableDef,
    max_attempts: int = 20,
) -> pd.DataFrame:
    """
    Regenerate colliding rows for any pk/unique composite key declared via an
    `indexes {}` block, so the generated seed data respects that constraint.
    Bounded retry mirrors generate.faker._deduplicate: give up gracefully on
    a tiny value space rather than looping forever.
    """
    columns_by_name = {column.name: column for column in table_def.columns}

    for key in table_def.composite_keys:
        if key.get("type") not in ("pk", "unique"):
            continue

        key_columns = key.get("columns") or []
        if not key_columns or any(c not in df.columns for c in key_columns):
            continue

        regen_columns = [(c, columns_by_name[c]) for c in key_columns]

        seen: set = set()
        for idx in df.index:
            combo = tuple(df.at[idx, c] for c in key_columns)
            attempts = 0
            while combo in seen and attempts < max_attempts:
                # Regenerate every column of the key (not just the last one) so
                # the retry can actually reach unused combinations, not just
                # unused values of a single column.
                for col_name, col_def in regen_columns:
                    df.at[idx, col_name] = generate_column_values(col_def, row_count=1)[0]
                combo = tuple(df.at[idx, c] for c in key_columns)
                attempts += 1
            seen.add(combo)

    return df


def _resolve_self_referencing_fks(
    df: pd.DataFrame,
    table_def: TableDef,
    table_name: str,
    fk_lookup: dict[tuple[str, str], tuple[str, str]],
    row_count: int,
) -> pd.DataFrame:
    """
    Re-generate any FK column that references its own table (e.g. a
    `manager_id` on `employees` pointing back at `employees.id`) using the
    table's own just-built parent column as the value pool.

    These columns can't be resolved during the main per-column generation
    pass above because the table isn't done building itself yet (its own
    df isn't added to `generated` until the whole loop iteration finishes),
    so `fk_series` falls through to None there and the column gets
    unrelated random values instead. Once `df` exists we know the real
    parent-column values and can fix it up here.
    """
    for column in table_def.columns:
        fk_target = fk_lookup.get((table_name, column.name))
        if not fk_target:
            continue

        parent_table, parent_column = fk_target
        if parent_table != table_name or parent_column not in df.columns:
            continue

        ensure_unique = "pk" in column.settings
        df[column.name] = generate_column_values(
            column=column,
            row_count=row_count,
            fk_series=df[parent_column],
            ensure_unique=ensure_unique,
        )

    return df


def _determine_row_count(table_name: str, base_rows: int) -> int:
    """
    Return the base number of rows for all tables.
    """
    return base_rows


def _topological_table_order(
    tables: dict[str, TableDef],
    fk_refs: list[dict],
) -> list[str]:
    """
    Order tables so parent tables are generated before children.
    """
    graph: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = dict.fromkeys(tables.keys(), 0)

    for ref in fk_refs:
        parent = ref["target_table"]
        child = ref["source_table"]

        if parent == child:
            continue
        if parent not in tables or child not in tables:
            continue

        if child not in graph[parent]:
            graph[parent].add(child)
            indegree[child] += 1

    queue = deque(sorted(name for name, deg in indegree.items() if deg == 0))
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in sorted(graph.get(node, [])):
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    # Safety net for disconnected tables
    for name in tables.keys():
        if name not in order:
            order.append(name)

    return order
