from __future__ import annotations

import hashlib
import random
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Optional

import pandas as pd
from faker import Faker

from model2data.generate.faker import (
    AsOf,
    generate_column_values,
    release_row_pools,
    reset_duplicate_unique_columns,
    reset_row_pools,
    set_locale,
)
from model2data.generate.options import UNIFORM, TimeProfile, validate_skew
from model2data.generate.relationships import (
    build_fk_lookup,
    classify_refs,
)
from model2data.generate.timeline import order_row_times
from model2data.parse.dbml import TableDef

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


# Composite keys the most recent run could not make fully unique. The dedup
# retry is deliberately bounded (see _deduplicate_composite_keys), so a key
# whose value space is too small for the requested row count ends up with
# real duplicates -- which then fail the composite-key dbt test the project
# generates for that very key. Recorded here so the CLI can say so up front
# instead of leaving the user to work backwards from a failing `dbt build`.
_dedup_state: dict[str, list[str]] = {"unresolved_composite_keys": []}


def reset_dedup_state() -> None:
    """Clear the record of composite keys left with duplicate combinations."""
    _dedup_state["unresolved_composite_keys"] = []


def get_unresolved_composite_keys() -> list[str]:
    """Return "table (col_a, col_b)" labels for composite keys left duplicated."""
    return list(_dedup_state["unresolved_composite_keys"])


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def generate_data_from_dbml(
    tables: dict[str, TableDef],
    refs: list[dict],
    base_rows: int = 100,
    seed: Optional[int] = None,
    row_overrides: Optional[Mapping[str, int]] = None,
    locale: Optional[str] = None,
    as_of: AsOf = None,
    table_seeds: Optional[Mapping[str, int]] = None,
    time_profile: Optional[TimeProfile] = None,
    skew: float = 0.0,
) -> dict[str, pd.DataFrame]:
    """
    Generate synthetic datasets from parsed DBML definitions.

    `base_rows` is the row count for every table; `row_overrides` sets it per
    table, keyed by DBML table name. Real schemas are rarely uniform -- a
    handful of dimension rows against a fact table two orders of magnitude
    larger is the normal shape, and generating 100 of each makes joins and
    aggregates behave nothing like the warehouse being modelled. Names not
    present in `row_overrides` fall back to `base_rows`; unknown names are
    ignored.

    `locale` picks the Faker locale every generated person and address is drawn
    from -- `"nl_BE"`, `"fr_FR"`, `"en_GB"` -- defaulting to `DEFAULT_LOCALE`.
    It is a per-run setting rather than a per-column one on purpose: a table
    holding one Belgian and one American address is the incoherence the row
    pools exist to remove.

    `as_of` is the date every generated date and timestamp is placed relative
    to -- dates land in the two years up to it, timestamps in the year up to
    midnight on it -- and defaults to today. Without it a seed reproduces only
    for as long as the day lasts: the numbers come back identical and the dates
    move, so a committed fixture churns and a saved project renders different
    rows next month. Pass the day the run should look like it happened on and
    the whole frame reproduces, on any later day.

    `table_seeds` re-rolls individual tables without disturbing the rest. Each
    table draws from its own RNG stream, derived from `(seed, table_name,
    table_seeds[table_name])`, so bumping one table's entry changes that
    table's rows and leaves every other table byte-identical. Children of a
    re-rolled table keep pointing at rows that exist, because their foreign
    keys are drawn from whatever their parent ended up holding. It needs a
    `seed` to work off -- with none, every table is already different on every
    run -- and unknown table names are an error rather than a silent no-op.

    `time_profile` shapes *when* generated timestamps and dates fall: toward
    business hours and weekdays, along a growth trend, with a seasonal peak.
    See `TimeProfile`. None is the uniform profile, which draws every second of
    the window with equal probability the way earlier releases did.

    `skew` is how unevenly a child table's rows are spread over its parents,
    from `0.0` (every parent equally likely, the earlier behaviour) to `1.0` (a
    few parents hold most of the children). A column can override it with a
    `{"skew": ...}` hint in its note.

    This function is deterministic if a seed is provided (and, with `as_of`,
    on any day). It performs no filesystem I/O and returns pandas DataFrames.
    """
    _validate_table_seeds(tables, table_seeds, seed)
    profile = time_profile or UNIFORM
    skew = validate_skew(skew)

    # Locale first, then the seed: switching locale builds a new Faker, and the
    # seed has to be the last word on the generator that actually runs.
    set_locale(locale)

    if seed is not None:
        random.seed(seed)
        Faker.seed(seed)

    reset_cycle_state()
    reset_dedup_state()
    reset_duplicate_unique_columns()
    reset_row_pools()

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
        row_count = _determine_row_count(table_def.name, base_rows, row_overrides)

        # Give this table its own RNG stream before a single value of it is
        # drawn. One stream for the whole run meant re-rolling a table
        # re-rolled everything generated after it too -- fine for a one-shot
        # CLI run, useless for a "regenerate just this table" button, which is
        # exactly the thing users ask for once they like four tables out of
        # five. Same locale-then-seed order as the run-level seeding above.
        if seed is not None:
            stream_seed = _table_stream_seed(
                seed, table_name, table_seeds.get(table_name) if table_seeds else None
            )
            random.seed(stream_seed)
            Faker.seed(stream_seed)

        # A table's identities are its own. `release_row_pools` below already
        # drops the pool keyed by this table's name; this also drops the
        # un-named bucket the composite-key repair shares, so nothing a
        # previous table left behind can reach this one's rows and make its
        # stream depend on what came before it.
        reset_row_pools()

        data: dict[str, list] = {}

        # A composite *primary* key's member columns are frequently declared
        # only via an `indexes {} [pk]` block (the standard DBML shape for a
        # join/bridge table's key -- see examples/tagging_m2m.dbml's
        # post_tags), with no `pk`/`not null` on the individual columns
        # themselves. SQL primary-key semantics forbid nulls in any such
        # column regardless of where the constraint was declared, so those
        # columns need the same not-null treatment as an explicit `pk`
        # column below -- otherwise the nullability pass can null out a
        # supposedly-required key column (and, when it's also an FK, make
        # that value look like a dangling reference to no real parent row).
        # A composite *unique* (non-pk) key doesn't get this treatment:
        # standard SQL unique constraints don't forbid nulls in their
        # member columns.
        composite_pk_columns: set[str] = {
            column_name
            for key in table_def.composite_keys
            if key.get("type") == "pk"
            for column_name in key.get("columns") or []
        }

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
                force_not_null=column.name in composite_pk_columns,
                table_name=table_name,
                as_of=as_of,
                time_profile=profile,
                skew=skew,
            )

        df = pd.DataFrame(data)
        # Ordering runs before FK resolution/dedup so a self-ref repair or a
        # composite-key retry regenerates a temporal column's value into a
        # frame that already respects created/updated/closed ordering, rather
        # than one where only the untouched columns do.
        df = order_row_times(df, table_def, as_of=as_of)
        df = _resolve_self_referencing_fks(
            df,
            table_def,
            table_name,
            fk_lookup,
            row_count,
            as_of=as_of,
            time_profile=profile,
            skew=skew,
        )
        df = _deduplicate_composite_keys(
            df,
            table_def,
            table_name,
            fk_lookup,
            generated,
            as_of=as_of,
            time_profile=profile,
            skew=skew,
        )

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
        # This table is finished: nothing will read its people or addresses
        # again, and on a million-row table they are worth tens of megabytes.
        release_row_pools(table_name)

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
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
    skew: float = 0.0,
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
        unresolved = 0
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
                        df.at[idx, col_name] = generate_column_values(
                            col_def,
                            row_count=1,
                            as_of=as_of,
                            time_profile=time_profile,
                            skew=skew,
                        )[0]
                combo = tuple(df.at[idx, c] for c in key_columns)
                attempts += 1
            # Retry budget spent and still colliding: this row keeps a
            # duplicate combination. Usually means the key's value space is
            # too small for the requested row count (e.g. a bridge table with
            # far more rows than parent-id pairs to draw from).
            if combo in seen:
                unresolved += 1
            seen.add(combo)

        if unresolved:
            _dedup_state["unresolved_composite_keys"].append(
                f"{table_name} ({', '.join(key_columns)}): {unresolved} duplicate row(s)"
            )

    return df


def _resolve_self_referencing_fks(
    df: pd.DataFrame,
    table_def: TableDef,
    table_name: str,
    fk_lookup: dict[tuple[str, str], tuple[str, str]],
    row_count: int,
    as_of: AsOf = None,
    time_profile: Optional[TimeProfile] = None,
    skew: float = 0.0,
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
    composite_pk_columns: set[str] = {
        column_name
        for key in table_def.composite_keys
        if key.get("type") == "pk"
        for column_name in key.get("columns") or []
    }

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
            force_not_null=column.name in composite_pk_columns,
            table_name=table_name,
            as_of=as_of,
            time_profile=time_profile,
            skew=skew,
        )

    return df


def _validate_table_seeds(
    tables: dict[str, TableDef],
    table_seeds: Optional[Mapping[str, int]],
    seed: Optional[int],
) -> None:
    """Reject a `table_seeds` mapping that cannot do what it was asked to do.

    Unlike `row_overrides`, which quietly ignores a name it does not know, an
    unknown name here is always a mistake worth stopping for: the caller asked
    for one table to be re-rolled and would otherwise get a run in which
    nothing changed, with nothing said about why. The same goes for passing
    `table_seeds` with no `seed` -- there is no stream to re-roll out of, and
    every table is already different on every run.
    """
    if not table_seeds:
        return

    unknown = sorted(name for name in table_seeds if name not in tables)
    if unknown:
        known = ", ".join(sorted(tables)) or "none"
        label = "No table named" if len(unknown) == 1 else "No tables named"
        named = ", ".join(repr(name) for name in unknown)
        raise ValueError(f"{label} {named} in this schema. Tables: {known}.")

    if seed is None:
        raise ValueError(
            "table_seeds needs a seed to re-roll a table out of: without one every "
            "table is already generated afresh on every run."
        )


def _table_stream_seed(seed: int, table_name: str, table_seed: Optional[int]) -> int:
    """The RNG seed one table draws from, derived from the run seed and its name.

    Hashed with blake2b rather than Python's built-in `hash()`, which is salted
    per interpreter process for strings: a "deterministic" seed built on it
    would reproduce only within a single run of the program, which is the one
    place determinism was never in doubt.
    """
    payload = f"{seed}|{table_name}|{'' if table_seed is None else table_seed}"
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _determine_row_count(
    table_name: str,
    base_rows: int,
    row_overrides: Optional[Mapping[str, int]] = None,
) -> int:
    """Return the row count for one table: its override, else `base_rows`."""
    if row_overrides:
        override = row_overrides.get(table_name)
        if override is not None:
            return override
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
