"""The Ossie 0.1.1 export: the mapping, the lossiness report, and the vendored schema.

`fixtures/ossie/osi-schema.json` is Apache Ossie's own JSON Schema for 0.1.1
(see `fixtures/ossie/VENDORED.md`); every document exported here validates
against it.
"""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

from model2data.metrics import OSSIE_VERSION, load, resolve, to_ossie
from model2data.model import load as load_model

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SPEC = ROOT / "model2data" / "spec" / "examples"
SHOP = load_model(FIXTURES / "metrics" / "shop.model2data.yml")
OSSIE_SCHEMA = json.loads((FIXTURES / "ossie" / "osi-schema.json").read_text(encoding="utf-8"))


def _valid(document: dict) -> None:
    jsonschema.Draft202012Validator(OSSIE_SCHEMA).validate(document)


def _coffee():
    model = load_model(SPEC / "coffee_webshop.model2data.yml")
    return resolve(model, load(SPEC / "coffee_webshop.metrics.yml", model))


def test_the_vendored_schema_is_ossie_0_1_1():
    assert OSSIE_SCHEMA["properties"]["version"]["const"] == OSSIE_VERSION == "0.1.1"
    jsonschema.Draft202012Validator.check_schema(OSSIE_SCHEMA)


@pytest.mark.parametrize(
    "model_path",
    sorted((ROOT / "examples").glob("*.model2data.yml")) + [SPEC / "coffee_webshop.model2data.yml"],
    ids=lambda path: path.name,
)
def test_every_example_model_exports_a_valid_document(model_path):
    export = to_ossie(resolve(load_model(model_path)))
    _valid(export.document)
    # And the YAML reads back as the same document.
    assert yaml.safe_load(export.to_yaml()) == json.loads(json.dumps(export.document))


def test_the_coffee_example_exports_a_valid_document():
    export = to_ossie(_coffee())
    _valid(export.document)
    _valid(yaml.safe_load(export.to_yaml()))


def test_the_semantic_model_and_its_datasets():
    document = to_ossie(_coffee()).document
    assert document["version"] == "0.1.1"
    (model,) = document["semantic_model"]
    assert model["name"] == "coffee_webshop"
    assert model["description"].startswith("A Belgian specialty-coffee roaster")
    products = next(d for d in model["datasets"] if d["name"] == "products")
    assert products["source"] == "staging.stg_products"
    assert products["primary_key"] == ["id"]
    assert products["unique_keys"] == [["sku"]]
    assert products["description"] == "The catalogue: one row per sellable product"
    fields = {f["name"]: f for f in products["fields"]}
    assert list(fields)[:3] == ["id", "name", "sku"]
    assert fields["id"] == {
        "name": "id",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "id"}]},
    }
    assert fields["name"]["dimension"] == {"is_time": False}
    assert fields["name"]["label"] == "Product"  # from the metrics file's dimensions
    assert fields["created_at"]["dimension"] == {"is_time": True}
    assert json.loads(fields["roast"]["custom_extensions"][0]["data"]) == {
        "model2data": {"values": ["light", "medium", "dark"]}
    }
    customers = next(d for d in model["datasets"] if d["name"] == "customers")
    assert "dimension" not in {f["name"]: f for f in customers["fields"]}["email"]


def test_relationships_run_from_the_many_side():
    model = to_ossie(_coffee()).document["semantic_model"][0]
    assert model["relationships"][0] == {
        "name": "orders_customer_id_to_customers",
        "from": "orders",
        "to": "customers",
        "from_columns": ["customer_id"],
        "to_columns": ["id"],
    }


def test_metric_expressions():
    metrics = {m["name"]: m for m in to_ossie(_coffee()).document["semantic_model"][0]["metrics"]}

    def sql(name: str) -> str:
        (dialect,) = metrics[name]["expression"]["dialects"]
        assert dialect["dialect"] == "ANSI_SQL"
        return dialect["expression"]

    revenue = "SUM(CASE WHEN orders.status IN ('paid', 'shipped', 'delivered') THEN orders.total_amount END)"
    assert sql("revenue") == revenue
    assert sql("orders") == "COUNT(DISTINCT orders.id)"
    assert sql("order_lines") == "COUNT(order_items.id)"
    assert sql("average_order_value") == f"({revenue}) / NULLIF(COUNT(DISTINCT orders.id), 0)"
    assert sql("web_share").startswith("(SUM(CASE WHEN (orders.status IN")
    assert "/ NULLIF(SUM(" in sql("web_share") and "100" not in sql("web_share")
    assert sql("coffee_units_sold") == (
        "SUM(CASE WHEN (orders.status <> 'cancelled' AND products.category IN ('beans', 'ground', "
        "'capsules') AND customers.phone IS NOT NULL AND (orders.channel = 'web' OR "
        "order_items.quantity >= 3)) THEN order_items.quantity END)"
    )
    assert metrics["revenue"]["description"].startswith("Order value")
    assert metrics["revenue"]["ai_context"].startswith("What customers paid for.")
    assert json.loads(metrics["revenue"]["custom_extensions"][0]["data"]) == {
        "model2data": {"label": "Revenue", "format": "currency", "time": "orders.order_date"}
    }
    assert metrics["revenue"]["custom_extensions"][0]["vendor_name"] == "COMMON"


def test_the_lossiness_report():
    export = to_ossie(_coffee())
    report = {(loss.subject, loss.item) for loss in export.lossiness}
    assert ("metric coffee_units_sold", "join path") in report
    assert any(
        subject.startswith("metrics revenue, orders") and item == "label"
        for subject, item in report
    )
    assert ("metrics revenue, average_order_value, web_share", "format") in report
    web_share = next(
        m for m in export.document["semantic_model"][0]["metrics"] if m["name"] == "web_share"
    )
    # percent is passed on as the fraction's format, the value never multiplied
    assert (
        json.loads(web_share["custom_extensions"][0]["data"])["model2data"]["format"] == "percent"
    )
    assert any(item == "time" for _, item in report)
    assert any(item == "values" and "products.category" in subject for subject, item in report)
    text = export.to_yaml()
    assert text.startswith("# Apache Ossie (Open Semantic Interchange) 0.1.1")
    assert "#   - metric coffee_units_sold: join path: reaches customers through orders" in text
    assert 'version: "0.1.1"' in text


def test_roles_grain_one_to_one_and_awkward_names():
    model = load_model(
        """
model2data: 0.4.0
name: odd
tables:
  user accounts:
    role: dimension
    columns:
      id: {type: int, pk: true}
      Display Name: text
  profiles:
    columns:
      user_id: {type: int, references: {to: user accounts.id, one_to_one: true}}
      is_public: boolean
  events:
    role: fact
    grain: [user_id, at]
    columns:
      user_id: {type: int, references: user accounts.id}
      at: timestamp
      amount: {type: numeric, measure: true}
"""
    )
    export = to_ossie(resolve(model), source=lambda entity: f"db.analytics.{entity.name}")
    _valid(export.document)
    datasets = {d["name"]: d for d in export.document["semantic_model"][0]["datasets"]}
    assert datasets["user_accounts"]["source"] == "db.analytics.user_accounts"
    assert datasets["user_accounts"]["fields"][1]["expression"]["dialects"][0]["expression"] == (
        '"Display Name"'
    )
    assert json.loads(datasets["events"]["custom_extensions"][0]["data"]) == {
        "model2data": {"role": "fact", "grain": ["user_id", "at"]}
    }
    relationship = export.document["semantic_model"][0]["relationships"][0]
    assert json.loads(relationship["custom_extensions"][0]["data"]) == {
        "model2data": {"one_to_one": True}
    }
    subjects = [(loss.subject, loss.item) for loss in export.lossiness]
    assert ("dataset user_accounts", "role") in subjects
    assert ("dataset events", "role and grain") in subjects
    assert ("relationship profiles_user_id_to_user_accounts", "one_to_one") in subjects
    # events has no primary key: its row count cannot name whose rows it counts
    metrics_out = {m["name"]: m for m in export.document["semantic_model"][0]["metrics"]}
    assert metrics_out["events_count"]["expression"]["dialects"][0]["expression"] == "COUNT(*)"
    assert ("metric events_count", "row count") in subjects
    assert metrics_out["events_amount"]["expression"]["dialects"][0]["expression"] == (
        "SUM(events.amount)"
    )


def test_a_filtered_row_count_and_literals_of_every_kind():
    metrics = load(
        """
model2data-metrics: 0.1.0
model: shop
infer: false
metrics:
  m:
    count: lines
    where:
      customers.vip: true
      customers.joined_on: {between: ["2025-01-01", "2025-12-31"]}
      orders.placed_at: {lt: "2026-01-01 00:00:00"}
      orders.note: {in: ["it's"]}
      lines.price: {gte: 1.5, lte: 2}
      orders.status: {not_in: [cancelled], is_null: false}
""",
        SHOP,
    )
    export = to_ossie(resolve(SHOP, metrics))
    _valid(export.document)
    (metric,) = export.document["semantic_model"][0]["metrics"]
    assert metric["expression"]["dialects"][0]["expression"] == (
        "COUNT(CASE WHEN (customers.vip = TRUE AND customers.joined_on BETWEEN DATE '2025-01-01' "
        "AND DATE '2025-12-31' AND orders.placed_at < TIMESTAMP '2026-01-01 00:00:00' AND "
        "orders.note IN ('it''s') AND (lines.price >= 1.5 AND lines.price <= 2) AND "
        "(orders.status NOT IN ('cancelled') AND orders.status IS NOT NULL)) THEN lines.id END)"
    )


def test_a_model_without_relationships_or_metrics():
    model = load_model("model2data: 0.4.0\nname: one\ntables:\n  t:\n    columns:\n      id: int\n")
    document = to_ossie(resolve(model)).document
    _valid(document)
    assert "relationships" not in document["semantic_model"][0]
    assert "metrics" not in document["semantic_model"][0]
    assert to_ossie(resolve(model)).lossiness == []
    assert "Not expressible" not in to_ossie(resolve(model)).to_yaml()
