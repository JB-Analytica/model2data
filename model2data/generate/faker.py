from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from faker import Faker

from model2data.parse.dbml import ColumnDef

fake = Faker()


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

    # -----------------------------------------------------
    # Integers
    # -----------------------------------------------------
    elif any(key in base_type for key in ["int", "integer", "bigint", "smallint"]):
        # Use note values if present, otherwise defaults
        if min_val is None:
            min_val = 0
        if max_val is None:
            max_val = 100
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
    # Fallback to Faker providers
    # -----------------------------------------------------
    else:
        try:
            values = [fake.format(base_type) for _ in range(row_count)]
        except (AttributeError, TypeError):
            if column.name.lower().endswith("_id") or ensure_unique:
                values = [str(uuid.uuid4()) for _ in range(row_count)]
            else:
                values = [fake.sentence(nb_words=3) for _ in range(row_count)]

    # -----------------------------------------------------
    # Nullability
    # -----------------------------------------------------
    if "not null" not in column.settings:
        null_fraction = max(0, min(0.2, 1 - (row_count / (row_count + 50))))
        sample_size = int(row_count * null_fraction)
        if sample_size:
            for idx in random.sample(range(row_count), k=sample_size):
                values[idx] = None

    return values


# ---------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------
def _random_datetime(start_days: int = -365, end_days: int = 0) -> datetime:
    start = datetime.now() + timedelta(days=start_days)
    end = datetime.now() + timedelta(days=end_days)
    delta = end - start
    random_second = random.randint(0, int(delta.total_seconds()))
    return start + timedelta(seconds=random_second)
