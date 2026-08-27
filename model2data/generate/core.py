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

# Tables the most recent generate_data_from_dbml() call found stuck in an
# unresolved FK cycle (never reached indegree 0 during the topological
# sort). Exposed out-of-band, mirroring generate.faker's
# reset_stats()/get_unmapped_columns() pattern, so the CLI can surface a
# warning without changing this module's existing return signature.
_cycle_state: dict[str, list[str]] = {"cyclic_tables": []}


def reset_cycle_state() -> None:
    """Clear the record of tables found in an unresolved FK cycle."""
    _cycle_state["cyclic_tables"] = []


def get_cyclic_tables() -> list[str]:
    """Return table names stuck in an unresolved FK cycle by the last run."""
    return list(_cycle_state["cyclic_tables"])


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

    reset_cycle_state()

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

            # "unique" columns get exactly the same dbt `unique` schema test
            # as "pk" columns (see dbt/tests.py) but, unlike pk, weren't
            # actually enforced during generation -- so a demo project's own
            # generated test could fail non-deterministically on a chance
            # collision (e.g. a "unique" promo_code or VIN column).
            ensure_unique = "pk" in column.settings or "unique" in column.settings
            data[column.name] = generate_column_values(
                column=column,
                row_count=row_count,
                fk_series=fk_series,
                ensure_unique=ensure_unique,
            )

        df = pd.DataFrame(data)
        df = _resolve_self_referencing_fks(df, table_def, table_name, fk_lookup, row_count)
        df = _deduplicate_composite_keys(df, table_def, table_name, fk_lookup, generated)

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
        if column.enum_values:
            # An enum column's data_type is the *enum's name*, not a SQL
            # scalar type -- and that name can innocently contain "int" as a
            # substring (e.g. "maintenance_type"), which would otherwise
            # false-positive the check below and crash trying to cast the
            # column's real string values to Int64. Values here are always
            # generated as strings (see generate_column_values), never ints.
            continue
        base_type = column.data_type.lower().split("(")[0].strip()
        if any(key in base_type for key in ["int", "integer", "bigint", "smallint"]):
            df[column.name] = df[column.name].astype("Int64")

    return df


def _deduplicate_composite_keys(
    df: pd.DataFrame,
    table_def: TableDef,
    table_name: str,
    fk_lookup: dict[tuple[str, str], tuple[str, str]],
    generated: dict[str, pd.DataFrame],
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

        # A composite key's columns are very often FKs (the standard way to
        # model a join/bridge table's PK). Regenerating those blind, via the
        # column's own type-based generator, would silently break the
        # relationship -- the retry needs to keep sampling from the real
        # parent id pool instead. Self-refs use this table's own
        # already-resolved column (dedup runs after self-ref resolution).
        fk_pools: dict[str, list] = {}
        for col_name, _ in regen_columns:
            fk_target = fk_lookup.get((table_name, col_name))
            if not fk_target:
                continue
            parent_table, parent_column = fk_target
            parent_df = df if parent_table == table_name else generated.get(parent_table)
            if parent_df is not None and parent_column in parent_df.columns:
                pool = parent_df[parent_column].tolist()
                if pool:
                    fk_pools[col_name] = pool

        seen: set = set()
        for idx in df.index:
            combo = tuple(df.at[idx, c] for c in key_columns)
            attempts = 0
            while combo in seen and attempts < max_attempts:
                # Regenerate every column of the key (not just the last one) so
                # the retry can actually reach unused combinations, not just
                # unused values of a single column.
                for col_name, col_def in regen_columns:
                    if col_name in fk_pools:
                        df.at[idx, col_name] = random.choice(fk_pools[col_name])
                    else:
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

        ensure_unique = "pk" in column.settings or "unique" in column.settings
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

    # Any table not reached by the Kahn's-algorithm pass above never had its
    # indegree reduced to 0, which (unlike a genuinely disconnected table,
    # which starts at indegree 0 and is processed by the loop above) can only
    # happen if it sits inside -- or depends on -- an unresolved multi-table
    # FK cycle. Append it to the order anyway (still generate *something*
    # rather than crash on an unusual-but-not-invalid schema), but record it
    # so the CLI can warn the user their generated FK data may not respect
    # every relationship.
    leftover = sorted(name for name in tables if name not in order)
    if leftover:
        _cycle_state["cyclic_tables"] = leftover
    order.extend(leftover)

    return order
