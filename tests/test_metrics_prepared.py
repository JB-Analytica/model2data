"""Prepared frames: the same values as plain frames, each column read once, safe across threads."""

from __future__ import annotations

import datetime as dt
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from test_metrics_values import SHOP, _frames
from typer.testing import CliRunner

import model2data.cli as cli
from model2data.generate.core import generate_data_from_dbml
from model2data.metrics import (
    GRAINS,
    PreparedFrames,
    breakdowns,
    by_period,
    filtered,
    is_additive,
    known_values,
    load,
    period_label,
    prepare,
    resolve,
    slices,
)
from model2data.metrics import values as values_module
from model2data.model import load as load_model
from model2data.model import to_engine

SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"

# A count, a sum, a filtered count, a distinct count, an average, a median, a ratio of two
# filtered metrics, derived metrics over one and two tables; orders are dated by a timestamp
# and customers by a date.
METRICS = """
model2data-metrics: 0.1.0
model: shop
metrics:
  revenue: {measure: orders.amount, agg: sum}
  orders_n: {count: orders}
  paid: {count: orders, where: {orders.status: paid}}
  buyers: {measure: orders.customer_id, agg: count_distinct}
  average_amount: {measure: orders.amount, agg: average}
  median_price: {measure: lines.price, agg: median}
  per_order: {ratio: {numerator: revenue, denominator: orders_n}}
  paid_share: {ratio: {numerator: paid, denominator: orders_n}}
  doubled: {expression: revenue * 2}
  customers_n: {count: customers}
  joined: {count: customers, time: customers.joined_on}
  mixed: {expression: revenue + customers_n}
  lines_n: {count: lines}
"""

WANTED = [
    ("revenue", "orders.status"),
    ("per_order", "customers.country"),
    ("buyers", "orders.status"),
    ("doubled", "customers.vip"),
    ("lines_n", "orders.status"),
    ("customers_n", "orders.status"),  # no path: a note, not a query
]

VIEWS = [
    ({"orders.status": ["paid", None]}, None),
    ({"customers.country": ["BE"]}, None),
    ({}, {"orders.placed_at": ("2025-01-01", "2025-01-20")}),
    ({"orders.status": ["open"]}, {"customers.joined_on": (None, "2025-01-09")}),
]


@pytest.fixture(scope="module")
def semantic():
    return resolve(SHOP, load(METRICS, SHOP))


def _answers(semantic, frames) -> list:
    """Every public computation, in a form that compares exactly."""
    out: list = [known_values(semantic, frames, seed=3, as_of="2025-03-01").to_json()]
    out += [by_period(semantic, frames, grain) for grain in GRAINS]
    out.append(breakdowns(semantic, frames, WANTED))
    out.append(breakdowns(semantic, frames, WANTED[:2], limit=1))
    for members, ranges in VIEWS:
        view = filtered(semantic, members, ranges=ranges)
        out.append(view.unfiltered)
        out.append(known_values(view.semantic, frames).to_json())
        out.append(by_period(view.semantic, frames, "week"))
        out.append(breakdowns(view.semantic, frames, WANTED[:3]))
    return out


def test_every_function_gives_the_same_from_prepared_frames(semantic) -> None:
    plain = _answers(semantic, _frames())
    with prepare(_frames()) as prepared:
        first = _answers(semantic, prepared)
        again = _answers(semantic, prepared)  # from what the first round kept
    assert first == plain
    assert again == plain


def test_the_coffee_example_gives_the_same_from_prepared_frames() -> None:
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    semantic = resolve(model, load(SPEC / "coffee_webshop.metrics.yml", model))
    inputs = to_engine(model)
    frames = generate_data_from_dbml(
        tables=inputs.tables, refs=inputs.refs, base_rows=150, seed=11, as_of=date(2026, 3, 15)
    )
    wanted = [("revenue", "orders.channel"), ("average_order_value", "customers.country")]

    def answers(given) -> list:
        view = filtered(semantic, {"orders.channel": ["web"]}).semantic
        return [
            known_values(semantic, given).to_json(),
            by_period(semantic, given, "quarter"),
            breakdowns(semantic, given, wanted),
            known_values(view, given).to_json(),
            by_period(view, given, "month"),
        ]

    with prepare(frames) as prepared:
        assert answers(prepared) == answers(frames)


def test_a_run_writes_the_same_values_from_prepared_frames(tmp_path, monkeypatch) -> None:
    """The values a run writes as `metric_values.json`, byte for byte."""
    compared = []

    def checked(semantic, frames, **run):
        written = known_values(semantic, frames, **run)
        with prepare(frames) as prepared:
            compared.append(known_values(semantic, prepared, **run).to_json() == written.to_json())
        return written

    monkeypatch.setattr(cli, "known_values", checked)
    monkeypatch.chdir(tmp_path)
    examples = Path(__file__).resolve().parent.parent / "examples"
    result = CliRunner().invoke(
        cli.app,
        ["generate", "-f", str(examples / "ecommerce.model2data.yml"), "--rows", "150"]
        + ["--metrics", str(examples / "ecommerce.metrics.yml"), "--seed", "3"]
        + ["--as-of", "2026-03-15", "--name", "prepared"],
    )
    assert result.exit_code == 0, result.output
    assert compared == [True]


@pytest.fixture
def conversions(monkeypatch) -> Counter:
    """How many times each (table, column) is turned into text."""
    counted: Counter = Counter()
    text_columns = values_module._text_columns
    tables = {id(frame): key for key, frame in _FRAMES.items()}

    def counting(frame, columns):
        for column in columns:
            counted[(tables[id(frame)], column)] += 1
        return text_columns(frame, columns)

    monkeypatch.setattr(values_module, "_text_columns", counting)
    return counted


_FRAMES = _frames()


def test_prepared_frames_convert_each_column_once(semantic, conversions) -> None:
    with prepare(_FRAMES) as prepared:
        _answers(semantic, prepared)
        _answers(semantic, prepared)
        # A table widened for a later call replaces the narrower one: one table per key.
        tables = prepared._connection.execute("select table_name from duckdb_tables()")
        assert len(tables.fetchall()) == len({table for table, _ in conversions})
    assert conversions
    assert set(conversions.values()) == {1}, conversions
    # Only the columns some query read: never the customers' email.
    assert ("customers", "email") not in conversions


def test_plain_frames_convert_on_every_call(semantic, conversions) -> None:
    by_period(semantic, _FRAMES, "month")
    by_period(semantic, _FRAMES, "quarter")
    assert conversions[("orders", "placed_at")] == 2


def test_a_column_needed_later_is_converted_then(semantic, conversions) -> None:
    with prepare(_FRAMES) as prepared:
        known_values(semantic, prepared)
        assert ("customers", "country") not in conversions
        breakdowns(semantic, prepared, [("revenue", "customers.country")])
        breakdowns(semantic, prepared, [("revenue", "customers.country")])
    assert conversions[("customers", "country")] == 1
    assert conversions[("orders", "amount")] == 1


def test_calls_from_many_threads_agree_and_convert_once(semantic, conversions) -> None:
    expected = _answers(semantic, _FRAMES)
    conversions.clear()
    start = threading.Barrier(8)

    with prepare(_FRAMES) as prepared:

        def ask(_: int) -> list:
            start.wait()  # all threads ask at once, before anything is converted
            return _answers(semantic, prepared)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(ask, range(8)))
    assert all(result == expected for result in results)
    assert set(conversions.values()) == {1}, conversions


def test_closed_frames_refuse_to_compute(semantic) -> None:
    with prepare(_frames()) as prepared:
        known_values(semantic, prepared)
        assert "tables loaded" in repr(prepared)
    assert prepared.closed
    assert repr(prepared).endswith("closed>")
    with pytest.raises(ValueError, match="closed"):
        known_values(semantic, prepared)
    prepared.close()  # closing twice is fine


def test_prepared_frames_are_the_frames(semantic) -> None:
    frames = _frames()
    prepared = prepare(frames)
    try:
        assert isinstance(prepared, PreparedFrames)
        assert prepare(prepared) is prepared
        assert set(prepared) == set(frames) and len(prepared) == len(frames)
        assert prepared["orders"] is frames["orders"]
        assert "orders" in prepared and "nowhere" not in prepared
    finally:
        prepared.close()


def test_a_table_a_metric_reads_must_be_there(semantic) -> None:
    frames = _frames()
    del frames["lines"]
    with prepare(frames) as prepared, pytest.raises(ValueError, match="'lines'"):
        known_values(semantic, prepared)


def test_additive_metrics(semantic) -> None:
    body = METRICS.split("\nmetrics:\n")[1]
    ours = {line.split(":")[0].strip() for line in body.strip().splitlines()}
    additive = {name for name in ours if is_additive(semantic.metrics[name])}
    assert additive == {"revenue", "orders_n", "paid", "customers_n", "joined", "lines_n"}
    assert slices._additive is is_additive


@pytest.mark.parametrize(
    "start, grain, label",
    [
        (dt.date(2025, 1, 6), "day", "2025-01-06"),
        (dt.date(2025, 1, 6), "week", "2025-01-06"),
        (dt.date(2025, 2, 17), "month", "2025-02"),
        (dt.date(2025, 8, 1), "quarter", "2025-Q3"),
        (dt.date(2025, 12, 31), "quarter", "2025-Q4"),
        (dt.date(2025, 4, 1), "year", "2025"),
    ],
)
def test_period_labels(start, grain, label) -> None:
    assert period_label(start, grain) == label


def test_period_label_refuses_a_grain_that_is_not_one() -> None:
    with pytest.raises(ValueError, match="fortnight"):
        period_label(dt.date(2025, 1, 6), "fortnight")
    assert slices._period is period_label


def test_by_period_labels_are_period_labels(semantic) -> None:
    for grain in GRAINS:
        months = by_period(semantic, _frames(), grain)["orders_n"] or {}
        for label in months:
            start = pd.Timestamp(label if grain != "quarter" else label.replace("-Q", "Q"))
            assert period_label(start.date(), grain) == label


def _large() -> dict[str, pd.DataFrame]:
    """A few thousand rows with an index that is neither 0..n-1 nor in order, and amounts
    whose float sum depends on the order they are added in."""
    import numpy as np

    rng = np.random.default_rng(5)
    n = 4000
    index = rng.permutation(n) * 7 + 1000
    frames = _frames()
    frames["customers"] = pd.DataFrame(
        {
            "id": range(1, 201),
            "email": [f"{i}@x" for i in range(200)],
            "country": rng.choice(["BE", "NL", "", "null", "FR"], 200),
            "vip": rng.choice([True, False], 200),
            "tier": rng.choice(["1", "2"], 200),
            "joined_on": pd.date_range("2024-01-01", periods=200, freq="D").strftime("%Y-%m-%d"),
        },
        index=rng.permutation(200) + 50,
    )
    frames["orders"] = pd.DataFrame(
        {
            "id": pd.array(range(1, n + 1), dtype="Int64"),
            "customer_id": pd.array(rng.integers(1, 230, n), dtype="Int64"),
            "store_id": pd.array([None] * n, dtype="Int64"),
            "status": rng.choice(["paid", "cancelled", "open", "null"], n),
            "amount": rng.random(n) * 1000 / 3,
            "placed_at": (
                pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 400 * 86400, n), "s")
            ).strftime("%Y-%m-%d %H:%M:%S"),
            "note": rng.choice(["", "rush", "null"], n),
        },
        index=index,
    )
    return frames


def test_a_table_widened_in_steps_gives_the_same_values() -> None:
    frames = _large()
    steps = [
        "  revenue: {measure: orders.amount, agg: sum}\n",
        "  paid: {measure: orders.amount, agg: average, where: {orders.status: paid}}\n",
        "  dated: {measure: orders.amount, agg: sum, time: orders.placed_at}\n"
        "  buyers: {measure: orders.customer_id, agg: count_distinct}\n",
    ]
    header = "model2data-metrics: 0.1.0\nmodel: shop\nmetrics:\n"
    with prepare(frames) as prepared:
        for step in range(1, len(steps) + 1):
            narrow = resolve(SHOP, load(header + "".join(steps[:step]), SHOP))
            assert (
                known_values(narrow, prepared).to_json() == known_values(narrow, frames).to_json()
            )
            assert by_period(narrow, prepared, "week") == by_period(narrow, frames, "week")
            wanted = [("revenue", "orders.status"), ("revenue", "customers.country")]
            assert breakdowns(narrow, prepared, wanted) == breakdowns(narrow, frames, wanted)
        # Three widenings of orders, and still one table per key, without the row number
        # visible to a query.
        orders_columns = prepared._loaded["orders"][1]
        assert {"amount", "status", "placed_at", "customer_id"} <= set(orders_columns)
        with prepared._relations({"m2d_0": ("orders", ["amount"])}) as cursor:
            names = [row[0] for row in cursor.execute("describe m2d_0").fetchall()]
        assert "__m2d_row" not in names and "amount" in names


def test_the_row_number_never_takes_a_users_column_name() -> None:
    frame = pd.DataFrame({"__M2D_ROW": [1], "___m2d_row": [2], "x": [3]})
    assert values_module._row_column(frame) == "____m2d_row"
