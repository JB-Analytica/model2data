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

        generated[table_name] = df

    return generated


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
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
