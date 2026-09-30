"""The published spec's schema is a valid JSON Schema and accepts its own example.

`model2data/spec/model.schema.json` is normative, so a schema that is itself
malformed, or that refuses the reference example written beside it, is a
broken spec rather than a broken document.
"""

import json
from pathlib import Path

import jsonschema
import pytest
import yaml

SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec"
SCHEMA = json.loads((SPEC / "model.schema.json").read_text(encoding="utf-8"))


def _load(path: Path) -> dict:
    # PyYAML is YAML 1.1, not the spec's 1.2 profile; the examples avoid every
    # scalar the two read differently, so the schema check is unaffected.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_the_schema_is_a_valid_draft_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize(
    "example", sorted((SPEC / "examples").glob("*.model2data.yml")), ids=lambda p: p.stem
)
def test_the_reference_examples_validate(example: Path):
    jsonschema.Draft202012Validator(SCHEMA).validate(_load(example))


@pytest.mark.parametrize(
    "broken, where",
    [
        ({"model2data": "0.2.0", "tables": {}}, "no tables"),
        ({"model2data": "0.1.0", "tables": {"t": {"columns": {"id": "int"}}}}, "old version"),
        (
            {
                "model2data": "0.2.0",
                "tables": {"t": {"columns": {"a": {"type": "int", "generate": {"nul_rate": 0.1}}}}},
            },
            "typo in a hint",
        ),
        (
            {
                "model2data": "0.2.0",
                "tables": {
                    "t": {"columns": {"a": {"type": "int", "generate": {"null_rate": 1.5}}}}
                },
            },
            "fraction out of range",
        ),
        (
            {
                "model2data": "0.2.0",
                "tables": {
                    "t": {
                        "columns": {
                            "a": {
                                "type": "numeric",
                                "generate": {"distribution": {"kind": "normal", "median": 3}},
                            }
                        }
                    }
                },
            },
            "parameter of another distribution",
        ),
        (
            {
                "model2data": "0.2.0",
                "tables": {"t": {"columns": {"a": {"type": "int", "note": "x"}}}},
            },
            "0.1 note",
        ),
        (
            {"model2data": "0.2.0", "tables": {"a.b.c": {"columns": {"id": "int"}}}},
            "three-part table key",
        ),
        (
            {"model2data": "0.2.0", "tables": {"t": {"color": "orange", "columns": {"id": "int"}}}},
            "colour name",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_invalid_documents_are_refused(broken: dict, where: str):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(SCHEMA).validate(broken)


def test_extensions_are_allowed_everywhere_they_are_defined():
    jsonschema.Draft202012Validator(SCHEMA).validate(
        {
            "model2data": "0.2.0",
            "x-owner": "data team",
            "tables": {
                "t": {
                    "x-layout": {"x": 1},
                    "columns": {"a": {"type": "int", "x-pii": False, "generate": {"x-note": 1}}},
                }
            },
        }
    )
