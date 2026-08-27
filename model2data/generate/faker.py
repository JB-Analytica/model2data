from __future__ import annotations

import random
import re
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional

import pandas as pd
from faker import Faker

from model2data.parse.dbml import ColumnDef

fake = Faker()


# ---------------------------------------------------------
# Column-name -> Faker provider inference
# ---------------------------------------------------------
# Ordered most-specific first: a column like "first_name" must match
# "first_name" before any looser pattern gets a chance. There is
# deliberately no generic "name" pattern, since "product_name" or
# "company_name" would otherwise be filled with a person's name.
_NAME_PATTERNS: list[tuple[str, Callable[[], object]]] = [
    ("first_name", lambda: fake.first_name()),
    ("last_name", lambda: fake.last_name()),
    ("full_name", lambda: fake.name()),
    ("user_name", lambda: fake.user_name()),
    ("username", lambda: fake.user_name()),
    ("password", lambda: fake.password()),
    ("email", lambda: fake.email()),
    ("phone", lambda: fake.phone_number()),
    ("mobile", lambda: fake.phone_number()),
    ("fax", lambda: fake.phone_number()),
    ("street", lambda: fake.street_address()),
    ("address", lambda: fake.address().replace("\n", ", ")),
    ("city", lambda: fake.city()),
    ("province", lambda: fake.state()),
    ("state", lambda: fake.state()),
    ("country", lambda: fake.country()),
    ("zip", lambda: fake.postcode()),
    ("postal", lambda: fake.postcode()),
    ("homepage", lambda: fake.url()),
    ("website", lambda: fake.url()),
    ("url", lambda: fake.url()),
    ("domain", lambda: fake.domain_name()),
    ("employer", lambda: fake.company()),
    ("company", lambda: fake.company()),
    ("job_title", lambda: fake.job()),
    ("ip_address", lambda: fake.ipv4()),
    ("colour", lambda: fake.color_name()),
    ("color", lambda: fake.color_name()),
    ("currency", lambda: fake.currency_code()),
    ("latitude", lambda: fake.latitude()),
    ("longitude", lambda: fake.longitude()),
    ("slug", lambda: fake.slug()),
    ("avatar", lambda: fake.image_url()),
    ("image", lambda: fake.image_url()),
    ("bio", lambda: fake.text(max_nb_chars=160)),
    ("description", lambda: fake.text(max_nb_chars=160)),
    ("comment", lambda: fake.text(max_nb_chars=160)),
    ("summary", lambda: fake.text(max_nb_chars=160)),
]

# DBML type substrings generate_column_values renders as a database-native
# numeric/boolean/date/uuid value rather than arbitrary text. Shared with
# `is_free_text_type` below so seed column-type config stays in sync with
# actual generation.
_STRUCTURED_TYPE_KEYS = (
    "uuid",
    "hash",
    "int",
    "integer",
    "bigint",
    "smallint",
    "decimal",
    "numeric",
    "float",
    "double",
    "boolean",
    "bool",
    "date",
    "time",
    "timestamp",
    "datetime",
)


def is_free_text_type(data_type: str) -> bool:
    """
    True for DBML types generate_column_values fills with arbitrary text
    (name-pattern lookups, a literal Faker provider, or the generic
    fallback) rather than a numeric/boolean/date/uuid value.

    Used to force such seed columns to VARCHAR in the generated dbt
    project: some Faker-produced text (EAN13 barcodes, postcodes with a
    leading zero, ...) is entirely digits, which is enough for dbt's CSV
    seed loader to mis-infer an integer column and either overflow or
    silently strip meaningful leading zeros.
    """
    base_type = data_type.lower().split("(")[0].strip()
    return not any(key in base_type for key in _STRUCTURED_TYPE_KEYS)


# Column/table introspection helpers used by both generation and the
# CLI's post-run summary, so the two stay in sync.
_stats_state: dict[str, list[tuple[str, str]]] = {"unmapped": []}


# Columns whose `unique`/`pk` de-duplication ran out of retries and left real
# duplicate values behind -- which then fail the `unique` dbt test generated
# for that same column. Same rationale as core's unresolved-composite-key
# tracking: the bounded retry is an accepted tradeoff, but a silent one would
# leave the user reverse-engineering a failing `dbt build`.
_duplicate_unique_columns: list[str] = []


def reset_stats() -> None:
    """Clear the record of columns that fell back to generic text."""
    _stats_state["unmapped"] = []
    _duplicate_unique_columns.clear()


def get_unmapped_columns() -> list[tuple[str, str]]:
    """Return (column_name, data_type) pairs generated with a generic fallback."""
    return list(_stats_state["unmapped"])


def reset_duplicate_unique_columns() -> None:
    """Clear the record of unique columns left with duplicate values.

    Separate from reset_stats() so generate_data_from_dbml can clear this
    per-run state itself, the way it already clears its own cycle/dedup
    state -- otherwise counts leak across successive calls in-process.
    """
    _duplicate_unique_columns.clear()


def get_duplicate_unique_columns() -> list[str]:
    """Return "column: N duplicate value(s)" labels for unique columns left duplicated."""
    return list(_duplicate_unique_columns)


def _infer_by_name(column_name: str) -> Optional[Callable[[], object]]:
    normalized = re.sub(r"[^a-z0-9]+", "_", column_name.lower())
    padded = f"_{normalized}_"
    for pattern, generator in _NAME_PATTERNS:
        if f"_{pattern}_" in padded:
            return generator
    return None


# ---------------------------------------------------------
# Public API
# ---------------------------------------------------------
def generate_column_values(
    column: ColumnDef,
    row_count: int,
    fk_series: Optional[pd.Series] = None,
    ensure_unique: bool = False,
    force_not_null: bool = False,
    table_name: Optional[str] = None,
) -> list:
    """
    Generate synthetic values for a single column.
    Respects FKs, uniqueness, and optional min/max hints in column notes.

    `force_not_null` lets a caller override the nullability pass below for a
    column whose *individual* settings don't carry `not null`/`pk` but is
    still never allowed to be null -- namely a composite primary key member
    declared only via an `indexes {} [pk]` block (see
    generate.core._deduplicate_composite_keys's caller), where no single
    column setting says so but SQL primary-key semantics forbid nulls in any
    of its columns regardless.
    """
    # Qualify the label so two same-named columns in different tables
    # (an `id` on each of two tables) stay distinguishable in the report.
    unique_label = f"{table_name}.{column.name}" if table_name else column.name

    if column.enum_values:
        return [random.choice(column.enum_values) for _ in range(row_count)]

    dtype = column.data_type.lower()
    base_type = dtype.split("(")[0].strip()
    values: list = []

    # Extract min/max from note if present
    min_val = None
    max_val = None
    if column.note:
        min_val = column.note.get("min")
        max_val = column.note.get("max")

    if fk_series is not None and not fk_series.empty:
        # A plain branch of the same if/elif chain (rather than an early
        # return) so a nullable FK column can actually come back null for
        # some rows -- e.g. an optional `manager_id` on a top-level
        # employee, or an order with no customer -- matching how every
        # other branch here already respects `not null`/`pk` via the
        # nullability pass below.
        fk_values = fk_series.tolist()
        values = [random.choice(fk_values) for _ in range(row_count)]

    # -----------------------------------------------------
    # UUIDs / hashes
    # -----------------------------------------------------
    elif "uuid" in base_type or "hash" in base_type:
        values = [str(uuid.uuid4()) for _ in range(row_count)]
        if ensure_unique:
            values = _deduplicate(values, lambda: str(uuid.uuid4()), column_name=unique_label)

    # -----------------------------------------------------
    # Integers
    # -----------------------------------------------------
    elif any(key in base_type for key in ["int", "integer", "bigint", "smallint"]):
        # Use note values if present, otherwise defaults
        had_explicit_range = min_val is not None or max_val is not None
        if min_val is None:
            min_val = 0
        if max_val is None:
            max_val = 100

        if ensure_unique:
            if not had_explicit_range:
                # No user-specified range: widen the default so there's
                # always enough headroom for `row_count` unique PK values.
                max_val = max(max_val, min_val + row_count - 1)

            usable_range = max_val - min_val + 1
            if usable_range >= row_count:
                values = random.sample(range(min_val, max_val + 1), row_count)
            else:
                # Explicit user range genuinely too small for row_count
                # unique values: fall back to bounded-retry regeneration
                # and accept the same tiny-value-space tradeoff as
                # _deduplicate.
                values = [random.randint(min_val, max_val) for _ in range(row_count)]
                values = _deduplicate(
                    values,
                    lambda: random.randint(min_val, max_val),
                    column_name=unique_label,
                )
        else:
            values = [random.randint(min_val, max_val) for _ in range(row_count)]

    # -----------------------------------------------------
    # Floats / decimals
    # -----------------------------------------------------
    elif any(key in base_type for key in ["decimal", "numeric", "float", "double"]):
        if min_val is None:
            min_val = 0
        if max_val is None:
            max_val = 10_000
        values = [round(random.uniform(min_val, max_val), 2) for _ in range(row_count)]
        if ensure_unique:
            values = _deduplicate(
                values,
                lambda: round(random.uniform(min_val, max_val), 2),
                column_name=unique_label,
            )

    # -----------------------------------------------------
    # Booleans
    # -----------------------------------------------------
    elif "boolean" in base_type or "bool" in base_type:
        values = [random.choice([True, False]) for _ in range(row_count)]

    # -----------------------------------------------------
    # Dates
    # -----------------------------------------------------
    elif "date" in base_type and "time" not in base_type:
        values = [fake.date_between(start_date="-2y", end_date="today") for _ in range(row_count)]

    elif "time" in base_type and "stamp" not in base_type:
        values = [fake.time() for _ in range(row_count)]

    elif any(key in base_type for key in ["timestamp", "datetime"]):
        values = [_random_datetime().isoformat(sep=" ") for _ in range(row_count)]

    # -----------------------------------------------------
    # Untyped / generic string columns: infer intent from the
    # column name first (email, city, phone...), then fall back
    # to a literal Faker provider name, then to a generic value.
    # -----------------------------------------------------
    else:
        name_generator = _infer_by_name(column.name)
        if name_generator is not None:
            values = [name_generator() for _ in range(row_count)]
            values = (
                _deduplicate(values, name_generator, column_name=unique_label)
                if ensure_unique
                else values
            )
        else:
            try:
                values = [fake.format(base_type) for _ in range(row_count)]
            except (AttributeError, TypeError):
                if column.name.lower().endswith("_id") or ensure_unique:
                    values = [str(uuid.uuid4()) for _ in range(row_count)]
                else:
                    _stats_state["unmapped"].append((column.name, column.data_type))
                    values = [fake.sentence(nb_words=3) for _ in range(row_count)]

    # -----------------------------------------------------
    # Nullability
    # -----------------------------------------------------
    if not force_not_null and "not null" not in column.settings and "pk" not in column.settings:
        null_fraction = max(0, min(0.2, 1 - (row_count / (row_count + 50))))
        sample_size = int(row_count * null_fraction)
        if sample_size:
            for idx in random.sample(range(row_count), k=sample_size):
                values[idx] = column.default

    return values


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _deduplicate(
    values: list,
    generator: Callable[[], object],
    max_attempts: int = 20,
    column_name: Optional[str] = None,
) -> list:
    """
    Best-effort de-duplication for name-inferred values (e.g. unique emails).
    Retries collisions a bounded number of times, then accepts remaining
    duplicates rather than looping forever on a small value space -- recording
    the column so the CLI can report it instead of failing silently.
    """
    seen: set = set()
    result = []
    unresolved = 0
    for value in values:
        attempts = 0
        while value in seen and attempts < max_attempts:
            value = generator()
            attempts += 1
        if value in seen:
            unresolved += 1
        seen.add(value)
        result.append(value)

    if unresolved and column_name:
        _duplicate_unique_columns.append(f"{column_name}: {unresolved} duplicate value(s)")

    return result


def _random_datetime(start_days: int = -365, end_days: int = 0) -> datetime:
    start = datetime.now() + timedelta(days=start_days)
    end = datetime.now() + timedelta(days=end_days)
    delta = end - start
    random_second = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_second)
