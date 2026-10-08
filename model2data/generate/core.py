from __future__ import annotations

import hashlib
import random
from collections import defaultdict, deque
from collections.abc import Mapping
from datetime import datetime
from typing import Optional

import pandas as pd
from faker import Faker

from model2data.generate.faker import (
    AsOf,
    generate_column_values,
    release_row_pools,
    reset_duplicate_unique_columns,
    reset_row_pools,
    resolve_address_pool_field,
    set_locale,
)
from model2data.generate.hints import validate_hints
from model2data.generate.kinds import is_integer_type
from model2data.generate.options import UNIFORM, TimeProfile, validate_skew
from model2data.generate.parents import ParentRule, follow_parents, parent_rules
from model2data.generate.relationships import (
    build_fk_lookup,
    classify_refs,
)
from model2data.generate.timeline import _resolve_anchor, order_row_times
from model2data.generate.when import SeedFor, apply_when
from model2data.parse.dbml import ColumnDef, TableDef

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
    validate_hints(tables, refs)
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
    ordered_tables, deferred_edges = _table_order(tables, fk_refs)
    # The columns whose `after` names a parent's column (see generate.parents).
    rules = parent_rules(tables, fk_refs, ordered_tables)
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

        # A table's own shape, worked out before a single value is drawn: does
        # this table have a `country` column with no `city`/`street`/`state`/
        # `postcode` beside it to keep coherent with. See
        # _lone_country_columns.
        lone_country_columns = _lone_country_columns(table_def)

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
                lone_country=column.name in lone_country_columns,
            )

        df = pd.DataFrame(data)
        # A cross-table `after` keeps a row's date on or after the parent row
        # it points at: before the row's own later dates are ordered, so they
        # follow it wherever it ends up (see generate.parents).
        table_rules = rules.get(table_name, [])
        floors = {
            rule.column: follow_parents(
                df,
                rule,
                _column(table_def, rule.column),
                generated,
                as_of=as_of,
                time_profile=profile,
            )[0]
            for rule in table_rules
        }
        # Ordering runs before FK resolution/dedup so a self-ref repair or a
        # composite-key retry regenerates a temporal column's value into a
        # frame that already respects created/updated/closed ordering, rather
        # than one where only the untouched columns do.
        df = order_row_times(df, table_def, as_of=as_of, time_profile=profile, floors=floors)
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
        df = _mirror_attributes(df, table_name, attribute_refs, fk_refs, generated)

        # `when` last, once every column it reads holds its final value, and
        # from streams of its own: see generate.when.
        apply_when(
            df,
            table_def,
            cap=_midnight(as_of),
            seed_for=_when_seed(seed, table_name, table_seeds),
            as_of=as_of,
            time_profile=profile,
        )
        if table_rules:
            df = _hold_parent_rules(df, table_def, table_rules, generated, as_of, profile)

        df = _coerce_integer_dtypes(df, table_def)
        generated[table_name] = df
        # This table is finished: nothing will read its people or addresses
        # again, and on a million-row table they are worth tens of megabytes.
        release_row_pools(table_name)

    # ---------------------------------------------------------
    # Nullable foreign keys that broke an FK cycle: their parent was generated
    # after them, so they are drawn now, from the rows it ended up holding.
    # ---------------------------------------------------------
    for table_name, column in _deferred_fk_columns(tables, fk_refs, deferred_edges):
        table_def = tables[table_name]
        parent_table, parent_column = fk_lookup[(table_name, column.name)]
        if seed is not None:
            stream_seed = _table_stream_seed(
                seed,
                f"{table_name}.{column.name}",
                table_seeds.get(table_name) if table_seeds else None,
            )
            random.seed(stream_seed)
            Faker.seed(stream_seed)
        reset_row_pools()
        df = generated[table_name]
        df[column.name] = generate_column_values(
            column=column,
            row_count=len(df),
            fk_series=generated[parent_table][parent_column],
            ensure_unique="unique" in column.settings,
            table_name=table_name,
            as_of=as_of,
            time_profile=profile,
            skew=skew,
        )
        df = _mirror_attributes(df, table_name, attribute_refs, fk_refs, generated)
        generated[table_name] = _coerce_integer_dtypes(df, table_def)
        release_row_pools(table_name)

    return generated


def _mirror_attributes(
    df: pd.DataFrame,
    table_name: str,
    attribute_refs: list[dict],
    fk_refs: list[dict],
    generated: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    """Copy each non-FK referenced attribute from the parent row the FK points at."""
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
        fk_ref = next(
            (
                r
                for r in fk_refs
                if r["source_table"] == table_name and r["target_table"] == parent_table
            ),
            None,
        )
        if fk_ref is None:
            continue

        fk_column = fk_ref["source_column"]
        parent_key = fk_ref["target_column"]
        if fk_column not in df.columns or parent_key not in parent_df.columns:
            continue

        lookup = parent_df.groupby(parent_key)[parent_column].first().to_dict()

        df[child_column] = df[fk_column].map(lookup)

    return df


def parent_rules_for(tables: dict[str, TableDef], refs: list[dict]) -> dict[str, list[ParentRule]]:
    """The columns whose cross-table `after` `generate_data_from_dbml` keeps, by table key.

    Each with the foreign keys it follows (see `generate.parents`). What the
    dbt export tests, so it is worked out exactly as generation works it out.
    """
    fk_refs, _ = classify_refs(tables, refs)
    order, _broken, _leftover = _plan_table_order(tables, fk_refs)
    return parent_rules(tables, fk_refs, order)


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _column(table_def: TableDef, name: str) -> ColumnDef:
    return next(c for c in table_def.columns if c.name == name)


def _hold_parent_rules(
    df: pd.DataFrame,
    table_def: TableDef,
    rules: list[ParentRule],
    generated: dict[str, pd.DataFrame],
    as_of: AsOf,
    profile: TimeProfile,
) -> pd.DataFrame:
    """Keep the cross-table `after` after the passes that run once it was applied.

    A composite key's repair can give a row another parent, and `when` can fill
    a date the null pass had left empty; either can leave a row dated before
    its parent. Such a row's date moves (its parents stay: the attributes
    mirrored through them are final by now), and the row's dates that follow
    something are placed again. A table neither touched comes back as it was.
    """
    floors: dict[str, pd.Series] = {}
    moved: set = set()
    for rule in rules:
        floor, rows = follow_parents(
            df,
            rule,
            _column(table_def, rule.column),
            generated,
            as_of=as_of,
            time_profile=profile,
            repick=False,
        )
        floors[rule.column] = floor
        moved.update(rows)
    if moved:
        df = order_row_times(
            df, table_def, as_of=as_of, time_profile=profile, rows=sorted(moved), floors=floors
        )
    return df


def _midnight(as_of: AsOf) -> datetime:
    """Midnight of the run's anchor day: the latest moment a first-day timestamp takes."""
    anchor = _resolve_anchor(as_of)
    return datetime(anchor.year, anchor.month, anchor.day)


def _when_seed(
    seed: Optional[int], table_name: str, table_seeds: Optional[Mapping[str, int]]
) -> SeedFor:
    """The seed of each `when` column's own stream: the table's, told apart by the column."""

    def seed_for(column: str) -> Optional[int]:
        if seed is None:
            return None
        table_seed = table_seeds.get(table_name) if table_seeds else None
        return _table_stream_seed(seed, f"{table_name}.{column}|when", table_seed)

    return seed_for


def _lone_country_columns(table_def: TableDef) -> set[str]:
    """Names of this table's *lone* country columns.

    A `country` column reads as the locale's own country on every row when it
    sits beside a `city`/`street`/`state`/`postcode` column -- together they
    describe one place, and the country has to agree with the rest of it.
    Alone, repeating that same country on every row reads as a single-country
    customer base rather than an international one, so
    `generate_column_values` draws it from a home-heavy mix instead (see
    `faker._HOME_COUNTRY_SHARE`). `country` columns don't count as company
    for each other -- only a *different* address-pool field does.
    """
    address_fields = {
        column.name: field
        for column in table_def.columns
        for field in [resolve_address_pool_field(column)]
        if field is not None
    }
    country_columns = {name for name, field in address_fields.items() if field == "country"}
    if not country_columns:
        return set()
    has_place_column = any(field != "country" for field in address_fields.values())
    return set() if has_place_column else country_columns


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
        # "int" alone matched every name the old list spelled out ("integer",
        # "bigint", "smallint" all contain it), so this is the same test, now
        # shared with the generator so the two cannot drift apart again.
        if is_integer_type(column.data_type):
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
        # Read the key once, as plain values: a row's combination is only read
        # back from the frame once it has been regenerated (rarely), where the
        # frame's own dtype decides the value stored. A plain value and the
        # frame's scalar for it are equal and hash alike, so `seen` is the same.
        combos = list(zip(*(df[c].tolist() for c in key_columns), strict=True))
        for position, idx in enumerate(df.index):
            combo = combos[position]
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
    """Order tables so parent tables are generated before children."""
    return _table_order(tables, fk_refs)[0]


def _nullable_fk(table: TableDef, column_name: str) -> bool:
    """Whether a foreign key column may be null: no `pk`, no `not null`, in no pk key."""
    column = next((c for c in table.columns if c.name == column_name), None)
    if column is None or "pk" in column.settings or "not null" in column.settings:
        return False
    return not any(
        key.get("type") == "pk" and column_name in (key.get("columns") or [])
        for key in table.composite_keys
    )


def _table_order(
    tables: dict[str, TableDef],
    fk_refs: list[dict],
) -> tuple[list[str], set[tuple[str, str]]]:
    """
    Order tables so parent tables are generated before children, and name the
    (parent, child) edges that had to be broken to get there.

    An FK cycle is broken at a table whose every remaining incoming edge is
    made only of nullable foreign keys: that table goes first, and those
    columns are drawn once their parents exist (see `_deferred_fk_columns`). A
    schema without a cycle breaks nothing, so its order is what it always was.
    """
    order, broken, leftover = _plan_table_order(tables, fk_refs)
    if leftover:
        _cycle_state["cyclic_tables"] = leftover
    return order, broken


def _plan_table_order(
    tables: dict[str, TableDef],
    fk_refs: list[dict],
) -> tuple[list[str], set[tuple[str, str]], list[str]]:
    """`_table_order`, plus the tables left in a cycle, without recording them."""
    graph: dict[str, set[str]] = defaultdict(set)
    indegree: dict[str, int] = dict.fromkeys(tables.keys(), 0)
    # An edge is breakable when every reference along it is a nullable FK.
    breakable: dict[tuple[str, str], bool] = {}

    for ref in fk_refs:
        parent = ref["target_table"]
        child = ref["source_table"]

        if parent == child:
            continue
        if parent not in tables or child not in tables:
            continue

        nullable = _nullable_fk(tables[child], ref["source_column"])
        breakable[(parent, child)] = breakable.get((parent, child), True) and nullable
        if child not in graph[parent]:
            graph[parent].add(child)
            indegree[child] += 1

    queue = deque(sorted(name for name, deg in indegree.items() if deg == 0))
    order: list[str] = []
    broken: set[tuple[str, str]] = set()

    while True:
        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbor in sorted(graph.get(node, [])):
                indegree[neighbor] -= 1
                if indegree[neighbor] == 0:
                    queue.append(neighbor)

        remaining = sorted(name for name in tables if name not in order)
        if not remaining:
            break
        placed = set(order)
        candidate = None
        for name in remaining:
            incoming = [
                parent
                for parent in remaining
                if name in graph.get(parent, ()) and parent not in placed
            ]
            if incoming and all(breakable[(parent, name)] for parent in incoming):
                candidate = (name, incoming)
                break
        if candidate is None:
            break
        name, incoming = candidate
        for parent in incoming:
            graph[parent].discard(name)
            broken.add((parent, name))
        indegree[name] = 0
        queue.append(name)

    # Any table still not reached sits inside -- or depends on -- an FK cycle
    # of required foreign keys, which no order can satisfy. Append it to the
    # order anyway (still generate *something* rather than crash on an
    # unusual-but-not-invalid schema), but record it so the CLI can warn the
    # user their generated FK data may not respect every relationship.
    leftover = sorted(name for name in tables if name not in order)
    order.extend(leftover)

    return order, broken, leftover


def _deferred_fk_columns(
    tables: dict[str, TableDef],
    fk_refs: list[dict],
    broken: set[tuple[str, str]],
) -> list[tuple[str, ColumnDef]]:
    """The (table, column) foreign keys along broken edges, in schema order."""
    columns: list[tuple[str, ColumnDef]] = []
    for ref in fk_refs:
        child, parent = ref["source_table"], ref["target_table"]
        if (parent, child) not in broken:
            continue
        column = next(c for c in tables[child].columns if c.name == ref["source_column"])
        if (child, column) not in columns:
            columns.append((child, column))
    return columns
