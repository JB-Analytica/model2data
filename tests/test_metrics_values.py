"""Known values: every metric over a run's tables, with SQL's nulls, by hand on a tiny shop.

The frames below are small enough to check each number on paper; the comment
beside each assertion does.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.metrics import known_values, load, resolve
from model2data.metrics.values import _rounded
from model2data.model import load as load_model
from model2data.model import to_engine

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"
SHOP = load_model(FIXTURES / "shop.model2data.yml")


def _frames() -> dict[str, pd.DataFrame]:
    customers = pd.DataFrame(
        {
            "id": [1, 2, 3, 4],
            "email": ["a@x", "b@x", "c@x", "d@x"],
            "country": ["BE", "NL", "", "null"],  # the last two read as null, as dbt reads them
            "vip": [True, False, None, None],
            "tier": ["1", "2", None, "1"],
            "joined_on": ["2024-12-03", "2025-01-09", "2025-01-30", None],
        }
    )
    orders = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3, 4], dtype="Int64"),
            "customer_id": pd.array([1, 2, None, 9], dtype="Int64"),  # 9 is an orphan
            "store_id": pd.array([None] * 4, dtype="Int64"),
            "status": ["paid", "cancelled", None, "open"],
            "amount": [10.5, 20.0, 30.0, 40.25],
            "placed_at": [
                "2025-01-05 10:00:00",
                "2025-01-20 23:59:59",
                "2025-02-01 00:00:00",
                None,
            ],
            "note": ["", "rush", None, "null"],
        }
    )
    lines = pd.DataFrame(
        {
            "id": pd.array([1, 2, 3, 4, 5], dtype="Int64"),
            "order_id": pd.array([1, 1, 2, 3, 4], dtype="Int64"),
            "qty": pd.array([2, 3, 1, None, 5], dtype="Int64"),
            "price": [1.1, 2.2, None, 4.0, 5.5],
        }
    )
    logs = pd.DataFrame({"id": [1, 2], "level": [3, 7]})
    empty = {
        "stores": pd.DataFrame({"id": [], "region": []}),
        "transfers": pd.DataFrame({"id": [], "sender_id": [], "receiver_id": [], "amount": []}),
        "slots": pd.DataFrame({"store_id": [], "day": [], "capacity": []}),
        "visits": pd.DataFrame({"id": [], "store_id": [], "day": [], "people": []}),
    }
    return {"customers": customers, "orders": orders, "lines": lines, "logs": logs, **empty}


METRICS = """
model2data-metrics: 0.1.0
model: shop
metrics:
  not_cancelled: {count: orders, where: {orders.status: {ne: cancelled}}}
  not_in_cancelled: {count: orders, where: {orders.status: {not_in: [cancelled]}}}
  no_status: {count: orders, where: {orders.status: {is_null: true}}}
  some_status: {count: orders, where: {orders.status: {is_null: false}}}
  vip_qty: {measure: lines.qty, agg: sum, where: {customers.vip: true}}
  not_vip_qty: {measure: lines.qty, agg: sum, where: {customers.vip: {ne: true}}}
  be_lines: {count: lines, where: {customers.country: [BE]}}
  no_country_lines: {count: lines, where: {customers.country: {is_null: true}}}
  paid_or_big: {count: lines, where: {any: [{orders.status: paid}, {lines.qty: {gt: 4}}]}}
  both: {count: lines, where: {any: [{all: [{orders.status: paid}, {lines.qty: {gte: 3}}]}, {lines.qty: 1}]}}
  january: {count: orders, where: {orders.placed_at: {between: ["2025-01-01", "2025-01-20 23:59:59"]}}}
  joined_2025: {count: customers, where: {customers.joined_on: {gte: "2025-01-01"}}}
  tier_one: {count: customers, where: {customers.tier: 1}}
  noted: {count: orders, where: {orders.note: {is_null: false}}}
  price_avg: {measure: lines.price, agg: average}
  price_median: {measure: lines.price, agg: median}
  amount_min: {measure: orders.amount, agg: min}
  amount_max: {measure: orders.amount, agg: max}
  buyers: {measure: orders.customer_id, agg: count_distinct}
  statuses: {measure: orders.status, agg: count}
  nothing: {measure: orders.amount, agg: sum, where: {orders.amount: {gt: 99}}}
  per_order: {ratio: {numerator: orders_amount, denominator: orders_count}}
  zero_division: {expression: orders_amount / (lines_qty - 11)}
  third: {expression: no_status / 3}
  mixed: {expression: lines_qty + customers_n}
  customers_n: {count: customers}
  avg_twice: {expression: price_avg * 2}
  undated: {measure: logs.level, agg: sum}
  with_undated: {expression: undated + orders_count}
"""


@pytest.fixture(scope="module")
def values():
    semantic = resolve(SHOP, load(METRICS, SHOP))
    return known_values(semantic, _frames(), seed=3, as_of="2025-03-01").values


@pytest.mark.parametrize(
    "name, expected",
    [
        ("orders_amount", Decimal("100.75")),  # 10.5 + 20 + 30 + 40.25
        ("orders_count", 4),
        ("lines_qty", 11),  # 2 + 3 + 1 + 5, the null skipped
        ("lines_count", 5),
        ("not_cancelled", 2),  # paid and open: the null status fails `ne`
        ("not_in_cancelled", 2),
        ("no_status", 1),
        ("some_status", 3),
        ("vip_qty", 5),  # lines 1 and 2, of order 1, of customer 1
        ("not_vip_qty", 1),  # line 3 (customer 2); a null vip, or no customer, fails `ne`
        ("be_lines", 2),
        ("no_country_lines", 2),  # line 4's order has no customer, line 5's is an orphan
        ("paid_or_big", 3),  # lines 1 and 2 (paid), line 5 (qty 5)
        ("both", 2),  # line 2 (paid, 3), line 3 (qty 1)
        ("january", 2),  # both bounds inclusive
        ("joined_2025", 2),
        ("tier_one", 2),  # an integer member written as a number
        ("noted", 1),  # "" and "null" read as null, as dbt reads them
        ("price_avg", Decimal("3.2")),  # (1.1 + 2.2 + 4.0 + 5.5) / 4
        ("price_median", Decimal("3.1")),  # between 2.2 and 4.0
        ("amount_min", Decimal("10.5")),
        ("amount_max", Decimal("40.25")),
        ("buyers", 3),  # 1, 2 and the orphan 9
        ("statuses", 3),
        ("nothing", None),  # a sum over no rows is null
        ("per_order", Decimal("25.1875")),  # 100.75 / 4
        ("zero_division", None),  # 11 - 11 is zero
        ("third", Decimal("0.333333")),
        ("mixed", 15),  # 11 + 4
        ("avg_twice", Decimal("6.4")),
        ("undated", 10),
        ("with_undated", 14),
    ],
)
def test_known_values(values, name, expected):
    assert values[name].value == expected


def test_values_by_month(values):
    # Order 4 has no time: in the total, in no month.
    assert values["orders_amount"].by_month == {"2025-01": Decimal("30.5"), "2025-02": 30}
    # Lines are dated by their order; February's only line has no qty, so its sum is null.
    assert values["lines_qty"].by_month == {"2025-01": 6, "2025-02": None}
    assert values["lines_qty"].time == "orders.placed_at"
    # A filter that keeps no row in a month: a count is 0.
    assert values["not_cancelled"].by_month == {"2025-01": 1, "2025-02": 0}
    # A ratio per month is its inputs' per month.
    assert values["per_order"].by_month == {"2025-01": Decimal("15.25"), "2025-02": 30}
    # Inputs dated differently: each in its own months, a missing count 0, a missing sum null.
    assert values["customers_n"].by_month == {"2024-12": 1, "2025-01": 2}
    assert values["mixed"].by_month == {"2024-12": None, "2025-01": 8, "2025-02": None}
    assert values["third"].by_month == {"2025-01": 0, "2025-02": Decimal("0.333333")}


def test_a_metric_without_time_has_no_months(values):
    assert values["undated"].by_month is None and values["undated"].time is None
    assert values["with_undated"].by_month is None


def test_the_json_is_stable_text():
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: shop\ninfer: false\nmetrics:\n"
        "  revenue: {label: Revenue, measure: orders.amount, agg: sum}\n"
        "  share: {expression: revenue / 3}\n"
        "  levels: {measure: logs.level, agg: max}\n",
        SHOP,
    )
    values = known_values(resolve(SHOP, metrics), _frames(), seed=3, as_of="2025-03-01")
    assert values.to_json() == (
        """{
  "model2data-metrics": "0.1.0",
  "model": "shop",
  "run": {
    "seed": 3,
    "as_of": "2025-03-01"
  },
  "rounding": "counts, and sums, minimums and maximums of integer columns, are exact; every other value is rounded to 6 decimal places, half to even",
  "metrics": {
    "revenue": {
      "kind": "simple",
      "label": "Revenue",
      "value": 100.75,
      "time": "orders.placed_at",
      "by_month": {
        "2025-01": 30.5,
        "2025-02": 30
      }
    },
    "share": {
      "kind": "derived",
      "label": "Share",
      "value": 33.583333,
      "by_month": {
        "2025-01": 10.166667,
        "2025-02": 10
      }
    },
    "levels": {
      "kind": "simple",
      "label": "Levels",
      "value": 7
    }
  }
}
"""
    )
    values.seed = values.as_of = None
    assert '"run"' not in values.to_json()


def test_an_empty_mapping_is_written_as_such():
    from model2data.metrics.values import _dump

    assert _dump({"a": {}}) == '{\n  "a": {}\n}'


@pytest.mark.parametrize(
    "raw, exact, written",
    [
        (Decimal("1200.000000000"), False, "1200"),
        (Decimal("0.0000004"), False, "0"),
        (Decimal("0.0000005"), False, "0"),  # half to even
        (Decimal("0.0000015"), False, "0.000002"),
        (2.675, False, "2.675"),
        (1 / 3, False, "0.333333"),
        (Decimal("-1.50"), False, "-1.5"),
        (7, False, "7"),
        (Decimal("12"), True, "12"),
        (None, True, "None"),
    ],
)
def test_rounding(raw, exact, written):
    value = _rounded(raw, exact)
    assert (format(value, "f") if isinstance(value, Decimal) else str(value)) == written


def test_a_table_a_metric_reads_must_have_rows():
    frames = _frames()
    del frames["logs"]
    semantic = resolve(
        SHOP, load("model2data-metrics: 0.1.0\nmodel: shop\nmetrics:\n  l: {count: logs}\n", SHOP)
    )
    with pytest.raises(ValueError, match="no generated rows for table 'logs'"):
        known_values(semantic, frames)


def test_the_reference_example_over_generated_data():
    """Recomputes three of the coffee example's metrics with pandas, by their definitions."""
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    metrics = load(SPEC / "coffee_webshop.metrics.yml", model)
    inputs = to_engine(model)
    frames = generate_data_from_dbml(
        tables=inputs.tables, refs=inputs.refs, base_rows=150, seed=11, as_of=date(2026, 3, 15)
    )
    values = known_values(resolve(model, metrics), frames).values
    orders = frames["orders"]
    kept = orders[orders["status"].isin(["paid", "shipped", "delivered"])]
    assert values["revenue"].value == _rounded(
        Decimal(str(round(kept["total_amount"].sum(), 6))), False
    )
    assert values["orders"].value == orders["id"].nunique()
    assert values["order_lines"].value == len(frames["order_items"])
    lines = frames["order_items"].merge(
        orders, left_on="order_id", right_on="id", suffixes=("", "_o")
    )
    months = pd.to_datetime(lines["order_date"]).dt.strftime("%Y-%m").value_counts().to_dict()
    assert values["order_lines"].by_month == dict(sorted(months.items()))
    orders_count = values["orders"].value
    assert isinstance(orders_count, int)
    assert values["average_order_value"].value == _rounded(
        Decimal(str(values["revenue"].value)) / orders_count, False
    )
