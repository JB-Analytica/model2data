"""A date's own `min` holds where the row's other dates come long before it."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.model import from_dict, to_engine

AS_OF = datetime(2026, 10, 1)
STARTED = {
    "type": "timestamp",
    "not_null": True,
    "generate": {"min": "2025-10-01", "max": "2025-12-31"},
}
WINDOW = {"min": "2026-09-01", "max": "2026-09-30"}


def _rows(kind: str, *, seed: int = 3, name: str = "canceled_at", **generate: Any) -> pd.DataFrame:
    columns = {
        "id": {"type": "int", "pk": True},
        "status": {"type": "status", "not_null": True},
        "started_at": STARTED,
        name: {"type": kind, "generate": {**WINDOW, **generate}},
    }
    document = {
        "model2data": "0.9.0",
        "enums": {"status": ["active", "canceled"]},
        "tables": {"subscriptions": {"columns": columns}},
    }
    engine = to_engine(from_dict(document))
    data = generate_data_from_dbml(
        tables=engine.tables, refs=engine.refs, base_rows=120, seed=seed, as_of=AS_OF
    )
    return data["subscriptions"]


@pytest.mark.parametrize("kind", ["timestamp", "date"])
@pytest.mark.parametrize(
    "generate",
    [
        {},  # ordered by its name: started_at, canceled_at
        {"after": "started_at"},
        {"when": {"status": ["canceled"]}},
        {"when": {"status": ["canceled"]}, "after": "started_at"},
    ],
    ids=["by name", "after", "when", "when and after"],
)
def test_a_window_that_opens_after_the_date_it_follows_is_kept(kind, generate):
    values = pd.to_datetime(_rows(kind, **generate)["canceled_at"]).dropna()
    assert len(values) > 10
    assert values.min() >= pd.Timestamp("2026-09-01")
    assert values.max() < pd.Timestamp("2026-10-01")
    assert values.nunique() > 5


def test_inside_the_window_the_date_still_follows():
    rows = _rows(
        "timestamp", name="ended_at", min="2025-10-01", max="2026-09-30", after="started_at"
    )
    ended = pd.to_datetime(rows["ended_at"]).dropna()
    assert (ended >= pd.to_datetime(rows.loc[ended.index, "started_at"])).all()
