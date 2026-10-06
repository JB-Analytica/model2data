"""The semantic model: paths, inferred metrics, dimensions, entities and resolved metrics."""

from pathlib import Path

import pytest

from model2data.metrics import (
    Graph,
    Join,
    Metric,
    Metrics,
    MetricsError,
    inferred_metrics,
    load,
    resolve,
)
from model2data.metrics.graph import describe
from model2data.metrics.infer import metric_name
from model2data.model import load as load_model

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "metrics"
SHOP = load_model(FIXTURES / "shop.model2data.yml")


def _metrics(body: str) -> Metrics:
    return load("model2data-metrics: 0.1.0\nmodel: shop\n" + body, SHOP)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def test_paths_follow_foreign_keys_from_child_to_parent():
    graph = Graph(SHOP)
    to_orders = Join("lines", ("order_id",), "orders", ("id",))
    to_customers = Join("orders", ("customer_id",), "customers", ("id",))
    assert graph.paths("lines", "customers") == [(to_orders, to_customers)]
    assert graph.paths("lines", "lines") == [()]
    assert graph.paths("customers", "orders") == []
    assert graph.paths("logs", "orders") == []
    assert describe((to_orders, to_customers)) == (
        "lines.order_id -> orders.id, then orders.customer_id -> customers.id"
    )
    assert describe(()) == "the table itself"


def test_two_paths_are_found_and_the_search_stops_there():
    graph = Graph(SHOP)
    assert len(graph.paths("transfers", "customers")) == 2
    assert len(graph.paths("transfers", "customers", limit=1)) == 1


def test_a_composite_foreign_key_is_one_step():
    assert Graph(SHOP).paths("visits", "stores") == [
        (
            Join("visits", ("store_id", "day"), "slots", ("store_id", "day")),
            Join("slots", ("store_id",), "stores", ("id",)),
        )
    ]


def test_a_cycle_of_references_is_walked_once():
    model = load_model(
        """
model2data: 0.4.0
tables:
  a:
    columns:
      id: {type: int, pk: true}
      b_id: {type: int, references: b.id}
  b:
    columns:
      id: {type: int, pk: true}
      a_id: {type: int, references: a.id}
      c_id: {type: int, references: c.id}
  c:
    columns:
      id: {type: int, pk: true}
      at: date
"""
    )
    graph = Graph(model)
    assert len(graph.paths("a", "c")) == 1
    assert graph.first_time_column("a") == ("c.at", graph.paths("a", "c")[0])


def test_the_default_time_column():
    graph = Graph(SHOP)
    assert graph.first_time_column("orders") == ("orders.placed_at", ())
    # lines has no date: its order's, one step away, before the customer's, two steps away
    found = graph.first_time_column("lines")
    assert found is not None and found[0] == "orders.placed_at"
    # stores, and anything it reaches, has none
    assert graph.first_time_column("stores") is None
    # transfers reaches customers along two paths, so customers cannot date it
    assert graph.first_time_column("transfers") is None
    assert graph.temporal_columns("customers") == ["joined_on"]


def test_an_enum_named_like_a_date_is_not_a_time():
    model = load_model(
        """
model2data: 0.4.0
enums:
  update_kind: [a, b]
tables:
  t:
    columns:
      id: {type: int, pk: true}
      kind: update_kind
"""
    )
    assert Graph(model).temporal_columns("t") == []


# ---------------------------------------------------------------------------
# Inferred metrics
# ---------------------------------------------------------------------------
def test_inferred_metrics_are_named_as_the_studio_names_them():
    inferred = inferred_metrics(SHOP)
    assert list(inferred) == [
        "orders_amount",
        "orders_count",
        "lines_qty",
        "lines_price_average",
        "lines_count",
    ]
    amount = inferred["orders_amount"]
    assert (amount.kind, amount.measure, amount.agg, amount.inferred) == (
        "simple",
        "orders.amount",
        "sum",
        True,
    )
    assert amount.label == "Orders amount"
    assert amount.description == "Sum of orders.amount."
    average = inferred["lines_price_average"]
    assert average.label == "Lines price (average)"
    assert average.description == "Average of lines.price."
    assert inferred["orders_count"] == Metric(
        kind="count",
        count="orders",
        label="Orders count",
        description="Rows of orders.",
        inferred=True,
    )


def test_a_row_count_for_facts_and_tables_with_a_measure_only():
    model = load_model(
        """
model2data: 0.4.0
tables:
  events:
    role: fact
    columns:
      id: {type: int, pk: true}
  prices:
    role: dimension
    columns:
      id: {type: int, pk: true}
      amount: {type: numeric, measure: max}
  plain:
    columns:
      id: {type: int, pk: true}
      off: {type: int, measure: false}
"""
    )
    assert list(inferred_metrics(model)) == ["events_count", "prices_amount_max"]


@pytest.mark.parametrize(
    "text, name",
    [
        ("Order Items__Total", "order_items_total"),
        ("raw.orders_amount", "raw_orders_amount"),
        ("2024_sales", "c_2024_sales"),
        ("x", "x_x"),
        ("___", "unnamed"),
        ("Coût €", "co_t"),
    ],
)
def test_metric_names(text, name):
    assert metric_name(text) == name


def test_a_name_taken_twice_gets_a_number():
    model = load_model(
        """
model2data: 0.4.0
tables:
  a b:
    columns:
      c: {type: int, measure: true}
  a_b:
    columns:
      c: {type: int, measure: true}
"""
    )
    assert list(inferred_metrics(model)) == ["a_b_c", "a_b_count", "a_b_c_2", "a_b_count_2"]


def test_an_explicit_metric_replaces_the_inferred_one_and_comes_first():
    semantic = resolve(
        SHOP,
        _metrics(
            "metrics:\n  lines_qty: {measure: lines.qty, agg: max}\n  extra: {count: stores}\n"
        ),
    )
    assert list(semantic.metrics) == [
        "lines_qty",
        "extra",
        "orders_amount",
        "orders_count",
        "lines_price_average",
        "lines_count",
    ]
    assert semantic.metrics["lines_qty"].agg == "max"
    assert semantic.metrics["lines_qty"].inferred is False


def test_infer_false_keeps_only_the_files_metrics():
    semantic = resolve(SHOP, _metrics("infer: false\nmetrics:\n  n: {count: stores}\n"))
    assert list(semantic.metrics) == ["n"]


def test_no_metrics_file_means_the_inferred_metrics():
    semantic = resolve(SHOP)
    assert list(semantic.metrics) == list(inferred_metrics(SHOP))
    assert semantic.name == "shop"
    nameless = load_model("model2data: 0.4.0\ntables:\n  t:\n    columns:\n      id: int\n")
    assert resolve(nameless, model_name="stem").name == "stem"
    assert resolve(nameless).name == "model"


def test_resolve_refuses_metrics_that_do_not_fit_the_model():
    metrics = load("model2data-metrics: 0.1.0\nmodel: shop\nmetrics:\n  m: {count: nowhere}\n")
    with pytest.raises(MetricsError) as raised:
        resolve(SHOP, metrics)
    assert [i.path for i in raised.value.issues] == ["metrics.m.count"]


# ---------------------------------------------------------------------------
# Resolved metrics
# ---------------------------------------------------------------------------
def test_a_simple_metric_resolves_its_table_aggregation_time_and_joins():
    semantic = resolve(
        SHOP,
        _metrics(
            """
metrics:
  m:
    measure: lines.price
    where:
      customers.vip: true
      any:
        - orders.status: paid
        - lines.qty: {gt: 2}
"""
        ),
    )
    metric = semantic.metrics["m"]
    assert (metric.table, metric.column, metric.column_kind, metric.agg) == (
        "lines",
        "price",
        "decimal",
        "average",
    )
    assert metric.time == "orders.placed_at" and metric.time_kind == "timestamp"
    assert list(metric.joins) == ["orders", "customers"]
    assert len(metric.joins["customers"]) == 2
    assert metric.label == "M"


def test_an_explicit_time_and_no_time():
    semantic = resolve(
        SHOP,
        _metrics(
            "metrics:\n  a: {count: lines, time: customers.joined_on}\n"
            "  b: {measure: logs.level, agg: max}\n"
        ),
    )
    assert semantic.metrics["a"].time == "customers.joined_on"
    assert semantic.metrics["a"].time_kind == "date"
    assert list(semantic.metrics["a"].joins) == ["customers"]
    assert semantic.metrics["b"].time is None and semantic.metrics["b"].joins == {}


def test_ratio_and_derived_metrics_know_their_inputs():
    semantic = resolve(
        SHOP,
        _metrics(
            """
metrics:
  r: {ratio: {numerator: orders_amount, denominator: orders_amount}}
  d: {expression: r * 100 - lines_qty / r}
"""
        ),
    )
    assert semantic.metrics["r"].inputs == ("orders_amount",)
    assert semantic.metrics["d"].inputs == ("r", "lines_qty")
    assert semantic.simple_inputs("d") == ["orders_amount", "lines_qty"]
    assert semantic.simple_inputs("orders_amount") == ["orders_amount"]


# ---------------------------------------------------------------------------
# Entities, relationships, dimensions, measures
# ---------------------------------------------------------------------------
def test_entities_carry_keys_grain_and_dbt_names():
    model = load_model(
        """
model2data: 0.4.0
tables:
  Order Lines:
    role: fact
    grain: [order_id, n]
    description: One per line
    keys:
      - {unique: [order_id, n]}
    columns:
      id: {type: int, pk: true}
      order_id: int
      n: int
      code: {type: text, unique: true}
"""
    )
    entity = resolve(model).entities["Order Lines"]
    assert entity.name == "order_lines"
    assert entity.primary_key == ("id",)
    assert entity.unique_keys == (("code",), ("order_id", "n"))
    assert entity.grain == ("order_id", "n")
    assert (entity.role, entity.description) == ("fact", "One per line")


def test_relationships_run_from_the_many_side():
    model = load_model(
        """
model2data: 0.4.0
tables:
  users:
    columns:
      id: {type: int, pk: true}
  profiles:
    columns:
      user_id: {type: int, references: {to: users.id, one_to_one: true}}
  pairs:
    keys:
      - {pk: [a, b]}
    columns:
      a: int
      b: int
  links:
    foreign_keys:
      - {columns: [x, y], references: pairs, to_columns: [a, b], one_to_one: true}
    columns:
      x: int
      y: int
"""
    )
    relationships = resolve(model).relationships
    assert [(r.name, r.from_table, r.to_table, r.one_to_one) for r in relationships] == [
        ("profiles_user_id_to_users", "profiles", "users", True),
        ("links_x_y_to_pairs", "links", "pairs", True),
    ]
    assert relationships[1].from_columns == ("x", "y")
    assert relationships[1].to_columns == ("a", "b")


def test_default_dimensions_and_overrides():
    semantic = resolve(
        SHOP,
        _metrics(
            """
dimensions:
  customers.vip: false
  customers.country: {label: Country, description: Where they live}
  orders.note: true
  orders.status: {label: Order status}
"""
        ),
    )
    found = {d.path: d for d in semantic.dimensions}
    assert list(found) == [
        "customers.country",
        "customers.tier",
        "customers.joined_on",
        "orders.status",
        "orders.placed_at",
        "orders.note",
        "slots.day",
        "visits.day",
    ]
    assert found["customers.country"].label == "Country"
    assert found["customers.country"].description == "Where they live"
    assert found["customers.country"].kind == "categorical"
    assert found["customers.tier"].members == ("1", "2")
    assert found["orders.status"].label == "Order status"
    assert found["orders.placed_at"].kind == "time"
    assert found["customers.joined_on"].label == "Joined on"


def test_keys_foreign_keys_and_measures_are_not_categorical_dimensions():
    model = load_model(
        """
model2data: 0.4.0
enums:
  e: [a, b]
tables:
  p:
    keys:
      - {pk: [k, k2]}
    columns:
      k: e
      k2: int
      u: {type: e, unique: true}
      r: {type: e, references: q.id}
      m: {type: boolean, measure: count}
      ok: e
  q:
    keys:
      - {pk: [id, id2]}
    columns:
      id: e
      id2: int
  w:
    foreign_keys:
      - {columns: [f, g], references: q, to_columns: [id, id2]}
    columns:
      f: e
      g: int
      flag: bool
"""
    )
    assert [d.path for d in resolve(model).dimensions] == ["p.ok", "w.flag"]


def test_measures_list_every_measure_column():
    assert [(m.table, m.column, m.agg) for m in resolve(SHOP).measures] == [
        ("orders", "amount", "sum"),
        ("lines", "qty", "sum"),
        ("lines", "price", "average"),
    ]
