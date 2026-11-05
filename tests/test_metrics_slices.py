"""Metrics narrowed to dimension members and broken down by a dimension, over a run's frames."""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest
from test_metrics_values import SHOP, _frames

from model2data.metrics import breakdowns, filtered, known_values, load, resolve

METRICS = """
model2data-metrics: 0.1.0
model: shop
metrics:
  revenue: {measure: orders.amount, agg: sum}
  orders_n: {count: orders}
  paid: {count: orders, where: {orders.status: paid}}
  average_amount: {measure: orders.amount, agg: average}
  per_order: {ratio: {numerator: revenue, denominator: orders_n}}
  doubled: {expression: revenue * 2}
  customers_n: {count: customers}
  mixed: {expression: revenue + customers_n}
  lines_n: {count: lines}
"""


@pytest.fixture(scope="module")
def semantic():
    return resolve(SHOP, load(METRICS, SHOP))


def _members(found) -> dict:
    return {member.member: member.value for member in found.members}


def test_filtering_narrows_totals_months_and_ratios(semantic) -> None:
    values = known_values(
        filtered(semantic, {"orders.status": ["paid"]}).semantic, _frames()
    ).values

    assert values["revenue"].value == Decimal("10.5")
    assert values["orders_n"].value == 1
    assert values["per_order"].value == Decimal("10.5")
    assert values["doubled"].value == 21
    assert values["orders_n"].by_month == {"2025-01": 1, "2025-02": 0}


def test_a_filter_goes_on_top_of_a_metrics_own(semantic) -> None:
    narrowed = filtered(semantic, {"orders.status": ["cancelled"]}).semantic
    assert known_values(narrowed, _frames()).values["paid"].value == 0


def test_a_filter_reaches_along_a_join(semantic) -> None:
    narrowed = filtered(semantic, {"customers.country": ["BE"]}).semantic
    assert known_values(narrowed, _frames()).values["orders_n"].value == 1


def test_a_null_member_keeps_rows_where_it_is_null(semantic) -> None:
    narrowed = filtered(semantic, {"orders.status": [None]}).semantic
    assert known_values(narrowed, _frames()).values["orders_n"].value == 1


def test_null_and_a_value_together(semantic) -> None:
    narrowed = filtered(semantic, {"orders.status": ["paid", None]}).semantic
    assert known_values(narrowed, _frames()).values["orders_n"].value == 2


def test_dimensions_narrow_together(semantic) -> None:
    both = {"orders.status": ["paid"], "customers.country": ["NL"]}
    assert known_values(filtered(semantic, both).semantic, _frames()).values["orders_n"].value == 0


def test_no_members_keeps_everything(semantic) -> None:
    plain = known_values(semantic, _frames()).to_json()
    assert known_values(filtered(semantic, {}).semantic, _frames()).to_json() == plain
    assert (
        known_values(filtered(semantic, {"orders.status": []}).semantic, _frames()).to_json()
        == plain
    )


def test_filtering_leaves_the_model_it_was_given_alone(semantic) -> None:
    before = known_values(semantic, _frames()).to_json()
    filtered(semantic, {"orders.status": ["paid"]})
    assert known_values(semantic, _frames()).to_json() == before


def test_a_metric_the_dimension_cannot_reach_is_listed(semantic) -> None:
    found = filtered(semantic, {"orders.status": ["paid"]})

    assert "customers_n" in found.unfiltered
    assert "mixed" in found.unfiltered
    assert "revenue" not in found.unfiltered


def test_a_filter_on_an_unknown_column_is_refused(semantic) -> None:
    with pytest.raises(ValueError, match="orders.nope"):
        filtered(semantic, {"orders.nope": ["x"]})


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("orders.amount", "1) or (1=1"),
        ("orders.id", "1 or 1=1"),
        ("orders.placed_at", "tomorrow"),
    ],
)
def test_a_member_that_is_not_of_the_columns_kind_is_refused(semantic, path, value) -> None:
    with pytest.raises(ValueError, match="is not a value of"):
        filtered(semantic, {path: [value]})


def test_a_text_member_cannot_escape_its_quotes(semantic) -> None:
    narrowed = filtered(semantic, {"orders.status": ["paid' or '1'='1"]}).semantic
    assert known_values(narrowed, _frames()).values["orders_n"].value == 0


def test_an_additive_metric_by_member_adds_up_to_its_total(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("revenue", "orders.status")])[0]

    assert _members(found) == {
        "open": Decimal("40.25"),
        None: 30,
        "cancelled": 20,
        "paid": Decimal("10.5"),
    }
    total = known_values(semantic, _frames()).values["revenue"].value
    assert sum(_members(found).values()) == total


def test_members_are_largest_first(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("revenue", "orders.status")])[0]
    assert [member.member for member in found.members] == ["open", None, "cancelled", "paid"]


def test_a_breakdown_reaches_along_a_join(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("orders_n", "customers.country")])[0]
    # Order 3 has no customer and order 4 an orphan one: a left join keeps both, as null.
    assert _members(found) == {None: 2, "BE": 1, "NL": 1}


def test_a_metrics_own_filter_applies_per_member(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("paid", "orders.status")])[0]
    assert _members(found)["paid"] == 1
    assert _members(found)["open"] == 0


def test_a_ratio_is_computed_per_member_from_its_inputs(semantic) -> None:
    frames = _frames()
    frames["orders"]["status"] = ["paid", "paid", "open", "open"]
    found = breakdowns(semantic, frames, [("per_order", "orders.status")])[0]

    assert _members(found) == {"paid": Decimal("15.25"), "open": Decimal("35.125")}


def test_a_derived_metric_is_computed_per_member_from_its_inputs(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("doubled", "orders.status")])[0]
    assert _members(found)["paid"] == 21


def test_a_breakdown_of_a_filtered_metric(semantic) -> None:
    narrowed = filtered(semantic, {"orders.status": ["paid", "open"]}).semantic
    found = breakdowns(narrowed, _frames(), [("orders_n", "orders.status")])[0]

    assert _members(found)["paid"] == 1
    assert _members(found)["cancelled"] == 0


def _many() -> dict[str, pd.DataFrame]:
    frames = _frames()
    frames["orders"] = pd.DataFrame(
        {
            "id": list(range(1, 7)),
            "customer_id": [None] * 6,
            "store_id": [None] * 6,
            "status": list("abcdef"),
            "amount": [60.0, 50.0, 40.0, 30.0, 20.0, 10.0],
            "placed_at": [None] * 6,
            "note": [None] * 6,
        }
    )
    return frames


def test_past_the_cap_an_additive_metric_sums_the_rest(semantic) -> None:
    found = breakdowns(semantic, _many(), [("revenue", "orders.status")], limit=2)[0]

    assert [member.member for member in found.members] == ["a", "b"]
    assert found.other == 100
    assert found.hidden == 4
    assert found.note is None


def test_past_the_cap_any_other_metric_says_what_it_left_out(semantic) -> None:
    found = breakdowns(semantic, _many(), [("average_amount", "orders.status")], limit=2)[0]

    assert len(found.members) == 2
    assert found.other is None
    assert found.hidden == 4
    assert "4 smaller members left out" in (found.note or "")


def test_a_dimension_the_metric_cannot_reach_says_so(semantic) -> None:
    found = breakdowns(semantic, _frames(), [("customers_n", "orders.status")])[0]

    assert found.members == []
    assert "no single many-to-one path" in (found.note or "")


def test_a_breakdown_of_something_unknown_is_refused(semantic) -> None:
    with pytest.raises(ValueError, match="nope"):
        breakdowns(semantic, _frames(), [("nope", "orders.status")])
    with pytest.raises(ValueError, match="orders.nope"):
        breakdowns(semantic, _frames(), [("revenue", "orders.nope")])


def test_the_same_frames_give_the_same_breakdown(semantic) -> None:
    first = breakdowns(semantic, _frames(), [("revenue", "orders.status")])
    assert first == breakdowns(semantic, _frames(), [("revenue", "orders.status")])
