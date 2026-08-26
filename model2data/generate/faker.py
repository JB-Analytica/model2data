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

# Column/table introspection helpers used by both generation and the
# CLI's post-run summary, so the two stay in sync.
_stats_state: dict[str, list[tuple[str, str]]] = {"unmapped": []}


def reset_stats() -> None:
    """Clear the record of columns that fell back to generic text."""
    _stats_state["unmapped"] = []


def get_unmapped_columns() -> list[tuple[str, str]]:
    """Return (column_name, data_type) pairs generated with a generic fallback."""
    return list(_stats_state["unmapped"])


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
) -> list:
    """
    Generate synthetic values for a single column.
    Respects FKs, uniqueness, and optional min/max hints in column notes.
    """
    if column.enum_values:
        return [random.choice(column.enum_values) for _ in range(row_count)]

    if fk_series is not None and not fk_series.empty:
        fk_values = fk_series.tolist()
        return [random.choice(fk_values) for _ in range(row_count)]

    dtype = column.data_type.lower()
    base_type = dtype.split("(")[0].strip()
    values: list = []

    # Extract min/max from note if present
    min_val = None
    max_val = None
    if column.note:
        min_val = column.note.get("min")
        max_val = column.note.get("max")

    # -----------------------------------------------------
    # UUIDs / hashes
    # -----------------------------------------------------
    if "uuid" in base_type or "hash" in base_type:
        values = [str(uuid.uuid4()) for _ in range(row_count)]
        if ensure_unique:
            values = _deduplicate(values, lambda: str(uuid.uuid4()))

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
                values = _deduplicate(values, lambda: random.randint(min_val, max_val))
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
            values = _deduplicate(values, lambda: round(random.uniform(min_val, max_val), 2))

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
            values = _deduplicate(values, name_generator) if ensure_unique else values
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
    if "not null" not in column.settings and "pk" not in column.settings:
        null_fraction = max(0, min(0.2, 1 - (row_count / (row_count + 50))))
        sample_size = int(row_count * null_fraction)
        if sample_size:
            for idx in random.sample(range(row_count), k=sample_size):
                values[idx] = column.default

    return values


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _deduplicate(values: list, generator: Callable[[], object], max_attempts: int = 20) -> list:
    """
    Best-effort de-duplication for name-inferred values (e.g. unique emails).
    Retries collisions a bounded number of times, then accepts remaining
    duplicates rather than looping forever on a small value space.
    """
    seen: set = set()
    result = []
    for value in values:
        attempts = 0
        while value in seen and attempts < max_attempts:
            value = generator()
            attempts += 1
        seen.add(value)
        result.append(value)
    return result


def _random_datetime(start_days: int = -365, end_days: int = 0) -> datetime:
    start = datetime.now() + timedelta(days=start_days)
    end = datetime.now() + timedelta(days=end_days)
    delta = end - start
    random_second = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_second)
