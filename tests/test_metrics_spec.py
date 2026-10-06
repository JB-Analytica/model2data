"""The metrics spec, its schema and the code that reads it say the same thing.

`model2data/spec/metrics/metrics.schema.json` and `README.md` are normative;
the reader in `model2data.metrics` implements them. Every list the schema
enumerates (aggregations, formats, operators, kinds) is also a constant in the
code, so this module checks the two against each other rather than trusting
both to be edited together.
"""

import dataclasses
import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from model2data.metrics import (
    AGGREGATIONS,
    FORMATS,
    OPERATORS,
    SCHEMA_URL,
    SPEC_VERSION,
    Metric,
    load,
)
from model2data.metrics.check import schema
from model2data.metrics.infer import metric_name
from model2data.metrics.types import KIND_KEYS, ORDER_OPERATORS
from model2data.model import load as load_model

SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec"
SCHEMA = json.loads((SPEC / "metrics" / "metrics.schema.json").read_text(encoding="utf-8"))
README = (SPEC / "metrics" / "README.md").read_text(encoding="utf-8")


def test_the_schema_is_a_valid_draft_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert schema() == SCHEMA


def test_the_schema_id_is_the_url_documents_point_at():
    assert SCHEMA["$id"] == SCHEMA_URL
    assert (
        SCHEMA_URL
        == f"https://www.jbanalytica.com/model2data/spec/metrics/{SPEC_VERSION}/metrics.schema.json"
    )
    example = (SPEC / "examples" / "coffee_webshop.metrics.yml").read_text(encoding="utf-8")
    assert example.splitlines()[0] == f"# yaml-language-server: $schema={SCHEMA_URL}"
    assert f"model2data-metrics: {SPEC_VERSION}" in example
    assert README.startswith(f"# model2data metrics — spec {SPEC_VERSION}")


def test_the_example_validates_against_the_schema():
    document = yaml.safe_load((SPEC / "examples" / "coffee_webshop.metrics.yml").read_text())
    jsonschema.Draft202012Validator(SCHEMA).validate(document)


def test_the_readme_example_conforms_against_the_example_model():
    block = re.search(r"```yaml\n(.*?)```", README, re.S)
    assert block is not None
    jsonschema.Draft202012Validator(SCHEMA).validate(yaml.safe_load(block.group(1)))
    model = load_model(SPEC / "examples" / "coffee_webshop.model2data.yml")
    assert load(block.group(1), model).warnings == []


@pytest.mark.parametrize(
    "broken",
    [
        {"model": "m"},
        {"model2data-metrics": "0.1", "model": "m"},
        {"model2data-metrics": "0.1.0", "model": "m", "metrics": {"Bad": {"count": "t"}}},
        {"model2data-metrics": "0.1.0", "model": "m", "metrics": {"m": {}}},
        {
            "model2data-metrics": "0.1.0",
            "model": "m",
            "metrics": {"m": {"count": "t", "measure": "t.c"}},
        },
        {
            "model2data-metrics": "0.1.0",
            "model": "m",
            "metrics": {"m": {"count": "t", "agg": "sum"}},
        },
        {
            "model2data-metrics": "0.1.0",
            "model": "m",
            "metrics": {"m": {"expression": "a", "time": "t.c"}},
        },
        {
            "model2data-metrics": "0.1.0",
            "model": "m",
            "metrics": {
                "m": {"ratio": {"numerator": "a", "denominator": "b"}, "where": {"t.c": 1}}
            },
        },
        {
            "model2data-metrics": "0.1.0",
            "model": "m",
            "metrics": {"m": {"count": "t", "where": {"t.c": {"not": 1}}}},
        },
        {"model2data-metrics": "0.1.0", "model": "m", "dimensions": {"t.c": "yes"}},
    ],
)
def test_the_schema_refuses(broken):
    assert list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(broken))


def test_the_aggregations_are_the_model_specs_seven():
    model_schema = json.loads((SPEC / "model.schema.json").read_text(encoding="utf-8"))
    measure = model_schema["$defs"]["column"]["properties"]["measure"]["oneOf"][1]["enum"]
    assert tuple(measure) == AGGREGATIONS
    assert tuple(SCHEMA["$defs"]["metric"]["properties"]["agg"]["enum"]) == AGGREGATIONS


def test_formats_operators_and_kinds_match_the_code():
    metric = SCHEMA["$defs"]["metric"]
    assert tuple(metric["properties"]["format"]["enum"]) == FORMATS
    operators = SCHEMA["$defs"]["condition"]["oneOf"][2]["properties"]
    assert tuple(operators) == OPERATORS
    assert set(ORDER_OPERATORS) <= set(OPERATORS)
    assert [branch["required"][0] for branch in metric["oneOf"]] == list(KIND_KEYS)
    for key in ("measure", "count", "ratio", "expression", "time", "where", "agg"):
        assert f"`{key}`" in README


def test_every_metric_key_is_a_field_of_the_typed_metric():
    keys = set(SCHEMA["$defs"]["metric"]["properties"])
    fields = {f.name for f in dataclasses.fields(Metric)} - {"kind", "inferred", "extensions"}
    assert keys == fields


def test_inferred_names_and_expression_names_are_metric_names():
    pattern = re.compile(SCHEMA["$defs"]["metricName"]["pattern"])
    for text in ("Order Items", "2024", "x", "__", "raw.orders_total", "Ça va"):
        assert pattern.match(metric_name(text))
    assert "[a-z][a-z0-9_]*" in README  # the grammar's NAME is the same pattern


@pytest.mark.parametrize(
    "path, ok",
    [
        ("t.c", True),
        ("s.t.c", True),
        ("c", False),
        ("a.b.c.d", False),
        (".c", False),
        ("t.", False),
    ],
)
def test_the_column_path_pattern(path, ok):
    assert bool(re.match(SCHEMA["$defs"]["columnPath"]["pattern"], path)) is ok


def test_publishing_lists_the_metrics_schema():
    publishing = (SPEC / "PUBLISHING.md").read_text(encoding="utf-8")
    assert (
        "https://www.jbanalytica.com/model2data/spec/metrics/0.1.0/metrics.schema.json"
        in publishing
    )
    assert "model2data/spec/metrics/metrics.schema.json` of engine 1.13.0" in publishing


def test_the_model_spec_links_the_metrics_spec():
    assert "metrics/README.md" in (SPEC / "README.md").read_text(encoding="utf-8")
