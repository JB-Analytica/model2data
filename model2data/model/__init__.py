"""The model2data model: spec 0.4.0 (and 0.2.x, 0.3.x), read, checked, written, and generated from.

A model is one document -- `<name>.model2data.yml`, or the same in JSON -- as
`model2data/spec/README.md` and `model2data/spec/model.schema.json` define it.

    >>> from model2data.model import load, dump, to_engine
    >>> model = load("examples/ecommerce.model2data.yml")
    >>> inputs = to_engine(model)          # (tables, refs) for the generator
    >>> text = dump(model)                 # back to canonical YAML

`load` reads a file or text (YAML under the spec's YAML profile, JSON, or DBML
through `from_dbml`), `from_dict` takes an already-parsed document, and both
raise `ModelError` listing every issue, each with its document path.
"""

from model2data.model.dbml import from_dbml
from model2data.model.document import from_dict, to_dict
from model2data.model.dump import dump
from model2data.model.engine import EngineInputs, run_as_of, to_engine
from model2data.model.errors import Issue, ModelError
from model2data.model.reader import load, validate
from model2data.model.types import (
    DEFECT_PRESETS,
    DEFECT_TYPES,
    Column,
    Defect,
    Enum,
    ForeignKey,
    Group,
    Incremental,
    Key,
    Model,
    Reference,
    Relationship,
    Run,
    Shape,
    Table,
)
from model2data.model.validate import SCHEMA_URL, SPEC_VERSION

__all__ = [
    "DEFECT_PRESETS",
    "DEFECT_TYPES",
    "SCHEMA_URL",
    "SPEC_VERSION",
    "Column",
    "Defect",
    "EngineInputs",
    "Enum",
    "ForeignKey",
    "Group",
    "Incremental",
    "Issue",
    "Key",
    "Model",
    "ModelError",
    "Reference",
    "Relationship",
    "Run",
    "Shape",
    "Table",
    "dump",
    "from_dbml",
    "from_dict",
    "load",
    "run_as_of",
    "to_dict",
    "to_engine",
    "validate",
]
