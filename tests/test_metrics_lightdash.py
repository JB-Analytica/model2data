"""The Lightdash export: the mapping, the filters, the lossiness report, and the vendored schema.

`fixtures/lightdash/lightdash-dbt-2.0.json` is Lightdash's own JSON Schema for
the meta of dbt YAML (see `fixtures/lightdash/VENDORED.md`); every document
exported here validates against it.
"""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from model2data.metrics import LightdashExport, load, resolve, to_lightdash, write_lightdash
from model2data.model import load as load_model

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SPEC = ROOT / "model2data" / "spec" / "examples"
SHOP = load_model(FIXTURES / "metrics" / "shop.model2data.yml")
SCHEMA = json.loads((FIXTURES / "lightdash" / "lightdash-dbt-2.0.json").read_text(encoding="utf-8"))


def _valid(document: dict) -> None:
    jsonschema.Draft7Validator(SCHEMA).validate(document)


def _coffee() -> LightdashExport:
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    return to_lightdash(resolve(model, load(SPEC / "coffee_webshop.metrics.yml", model)))


def _shop(text: str) -> LightdashExport:
    header = "model2data-metrics: 0.1.0\nmodel: shop\ninfer: false\nmetrics:\n"
    return to_lightdash(resolve(SHOP, load(header + text, SHOP)))


def _models(export: LightdashExport) -> dict[str, dict]:
    return {model["name"]: model for model in export.models}


def _meta(export: LightdashExport, model: str) -> dict:
    return _models(export)[model]["config"]["meta"]


def _columns(export: LightdashExport, model: str) -> dict[str, dict]:
    return {c["name"]: c["config"]["meta"] for c in _models(export)[model]["columns"]}


def _metric(export: LightdashExport, name: str) -> dict:
    """A metric wherever it is: on a column or on its model."""
    for model in export.models:
        if name in model["config"]["meta"].get("metrics", {}):
            return model["config"]["meta"]["metrics"][name]
        for column in model["columns"]:
            if name in column["config"]["meta"].get("metrics", {}):
                return column["config"]["meta"]["metrics"][name]
    raise KeyError(name)


def _losses(export: LightdashExport) -> dict[tuple[str, str], str]:
    return {(loss.subject, loss.item): loss.reason for loss in export.lossiness}


# ---------------------------------------------------------------------------
# The schema
# ---------------------------------------------------------------------------
def test_the_vendored_schema_is_lightdashs_dbt_schema():
    jsonschema.Draft7Validator.check_schema(SCHEMA)
    assert "modelMeta" in SCHEMA["$defs"] and "columnMeta" in SCHEMA["$defs"]


@pytest.mark.parametrize(
    "model_path, metrics_path",
    [(path, None) for path in sorted((ROOT / "examples").glob("*.model2data.yml"))]
    + [
        (
            ROOT / "examples" / "ecommerce.model2data.yml",
            ROOT / "examples" / "ecommerce.metrics.yml",
        ),
        (SPEC / "coffee_webshop.model2data.yml", SPEC / "coffee_webshop.metrics.yml"),
        (FIXTURES / "metrics" / "shop.model2data.yml", FIXTURES / "metrics" / "shop.metrics.yml"),
        (
            ROOT / "examples" / "ecommerce_training.model2data.yml",
            FIXTURES / "metrics" / "ecommerce_training.metrics.yml",
        ),
    ],
    ids=lambda path: path.name if path else "inferred",
)
def test_every_example_exports_a_valid_document(model_path, metrics_path):
    model = load_model(model_path)
    metrics = load(metrics_path, model) if metrics_path else None
    export = to_lightdash(resolve(model, metrics))
    _valid(export.document)
    text = export.to_yaml()
    assert yaml.safe_load(text) == export.document
    assert text.startswith("# Lightdash metrics and dimensions (dbt config.meta)")


# ---------------------------------------------------------------------------
# Tables, joins, dimensions
# ---------------------------------------------------------------------------
def test_the_models_their_joins_and_dimensions():
    export = _coffee()
    assert [m["name"] for m in export.models] == [
        "stg_customers",
        "stg_products",
        "stg_orders",
        "stg_order_items",
        "stg_product_reviews",
    ]
    orders = _models(export)["stg_orders"]
    assert orders["description"].startswith("One row per order")
    assert orders["config"]["meta"]["label"] == "Orders"
    assert orders["config"]["meta"]["primary_key"] == "id"
    # Every table reached along one path, nearest first, a step at a time.
    assert _meta(export, "stg_order_items")["joins"] == [
        {
            "join": "stg_products",
            "sql_on": "${stg_order_items.product_id} = ${stg_products.id}",
            "relationship": "many-to-one",
        },
        {
            "join": "stg_orders",
            "sql_on": "${stg_order_items.order_id} = ${stg_orders.id}",
            "relationship": "many-to-one",
        },
        {
            "join": "stg_customers",
            "sql_on": "${stg_orders.customer_id} = ${stg_customers.id}",
            "relationship": "many-to-one",
        },
    ]
    assert "joins" not in _meta(export, "stg_customers")
    products = _columns(export, "stg_products")
    assert products["name"]["dimension"] == {"label": "Product"}  # the metrics file's label
    assert products["category"]["dimension"] == {"label": "Category"}
    assert products["id"]["dimension"] == {"hidden": True}  # a key: not a dimension
    assert products["price"]["dimension"] == {"hidden": True}
    # `customers.email: false` in the metrics file: never offered.
    assert _columns(export, "stg_customers")["email"]["dimension"] == {"hidden": True}
    name = next(c for c in _models(export)["stg_products"]["columns"] if c["name"] == "name")
    assert name["description"] == "The blend or single-origin name printed on the bag"


def test_composite_keys_one_to_one_and_a_dimensions_own_description():
    model = load_model(
        """
model2data: 0.4.0
name: keys
tables:
  slots:
    keys:
      - {pk: [store_id, day]}
    columns:
      store_id: int
      day: date
  visits:
    foreign_keys:
      - {columns: [store_id, day], references: slots, to_columns: [store_id, day]}
    columns:
      id: {type: int, pk: true}
      store_id: int
      day: date
  badges:
    columns:
      visit_id: {type: int, references: {to: visits.id, one_to_one: true}}
      shiny: {type: boolean, description: Whether it shines}
"""
    )
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: keys\ndimensions:\n"
        "  badges.shiny: {label: Shiny, description: Polished to a shine}\n",
        model,
    )
    export = to_lightdash(resolve(model, metrics))
    _valid(export.document)
    assert _meta(export, "stg_slots")["primary_key"] == ["store_id", "day"]
    assert "primary_key" not in _meta(export, "stg_badges")
    assert _meta(export, "stg_visits")["joins"] == [
        {
            "join": "stg_slots",
            "sql_on": "${stg_visits.store_id} = ${stg_slots.store_id} AND "
            "${stg_visits.day} = ${stg_slots.day}",
            "relationship": "many-to-one",
        }
    ]
    assert _meta(export, "stg_badges")["joins"][0]["relationship"] == "one-to-one"
    assert _columns(export, "stg_badges")["shiny"]["dimension"] == {
        "label": "Shiny",
        "description": "Polished to a shine",
    }


def test_a_custom_model_name():
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    export = to_lightdash(resolve(model), source=lambda entity: f"dim_{entity.name}")
    assert _models(export)["dim_orders"]["config"]["meta"]["joins"][0] == {
        "join": "dim_customers",
        "sql_on": "${dim_orders.customer_id} = ${dim_customers.id}",
        "relationship": "many-to-one",
    }


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def test_simple_metrics_row_counts_ratios_and_derived_metrics():
    export = _coffee()
    revenue = _columns(export, "stg_orders")["total_amount"]["metrics"]["revenue"]
    assert revenue == {
        "type": "sum",
        "label": "Revenue",
        "description": "Order value of the orders that were not cancelled or left unpaid.",
        "ai_hint": "What customers paid for. Use it for sales and turnover questions; it leaves "
        "out pending and cancelled orders.",
        "format": "#,##0.00",
        "filters": [{"status": ["paid", "shipped", "delivered"]}],
        "default_time_dimension": {"field": "order_date", "interval": "MONTH"},
    }
    assert _columns(export, "stg_orders")["id"]["metrics"]["orders"]["type"] == "count_distinct"
    # A row count counts the one-column key distinctly: the same number, and safe when joined.
    assert _columns(export, "stg_order_items")["id"]["metrics"]["order_lines"] == {
        "type": "count_distinct",
        "label": "Order lines",
    }
    assert _metric(export, "web_revenue")["filters"] == [
        {"status": ["paid", "shipped", "delivered"]},
        {"channel": ["web"]},
    ]
    assert _meta(export, "stg_orders")["metrics"] == {
        "average_order_value": {
            "type": "number",
            "sql": "${revenue} / NULLIF(${orders}, 0)",
            "label": "Average order value",
            "format": "#,##0.00",
        },
        "web_share": {
            "type": "number",
            "sql": "(${web_revenue} / NULLIF(${revenue}, 0))",
            "label": "Web share of revenue",
            "format": "0.00%",
        },
    }
    assert export.metrics == [
        "revenue",
        "orders",
        "order_lines",
        "average_order_value",
        "web_revenue",
        "web_share",
        "coffee_units_sold",
        "orders_total_amount",
        "orders_count",
        "order_items_quantity",
        "order_items_count",
    ]


def test_every_aggregation_and_a_ratio_inside_an_expression():
    export = _shop(
        """
  a: {measure: lines.qty, agg: sum}
  b: {measure: lines.price, agg: average}
  c: {measure: lines.price, agg: min}
  d: {measure: lines.price, agg: max}
  e: {measure: lines.price, agg: median}
  f: {measure: orders.note, agg: count}
  g: {measure: orders.note, agg: count_distinct}
  r: {ratio: {numerator: d, denominator: c}}
  x: {expression: "r * 2 - a", format: number}
"""
    )
    _valid(export.document)
    types = {name: _metric(export, name)["type"] for name in "abcdefg"}
    assert types == {
        "a": "sum",
        "b": "average",
        "c": "min",
        "d": "max",
        "e": "median",
        "f": "count",
        "g": "count_distinct",
    }
    assert _metric(export, "x") == {
        "type": "number",
        "sql": "(((${d} / NULLIF(${c}, 0)) * 2) - ${a})",
        "label": "X",
    }


def test_a_row_count_without_a_one_column_key_is_a_model_metric():
    model = load_model(
        "model2data: 0.4.0\nname: logs\ntables:\n  logs:\n    role: fact\n    columns:\n"
        "      level: int\n      at: timestamp\n"
    )
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: logs\nmetrics:\n"
        "  warnings: {count: logs, where: {logs.at: {gte: '2026-01-01 00:00:00'}}}\n",
        model,
    )
    export = to_lightdash(resolve(model, metrics))
    _valid(export.document)
    assert _meta(export, "stg_logs")["metrics"] == {
        "warnings": {
            "type": "count",
            "sql": "CASE WHEN ${at} >= timestamp '2026-01-01 00:00:00' THEN 1 END",
            "label": "Warnings",
            "default_time_dimension": {"field": "at", "interval": "MONTH"},
        },
        "logs_count": {
            "type": "count",
            "sql": "1",
            "label": "Logs count",
            "description": "Rows of logs.",
            "default_time_dimension": {"field": "at", "interval": "MONTH"},
        },
    }


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
def test_filters_lightdash_can_say():
    export = _shop(
        """
  m:
    measure: lines.price
    agg: sum
    where:
      lines.qty: {gt: 1, lte: 9}
      lines.price: {gte: 2.5, lt: 100, between: [0, 1000.25]}
      orders.status: {ne: cancelled}
      orders.note: {not_in: [a, b], is_null: false}
      customers.vip: true
      customers.tier: [1, 2]
      customers.country: {is_null: true}
"""
    )
    _valid(export.document)
    assert _metric(export, "m")["filters"] == [
        {"qty": "> 1"},
        {"qty": "<= 9"},
        {"price": ">= 2.5"},
        {"price": "< 100"},
        {"price": "between 0 and 1000.25"},
        {"stg_orders.status": "!cancelled"},
        {"stg_orders.status": "!null"},  # Lightdash's not-equal lets nulls through
        {"stg_orders.note": "!a"},
        {"stg_orders.note": "!b"},
        {"stg_orders.note": "!null"},
        {"stg_customers.vip": ["true"]},
        {"stg_customers.tier": ["1", "2"]},
        {"stg_customers.country": "null"},
    ]
    assert "sql" not in _metric(export, "m")


def test_filters_lightdash_cannot_say_become_sql():
    export = _shop(
        """
  m:
    count: lines
    where:
      orders.placed_at: {lt: "2100-01-01 00:00:00"}
      customers.vip: {in: [true, false]}
      orders.note: {ne: "50%, or so"}
      customers.country: {not_in: [nullland]}
      orders.status: paid
      any:
        - lines.qty: {gte: 3}
        - customers.joined_on: "2020-01-01"
"""
    )
    _valid(export.document)
    metric = _metric(export, "m")
    assert metric["filters"] == [{"stg_orders.status": ["paid"]}]
    assert metric["sql"] == (
        "CASE WHEN (${stg_orders.placed_at} < timestamp '2100-01-01 00:00:00' and "
        "${stg_customers.vip} in (true, false) and ${stg_orders.note} <> '50%, or so' and "
        "${stg_customers.country} not in ('nullland') and "
        "(${qty} >= 3 or ${stg_customers.joined_on} = date '2020-01-01')) THEN ${id} END"
    )


@pytest.mark.parametrize(
    "value, filtered",
    [
        ("plain words-and_more", True),
        (" padded", False),
        ("a,b", False),
        ("100%", False),
        ("x^y", False),
        ('say "hi"', False),
        ("back\\slash", False),
        ("emptyish", False),
        ("NULL island", False),
    ],
)
def test_a_negated_value_is_a_filter_only_when_lightdash_reads_it_back(value, filtered):
    text = json.dumps(value)
    export = _shop(f"  m: {{count: orders, where: {{orders.note: {{ne: {text}}}}}}}\n")
    metric = _metric(export, "m")
    if filtered:
        assert metric["filters"] == [{"note": f"!{value}"}, {"note": "!null"}]
        assert "sql" not in metric
    else:
        assert "filters" not in metric
        assert metric["sql"].startswith("CASE WHEN ${note} <> ")


# ---------------------------------------------------------------------------
# Lossiness
# ---------------------------------------------------------------------------
def test_the_lossiness_of_the_coffee_example():
    losses = _losses(_coffee())
    assert set(losses) == {
        ("metrics order_lines, coffee_units_sold, order_items_quantity, order_items_count", "time"),
        (
            "dimensions stg_products.category, stg_products.origin, stg_products.roast, "
            "stg_orders.channel, stg_orders.status",
            "values",
        ),
        ("metrics revenue, average_order_value", "currency"),
    }
    assert losses[
        ("metrics order_lines, coffee_units_sold, order_items_quantity, order_items_count", "time")
    ] == (
        "dated by orders.order_date, a column of stg_orders; a Lightdash metric's default time "
        "dimension is a field of its own table"
    )


def test_singular_losses_roles_grain_and_awkward_names():
    model = load_model(
        """
model2data: 0.4.0
name: odd
enums:
  kind: [a, b]
tables:
  user accounts:
    role: dimension
    columns:
      id: {type: int, pk: true}
      Display Name: text
  events:
    role: fact
    grain: [user_id, at]
    columns:
      user_id: {type: int, references: user accounts.id}
      at: timestamp
      kind: kind
      amount: {type: numeric, measure: true}
"""
    )
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: odd\nmetrics:\n"
        "  spend: {measure: events.amount, format: currency}\n",
        model,
    )
    export = to_lightdash(resolve(model, metrics))
    _valid(export.document)
    losses = _losses(export)
    assert losses[("table stg_user_accounts", "role")] == "Lightdash tables have no role"
    assert losses[("table stg_events", "role and grain")] == (
        "Lightdash tables have no role or grain"
    )
    assert ("dimension stg_events.kind", "values") in losses
    assert ("metric spend", "currency") in losses
    assert ("column stg_user_accounts.Display Name", "name") in losses
    columns = [c["name"] for c in _models(export)["stg_user_accounts"]["columns"]]
    assert columns == ["id", '"Display Name"']


def test_plural_awkward_names():
    model = load_model(
        "model2data: 0.4.0\nname: odd\ntables:\n  t:\n    columns:\n"
        "      Big: int\n      also odd: int\n"
    )
    losses = _losses(to_lightdash(resolve(model)))
    assert ("columns stg_t.Big, stg_t.also odd", "name") in losses


def test_a_ratio_across_tables_is_not_exported():
    export = _shop(
        """
  qty: {measure: lines.qty, agg: sum}
  amount: {measure: orders.amount}
  per_euro: {ratio: {numerator: qty, denominator: amount}}
  per_euro_twice: {expression: per_euro * 2}
"""
    )
    _valid(export.document)
    assert export.metrics == ["qty", "amount"]
    losses = _losses(export)
    assert losses[("metric per_euro", "inputs")].startswith(
        "its inputs are on stg_lines, stg_orders; a Lightdash metric belongs to one table"
    )
    assert ("metric per_euro_twice", "inputs") in losses
    with pytest.raises(KeyError):
        _metric(export, "per_euro")


def test_tables_reached_along_two_paths_are_not_joined():
    export = to_lightdash(resolve(SHOP))
    losses = _losses(export)
    assert losses[("table stg_transfers", "joins")] == (
        "stg_customers is reached along more than one path; a Lightdash explore joins a table "
        "once, so its explore does not join it"
    )
    assert "joins" not in _meta(export, "stg_transfers")


AMBIGUOUS = """
model2data: 0.4.0
name: amb
enums:
  colour: [red, blue]
tables:
  c:
    columns:
      id: {type: bigint, pk: true}
      x: colour
  d:
    columns:
      id: {type: bigint, pk: true}
      y: colour
  b:
    columns:
      id: {type: bigint, pk: true}
      c_id: {type: bigint, references: c.id}
      d_id: {type: bigint, references: d.id}
      v: {type: int, measure: sum}
  a:
    columns:
      id: {type: bigint, pk: true}
      b_id: {type: bigint, references: b.id}
      c_id: {type: bigint, references: c.id}
      d_id: {type: bigint, references: d.id}
"""


def test_a_joined_tables_metrics_that_filter_on_an_unjoined_table_are_left_out_of_the_join():
    model = load_model(AMBIGUOUS)
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: amb\nmetrics:\n"
        "  red_v: {measure: b.v, where: {c.x: red}}\n"
        "  red_share: {ratio: {numerator: red_v, denominator: b_v}}\n",
        model,
    )
    export = to_lightdash(resolve(model, metrics))
    _valid(export.document)
    losses = _losses(export)
    assert losses[("table stg_a", "joins")] == (
        "stg_c, stg_d are reached along more than one path; a Lightdash explore joins a table "
        "once, so its explore does not join them"
    )
    assert losses[("table stg_a", "joined metrics")] == (
        "its explore joins stg_b without red_v, red_share: they filter on a table the explore "
        "does not join"
    )
    (join,) = _meta(export, "stg_a")["joins"]
    assert join["join"] == "stg_b"
    assert join["fields"] == ["id", "c_id", "d_id", "v", "b_v", "b_count"]


def test_a_single_metric_left_out_of_a_join():
    model = load_model(AMBIGUOUS)
    metrics = load(
        "model2data-metrics: 0.1.0\nmodel: amb\ninfer: false\nmetrics:\n"
        "  red_v: {measure: b.v, where: {c.x: red}}\n",
        model,
    )
    losses = _losses(to_lightdash(resolve(model, metrics)))
    assert losses[("table stg_a", "joined metrics")] == (
        "its explore joins stg_b without red_v: it filters on a table the explore does not join"
    )


def test_no_losses_and_no_metrics():
    model = load_model("model2data: 0.4.0\nname: one\ntables:\n  t:\n    columns:\n      id: int\n")
    export = to_lightdash(resolve(model))
    assert export.lossiness == [] and export.metrics == []
    assert "Not expressible" not in export.to_yaml()
    assert export.document == {
        "version": 2,
        "models": [
            {
                "name": "stg_t",
                "config": {"meta": {"label": "T"}},
                "columns": [{"name": "id", "config": {"meta": {"dimension": {"hidden": True}}}}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Into a generated project
# ---------------------------------------------------------------------------
def test_write_lightdash_merges_into_the_staging_yaml(tmp_path):
    staging = tmp_path / "models" / "staging"
    staging.mkdir(parents=True)
    model = load_model(
        "model2data: 0.4.0\nname: odd\ntables:\n  t:\n    description: A table\n    columns:\n"
        "      id: {type: int, pk: true}\n      Big Name: boolean\n      gone: int\n"
    )
    (staging / "stg_t.yml").write_text(
        yaml.safe_dump(
            {
                "version": 2,
                "models": [
                    {"name": "other"},
                    {
                        "name": "stg_t",
                        "config": {"tags": ["x"]},
                        "columns": [
                            {"name": "id", "tests": ["unique"]},
                            {"name": '"Big Name"', "config": {"tags": ["y"]}},
                            {"name": "extra"},
                        ],
                    },
                ],
            },
            sort_keys=False,
        )
    )
    export = to_lightdash(resolve(model))
    written = write_lightdash(tmp_path, export)
    assert written == [staging / "stg_t.yml"]
    document = yaml.safe_load(written[0].read_text())
    other, entry = document["models"]
    assert other == {"name": "other"}
    assert list(entry) == ["name", "description", "config", "columns"]
    assert entry["description"] == "A table"
    assert entry["config"] == {"tags": ["x"], "meta": {"label": "T", "primary_key": "id"}}
    columns = entry["columns"]
    assert columns[0] == {
        "name": "id",
        "config": {"meta": {"dimension": {"hidden": True}}},
        "tests": ["unique"],
    }
    assert columns[1]["config"] == {"tags": ["y"], "meta": {"dimension": {"label": "Big Name"}}}
    assert columns[2] == {"name": "extra"}
