"""A seed and an `as_of` day name one dataset, in any process, for every type.

`--seed` is sold as byte-identical output -- safe to commit, safe to diff in
CI -- so nothing a generator draws may come from outside the seeded RNG. UUIDs
did: `uuid.uuid4()` reads the operating system's randomness, so every `uuid`
key column, every table referencing one, and every unique text column the
de-duplicator had to fall back to UUIDs for came out different on each run.

Each run below is a fresh interpreter with its own `PYTHONHASHSEED`, so a
generator that leaned on set or dict ordering of strings would fail here too,
not only one that reads the clock or the OS.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

UUID_SCHEMA = """
Table loads {
  load_id uuid [pk]
  checksum hash [unique]
}
Table stories {
  id bigint [pk]
  load_id uuid [not null]
  external_ref varchar [unique]
}
Ref: stories.load_id > loads.load_id
"""

_GENERATE = """
import datetime as dt, sys
from pathlib import Path
from model2data.generate.core import generate_data_from_dbml
from model2data.parse.dbml import parse_dbml
tables, refs = parse_dbml(Path(sys.argv[1]))
frames = generate_data_from_dbml(
    tables, refs, base_rows=40, seed=7, as_of=dt.datetime(2026, 1, 1)
)
for name in sorted(frames):
    sys.stdout.write(f"== {name}\\n" + frames[name].to_csv(index=False))
"""


def _generate_in_fresh_process(dbml: Path, hash_seed: str) -> str:
    return subprocess.run(
        [sys.executable, "-c", _GENERATE, str(dbml)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONHASHSEED": hash_seed},
    ).stdout


@pytest.fixture
def uuid_schema(tmp_path: Path) -> Path:
    path = tmp_path / "uuids.dbml"
    path.write_text(UUID_SCHEMA)
    return path


def test_uuid_and_hash_columns_follow_the_seed(uuid_schema: Path):
    first = _generate_in_fresh_process(uuid_schema, "1")
    second = _generate_in_fresh_process(uuid_schema, "2")
    assert first == second


@pytest.mark.parametrize("example", sorted(EXAMPLES.glob("*.dbml")), ids=lambda p: p.stem)
def test_every_example_reproduces_across_processes(example: Path):
    assert _generate_in_fresh_process(example, "1") == _generate_in_fresh_process(example, "2")
