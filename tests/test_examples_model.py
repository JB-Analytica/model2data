"""Every example is a `.model2data.yml` document and the DBML it was converted from.

The two generate the same bytes under a seed and an `as_of`: the document is
what `model2data convert` wrote for the DBML (plus a description), and both
reach the generator through the same `to_engine`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.model import dump, from_dbml, load, to_engine

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
DOCUMENTS = sorted(EXAMPLES.glob("*.model2data.yml"))


def _generate(path: Path) -> str:
    inputs = to_engine(load(path))
    frames = generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=40, seed=11, as_of=datetime(2026, 1, 1)
    )
    return "".join(f"== {name}\n" + frames[name].to_csv(index=False) for name in sorted(frames))


def test_every_dbml_example_has_its_document():
    assert [path.name.replace(".model2data.yml", "") for path in DOCUMENTS] == [
        path.stem for path in sorted(EXAMPLES.glob("*.dbml"))
    ]


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_the_document_and_its_dbml_generate_the_same_bytes(document: Path):
    dbml = EXAMPLES / document.name.replace(".model2data.yml", ".dbml")
    assert _generate(document) == _generate(dbml)


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_the_document_is_the_dbml_converted(document: Path):
    dbml = EXAMPLES / document.name.replace(".model2data.yml", ".dbml")
    converted = from_dbml(dbml.read_text(encoding="utf-8"))
    model = load(document)
    converted.description = model.description
    assert model == converted


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
def test_the_document_is_in_the_canonical_style(document: Path):
    text = document.read_text(encoding="utf-8")
    assert dump(load(document)) == text
