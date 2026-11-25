import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.naming import dbt_names, for_dbt
from model2data.generate.core import generate_data_from_dbml
from model2data.model import load
from model2data.model.engine import to_engine
from model2data.parse.dbml import parse_dbml
from model2data.utils import normalize_identifier


def test_dbml_names_preserved_but_dbt_names_normalized(tmp_path):
    """
    DBML table names must remain untouched internally,
    while dbt artifacts (seeds/models) use normalized identifiers.

    This test enforces the DBML → dbt naming boundary.
    """

    dbml_path = Path("examples/hackernews.dbml")

    # -------------------------
    # Parse DBML
    # -------------------------
    tables, refs = parse_dbml(dbml_path)

    # Pick a table that needs normalization
    # (kids is a good example because it becomes stories__kids)
    assert "stories__kids" in tables

    dbml_name = "stories__kids"
    dbt_name = normalize_identifier(dbml_name)

    # -------------------------
    # Generate data
    # -------------------------
    data = generate_data_from_dbml(
        tables=tables,
        refs=refs,
        base_rows=20,
        seed=1,
    )

    # Data keys must use DBML names
    assert dbml_name in data
    assert dbt_name not in data

    # -------------------------
    # Simulate dbt seed naming
    # -------------------------
    seed_filename = f"raw_{dbt_name}.csv"

    # This is what cli.py writes
    seeds_path = tmp_path / "seeds"
    seeds_path.mkdir()

    data[dbml_name].to_csv(seeds_path / seed_filename, index=False)

    # dbt sees normalized names only
    assert (seeds_path / seed_filename).exists()


# Every name dbt could trip over at once: tables it renames, named by `after` (`raw.users`, and a
# capital), columns that are reserved words under each kind of test, members with a quote.
AWKWARD = """\
model2data: 0.5.0
enums:
  kind: ["it's", 'say "hi"', plain]
tables:
  {users}:
    columns:
      id: {{type: int, pk: true}}
      created_at: timestamp
  {posts}:
    columns:
      id: {{type: int, pk: true}}
      by: {{type: int, not_null: true, references: {users}.id}}
      order: {{type: int, unique: true}}
      kind: {{type: kind, not_null: true}}
      select: {{type: int, generate: {{min: 0, max: 9, null_rate: 0.2, distinct: 5}}}}
      posted_at: {{type: timestamp, generate: {{after: {users}.created_at}}}}
      end: {{type: timestamp, generate: {{after: [posted_at, {users}.created_at]}}}}
      group: {{type: text, generate: {{when: {{kind: ["it's", plain]}}}}}}
"""


def test_for_dbt_renames_the_tables_an_after_names():
    inputs = to_engine(load(AWKWARD.format(users="raw.users", posts="raw.posts")))
    tables, _ = for_dbt(inputs.tables, inputs.refs, dbt_names(inputs.tables))
    after = {c.name: (c.note or {}).get("after") for c in tables["raw_posts"].columns}
    assert after["posted_at"] == "raw_users.created_at"
    assert after["end"] == ["posted_at", "raw_users.created_at"]
    # The model's own tables keep the names they had.
    own = {c.name: (c.note or {}).get("after") for c in inputs.tables["raw.posts"].columns}
    assert own["posted_at"] == "raw.users.created_at"


@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt CLI not found on PATH")
@pytest.mark.parametrize("users, posts", [("raw.users", "raw.posts"), ("Users", "Posts")])
@pytest.mark.parametrize("defects", [None, "training"])
def test_awkward_names_build(tmp_path, monkeypatch, users, posts, defects):
    """A clean project passes; a broken one fails the tests its report names, and none errors."""
    monkeypatch.chdir(tmp_path)
    model = tmp_path / "awkward.model2data.yml"
    model.write_text(AWKWARD.format(users=users, posts=posts))
    arguments = ["--file", str(model), "--rows", "40", "--seed", "1", "--as-of", "2026-01-01"]
    arguments += ["--hint-tests", "error", "--unit-tests", "--name", "awkward"]
    if defects:
        arguments += ["--defects", defects]
    result = CliRunner().invoke(app, ["generate", *arguments])
    assert result.exit_code == 0, result.output

    project = tmp_path / "dbt_awkward"
    built = subprocess.run(
        ["dbt", "build", "--profiles-dir", "."], cwd=project, capture_output=True, text=True
    )
    results = json.loads((project / "target" / "run_results.json").read_text())["results"]
    assert not [r["unique_id"] for r in results if r["status"] == "error"], built.stdout
    failed = {r["unique_id"].split(".")[2] for r in results if r["status"] == "fail"}
    report = project / "EXPECTED_FAILURES.md"
    named = report.read_text() if defects else ""
    assert failed == set(re.findall(r"^\| `(\w+)` \| FAIL", named, re.M))
    assert bool(failed) == bool(defects)
