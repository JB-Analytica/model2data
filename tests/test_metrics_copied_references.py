"""A reference onto a plain column next to one onto the parent's key is a copy, not a join.

`order_items.unit_price` references `products.price` beside
`order_items.product_id` references `products.id`: the price is the line's own
product's, so only `product_id` relates the two tables.
"""

from datetime import date

from model2data.generate.core import generate_data_from_dbml
from model2data.metrics import check_against, known_values, load, resolve, to_lightdash
from model2data.metrics.graph import Graph
from model2data.model import from_dict, to_engine

COFFEE = {
    "model2data": "0.6.0",
    "name": "coffee",
    "enums": {"category": ["beans", "gear", "tea"]},
    "tables": {
        "products": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "price": {"type": "numeric"},
                "category": "category",
            }
        },
        "orders": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "ordered_at": {"type": "date"},
            }
        },
        "order_items": {
            "columns": {
                "id": {"type": "bigint", "pk": True},
                "order_id": {"type": "bigint", "references": "orders.id"},
                "product_id": {"type": "bigint", "references": "products.id"},
                "unit_price": {"type": "numeric", "references": "products.price"},
                "quantity": {"type": "int"},
            }
        },
    },
}

METRICS = """
model2data-metrics: 0.1.0
model: coffee
infer: false
metrics:
  beans_quantity:
    measure: order_items.quantity
    agg: sum
    time: orders.ordered_at
    where:
      products.category: [beans, tea]
"""


def _model(order_items: dict | None = None):
    document = {**COFFEE, "tables": {**COFFEE["tables"]}}
    if order_items is not None:
        document["tables"]["order_items"] = {"columns": order_items}
    return from_dict(document)


def test_copies_is_true_for_a_plain_column_beside_a_key_reference():
    model = _model()
    assert model.copies("order_items", "unit_price")
    assert not model.copies("order_items", "product_id")
    assert not model.copies("order_items", "order_id")
    assert not model.copies("order_items", "quantity")


def test_copies_sees_a_foreign_key_beside_it():
    document = {**COFFEE, "tables": {**COFFEE["tables"]}}
    document["tables"]["products"] = {
        "columns": {**COFFEE["tables"]["products"]["columns"], "variant": {"type": "bigint"}}
    }
    document["tables"]["order_items"] = {
        "columns": {
            "id": {"type": "bigint", "pk": True},
            "product_id": {"type": "bigint"},
            "variant": {"type": "bigint"},
            "unit_price": {"type": "numeric", "references": "products.price"},
        },
        "foreign_keys": [
            {
                "columns": ["product_id", "variant"],
                "references": "products",
                "to_columns": ["id", "variant"],
            }
        ],
    }
    assert from_dict(document).copies("order_items", "unit_price")


def test_a_plain_column_reference_without_a_key_reference_is_still_a_join():
    model = _model(
        {
            "id": {"type": "bigint", "pk": True},
            "order_id": {"type": "bigint", "references": "orders.id"},
            "unit_price": {"type": "numeric", "references": "products.price"},
            "quantity": {"type": "int"},
        }
    )
    assert not model.copies("order_items", "unit_price")
    steps = Graph(model).steps["order_items"]
    assert [str(step) for step in steps] == [
        "order_items.order_id -> orders.id",
        "order_items.unit_price -> products.price",
    ]


def test_the_graph_reaches_products_one_way():
    steps = Graph(_model()).steps["order_items"]
    assert [str(step) for step in steps] == [
        "order_items.order_id -> orders.id",
        "order_items.product_id -> products.id",
    ]


def test_a_metric_filtered_on_the_copied_references_table_is_not_ambiguous():
    model = _model()
    metrics = load(METRICS, model)
    assert not [i for i in check_against(metrics, model) if i.is_error]


def test_metric_values_follow_the_key_reference():
    model = _model()
    inputs = to_engine(model)
    frames = generate_data_from_dbml(
        tables=inputs.tables, refs=inputs.refs, base_rows=120, seed=5, as_of=date(2026, 3, 15)
    )
    semantic = resolve(model, load(METRICS, model))
    values = known_values(semantic, frames, seed=5, as_of="2026-03-15").values
    items = frames["order_items"].merge(
        frames["products"][["id", "category"]], left_on="product_id", right_on="id"
    )
    kept = items[items["category"].isin(["beans", "tea"])]
    assert len(kept) < len(items)
    assert values["beans_quantity"].value == int(kept["quantity"].sum())
    # the copy is the line's own product's price
    prices = frames["order_items"].merge(
        frames["products"][["id", "price"]], left_on="product_id", right_on="id"
    )
    same = (prices["unit_price"] == prices["price"]) | prices["price"].isna()
    assert same.all()


def test_the_semantic_model_has_one_relationship_from_order_items_to_products():
    semantic = resolve(_model(), load(METRICS, _model()))
    found = [
        r
        for r in semantic.relationships
        if r.from_table == "order_items" and r.to_table == "products"
    ]
    assert len(found) == 1
    assert found[0].from_columns == ("product_id",)


def test_the_lightdash_export_joins_products_without_a_warning():
    model = _model()
    export = to_lightdash(resolve(model, load(METRICS, model)))
    assert not [loss for loss in export.lossiness if "more than one path" in loss.reason]
    items = next(m for m in export.models if m["name"] == "stg_order_items")
    joined = [join["join"] for join in items["config"]["meta"]["joins"]]
    assert "stg_products" in joined
