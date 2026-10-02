"""The same model, seed and `--as-of` write byte-identical files in every 1.x release.

That is the product's promise: a committed dataset, a diffed CI run and a teaching
exercise all lean on it. This module enforces it. Each case runs the real CLI
in-process, hashes every file it writes, and compares against the hashes in
`fixtures/determinism.json`, which were recorded from a published release.

If a case fails, output changed for the same seed. That is a breaking change and
needs a major version; it is not a snapshot to refresh. Faker or pandas releases
that change the data show up here too (CI runs this file against the newest of both).

Re-recording is for adding or deliberately versioned changes only:

    MODEL2DATA_RECORD_DETERMINISM=1 uv run pytest tests/test_determinism.py

The engine's version string is the one thing normalised (the defects report and
EXPECTED_FAILURES.md name the engine that wrote them).
"""

import hashlib
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model2data import __version__
from model2data.cli import app

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN = FIXTURES / "determinism.json"
VERSION_PLACEHOLDER = "<ENGINE_VERSION>"

_COMMON = ["--seed", "7", "--as-of", "2026-03-15", "--rows", "40", "--name", "det"]

# case id -> (model path, extra CLI args)
CASES: dict[str, tuple[Path, list[str]]] = {}


def _case(case_id: str, model: Path, *args: str) -> None:
    CASES[case_id] = (model, list(args))


# Every example model once, as the model's own run settings have it.
for _path in sorted([*EXAMPLES.glob("*.model2data.yml"), *EXAMPLES.glob("*.dbml")]):
    _case(f"example-{_path.name}", _path)

_ECOM = EXAMPLES / "ecommerce.model2data.yml"
_TRAIN = EXAMPLES / "ecommerce_training.model2data.yml"
_ADV = EXAMPLES / "advanced_features.dbml"

for _preset in ("none", "clean", "training", "messy"):
    _case(f"preset-{_preset}", _TRAIN, "--defects", _preset, "--days", "3")
_case("days-2", EXAMPLES / "ecommerce_daily.model2data.yml", "--days", "2")
_case("days-changelog", _TRAIN, "--days", "2", "--days-format", "changelog")
_case("unit-tests", _ECOM, "--unit-tests")
_case("table-seed", _ECOM, "--table-seed", "orders=4")
_case("skew", EXAMPLES / "saas_platform.model2data.yml", "--skew", "0.8")
_case("incremental-history", FIXTURES / "defects" / "hcol.model2data.yml", "--days", "3")
_case("composite-keys", FIXTURES / "composite_only.dbml")
for _locale in ("en_US", "nl_BE", "de_DE", "fr_FR", "ja_JP"):
    _case(f"locale-{_locale}", _ECOM, "--locale", _locale)


def _normalise(name: str, data: bytes) -> bytes:
    if name.endswith((".json", ".md")):
        return data.replace(__version__.encode(), VERSION_PLACEHOLDER.encode())
    return data


def _run(case_id: str, workdir: Path) -> dict[str, str]:
    model, args = CASES[case_id]
    cwd = Path.cwd()
    os.chdir(workdir)
    try:
        result = CliRunner().invoke(app, ["generate", "-f", str(model), *_COMMON, *args])
    finally:
        os.chdir(cwd)
    assert result.exit_code == 0, result.output
    hashes = {}
    for path in sorted(workdir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(workdir).as_posix()
            data = _normalise(path.name, path.read_bytes())
            # Nothing may embed where it was written: that would differ per machine.
            assert str(workdir).encode() not in data, f"{rel} embeds the output directory"
            hashes[rel] = hashlib.sha256(data).hexdigest()
    return hashes


def _golden() -> dict[str, dict[str, str]]:
    return json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}


@pytest.fixture(scope="module")
def recorded():
    """Collects hashes while recording; written once after the module's cases ran."""
    fresh: dict[str, dict[str, str]] = {}
    yield fresh
    if os.environ.get("MODEL2DATA_RECORD_DETERMINISM") and fresh:
        merged = {**_golden(), **fresh} if len(fresh) < len(CASES) else fresh
        GOLDEN.write_text(json.dumps(merged, indent=1, sort_keys=True) + "\n")


@pytest.mark.parametrize("case_id", list(CASES))
def test_same_seed_same_bytes(case_id, tmp_path, recorded):
    actual = _run(case_id, tmp_path)
    if os.environ.get("MODEL2DATA_RECORD_DETERMINISM"):
        recorded[case_id] = actual
        return
    expected = _golden().get(case_id)
    assert expected is not None, (
        f"no recorded hashes for case {case_id!r}; record them with "
        "MODEL2DATA_RECORD_DETERMINISM=1 (only when adding a case)"
    )
    changed = sorted(f for f in expected.keys() & actual.keys() if expected[f] != actual[f])
    added = sorted(actual.keys() - expected.keys())
    missing = sorted(expected.keys() - actual.keys())
    assert not (changed or added or missing), (
        f"DETERMINISM BROKEN in case {case_id!r}: the same model, seed and --as-of no "
        "longer write the same files.\n"
        f"  changed files: {changed}\n  new files: {added}\n  missing files: {missing}\n"
        "Changing output for the same seed breaks model2data's promise of byte-identical "
        "output across 1.x releases and needs a MAJOR version. Do not just re-record the "
        "hashes. If the change comes from a Faker or pandas release, pin or work around it."
    )


def test_golden_matches_cases():
    assert set(_golden()) == set(CASES), "determinism.json and CASES disagree; re-record"
