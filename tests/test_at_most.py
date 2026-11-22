"""`at_most` on a foreign key (spec 0.9.0): no parent gets more than N child rows."""

from __future__ import annotations

import copy
import random
import shutil
import subprocess
from dataclasses import replace
from datetime import date
from typing import Any, Optional

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from model2data.cli import app
from model2data.dbt.hint_tests import CHILDREN_MACROS_FILE, hint_tests_for, write_hint_macros
from model2data.dbt.tests import dbt_tests
from model2data.defects.checks import as_seeded, fails
from model2data.generate import rollups
from model2data.generate.core import generate_data_from_dbml, get_unsatisfied_rules
from model2data.generate.days import generate_days
from model2data.generate.faker import generate_column_values
from model2data.model import from_dict, to_engine
from model2data.model.dbml import from_dbml
from model2data.model.document import document_version
from model2data.model.errors import ModelError
from model2data.model.validate import check
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = date(2026, 11, 22)


def _league(club_id: Optional[dict] = None, *, version: str = "0.9.0", **run: Any) -> dict:
    """Clubs and their players; `club_id` is the players' foreign key."""
    return copy.deepcopy(
        {
            "model2data": version,
            "tables": {
                "clubs": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "founded_at": {"type": "timestamp", "not_null": True},
                    }
                },
                "players": {
                    "columns": {
                        "id": {"type": "bigint", "pk": True},
                        "club_id": club_id
                        or {
                            "type": "bigint",
                            "not_null": True,
                            "references": "clubs.id",
                            "generate": {"at_most": 25},
                        },
                    }
                },
            },
            **({"run": run} if run else {}),
        }
    )


def _club_id(**generate: Any) -> dict:
    return {"type": "bigint", "not_null": True, "references": "clubs.id", "generate": generate}


def _generate(document: dict, rows: dict, seed: int = 1, **options: Any) -> dict:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables, inputs.refs, row_overrides=rows, seed=seed, as_of=AS_OF, **options
    )


def _squads(frames: dict, column: str = "club_id", table: str = "players") -> pd.Series:
    return frames[table][column].dropna().value_counts()


def _errors(document: dict) -> list[str]:
    return [str(issue) for issue in check(document) if issue.is_error]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("skew", [0.0, 0.8, 1.0])
@pytest.mark.parametrize("seed", range(6))
def test_no_parent_is_ever_given_more_than_at_most(seed, skew):
    document = _league(_club_id(at_most=25, skew=skew))
    frames = _generate(document, {"clubs": 12, "players": 280}, seed=seed)
    squads = _squads(frames)
    assert len(frames["players"]) == 280
    assert squads.max() <= 25
    assert set(squads.index) <= set(frames["clubs"]["id"])


def test_skew_still_makes_some_parents_popular_under_the_cap():
    frames = _generate(_league(_club_id(at_most=40, skew=1.0)), {"clubs": 20, "players": 300})
    squads = _squads(frames)
    assert squads.max() == 40  # the popular clubs fill up
    assert squads.min() < 10  # and the others stay small


def test_rows_that_exactly_fill_every_parent_give_each_one_at_most():
    for skew in (0.0, 0.9):
        frames = _generate(_league(_club_id(at_most=7, skew=skew)), {"clubs": 30, "players": 210})
        squads = _squads(frames)
        assert len(squads) == 30 and (squads == 7).all()


def test_at_most_one_on_a_plain_foreign_key_gives_each_parent_one_row():
    frames = _generate(_league(_club_id(at_most=1)), {"clubs": 50, "players": 50})
    assert (_squads(frames) == 1).all()


def test_with_at_least_every_parent_falls_in_the_range():
    for seed in range(5):
        frames = _generate(
            _league(_club_id(at_least=18, at_most=25, skew=0.9)),
            {"clubs": 12, "players": 260},
            seed=seed,
        )
        squads = _squads(frames)
        assert len(squads) == 12
        assert squads.min() >= 18 and squads.max() <= 25


def test_a_null_is_no_child():
    club_id = {
        "type": "bigint",
        "references": "clubs.id",
        "generate": {"at_most": 2, "null_rate": 0.5},
    }
    frames = _generate(_league(club_id), {"clubs": 10, "players": 40})
    players = frames["players"]
    assert players["club_id"].isna().sum() == 20  # the null rate holds exactly
    assert _squads(frames).max() <= 2 and len(players) == 40


def test_a_default_standing_in_for_null_counts_as_a_child():
    club_id = {
        "type": "bigint",
        "references": "clubs.id",
        "default": 1,
        "generate": {"at_most": 3, "null_rate": 0.1},
    }
    frames = _generate(_league(club_id), {"clubs": 10, "players": 30})
    squads = _squads(frames)
    assert squads.max() <= 3
    assert squads[1] == 3  # the three default rows fill club 1


def test_more_rows_than_the_parents_hold_is_an_error_not_a_cap():
    with pytest.raises(ValueError) as raised:
        _generate(_league(), {"clubs": 12, "players": 301})
    assert str(raised.value) == (
        'players.club_id: "at_most" 25 lets its 12 parent value(s) hold 300 row(s) and 301 '
        'more need a parent: lower the table\'s rows or raise "at_most".'
    )


def test_the_capacity_error_counts_what_at_least_gave():
    with pytest.raises(ValueError, match=r"hold 12 row\(s\), 8 of them already given, and 5 more"):
        _generate(_league(_club_id(at_least=2, at_most=3)), {"clubs": 4, "players": 13})


def test_the_command_line_rows_can_be_what_makes_it_impossible(tmp_path, monkeypatch):
    path = tmp_path / "league.model2data.yml"
    path.write_text(yaml.safe_dump(_league()))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        ["generate", "--file", str(path), "--rows", "400", "--rows-for", "clubs=12", "--seed", "1"],
    )
    assert result.exit_code != 0
    assert '"at_most" 25 lets its 12 parent value(s) hold 300 row(s)' in result.output


def test_the_draw_is_the_seed_s_and_without_at_most_nothing_changes():
    column = ColumnDef("club_id", "bigint", {"not null"}, note={"skew": 0.7})
    parents = pd.Series(range(1, 21))

    def draw(**more: Any) -> list:
        random.seed(5)
        return generate_column_values(column, 200, fk_series=parents, table_name="t", **more)

    assert draw() == draw(at_most=0)
    assert draw(at_most=12) == draw(at_most=12)
    assert max(pd.Series(draw(at_most=12)).value_counts()) <= 12


def test_a_self_reference_keeps_at_most():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "staff": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "manager_id": {
                        "type": "bigint",
                        "references": "staff.id",
                        "generate": {"at_most": 3, "null_rate": 0.1},
                    },
                }
            }
        },
    }
    frames = _generate(document, {"staff": 60})
    assert _squads(frames, "manager_id", "staff").max() <= 3


def test_a_cross_table_after_moves_the_date_rather_than_the_parent():
    document = _league()
    document["tables"]["players"]["columns"]["signed_at"] = {
        "type": "timestamp",
        "not_null": True,
        "generate": {"after": "clubs.founded_at"},
    }
    frames = _generate(document, {"clubs": 12, "players": 300}, seed=3)
    players, clubs = frames["players"], frames["clubs"]
    assert (_squads(frames) == 25).all()
    founded = players["club_id"].map(clubs.set_index("id")["founded_at"])
    assert (pd.to_datetime(players["signed_at"]) >= pd.to_datetime(founded)).all()


def test_a_composite_key_repair_keeps_the_capped_column():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "posts": {"columns": {"id": {"type": "bigint", "pk": True}}},
            "tags": {"columns": {"id": {"type": "bigint", "pk": True}}},
            "post_tags": {
                "keys": [{"pk": ["post_id", "tag_id"]}],
                "columns": {
                    "post_id": {
                        "type": "bigint",
                        "references": "posts.id",
                        "generate": {"at_most": 4},
                    },
                    "tag_id": {"type": "bigint", "references": "tags.id"},
                },
            },
        },
    }
    frames = _generate(document, {"posts": 30, "tags": 25, "post_tags": 110})
    links = frames["post_tags"]
    assert _squads(frames, "post_id", "post_tags").max() <= 4
    assert not links.duplicated(["post_id", "tag_id"]).any()


def test_a_rule_draws_the_capped_column_again_among_the_parents_with_room():
    document = {
        "model2data": "0.9.0",
        "tables": {
            "clubs": {"columns": {"id": {"type": "bigint", "pk": True}}},
            "games": {
                "rules": [{"away_club_id": {"ne": {"column": "home_club_id"}}}],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "home_club_id": {
                        "type": "bigint",
                        "not_null": True,
                        "references": "clubs.id",
                    },
                    "away_club_id": {
                        "type": "bigint",
                        "not_null": True,
                        "references": "clubs.id",
                        "generate": {"at_most": 5},
                    },
                },
            },
        },
    }
    for seed in range(4):
        frames = _generate(document, {"clubs": 10, "games": 48}, seed=seed)
        games = frames["games"]
        assert _squads(frames, "away_club_id", "games").max() <= 5
        assert get_unsatisfied_rules() == []
        assert (games["home_club_id"] != games["away_club_id"]).all()


def test_days_after_the_first_are_refused_for_a_table_with_incremental():
    document = _league()
    document["tables"]["players"]["incremental"] = {"new_per_day": 3}
    with pytest.raises(ValueError, match=r'players\.club_id: "at_most" holds for the first day'):
        generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validation_reports_rows_the_parents_cannot_hold():
    document = _league(rows_per_table={"clubs": 12, "players": 301})
    assert _errors(document) == [
        "tables.players.columns.club_id.generate.at_most: 25 lets the 12 clubs rows hold 300 "
        "players rows, and players has 301 with a club_id: lower its rows or raise `at_most`"
    ]
    assert _errors(_league(rows_per_table={"clubs": 12, "players": 300})) == []
    with pytest.raises(ModelError):
        from_dict(document)


def test_validation_counts_a_nullable_column_s_rows_with_a_value():
    def nullable(**generate: Any) -> dict:
        return {"type": "bigint", "references": "clubs.id", "generate": {"at_most": 2, **generate}}

    # 40 rows, half null: 20 children for 10 clubs of 2.
    assert (
        _errors(_league(nullable(null_rate=0.5), rows_per_table={"clubs": 10, "players": 40})) == []
    )
    assert _errors(_league(nullable(null_rate=0.4), rows_per_table={"clubs": 10, "players": 40}))
    # The default null rate leaves 80 of 100 rows with a club.
    assert _errors(_league(nullable(), rows_per_table={"clubs": 40, "players": 100})) == []
    assert _errors(_league(nullable(), rows_per_table={"clubs": 39, "players": 100}))
    # With at_least, the covered rows are never null and the rest take the null rate.
    ranged = nullable(at_least=1, null_rate=0.5)
    assert _errors(_league(ranged, rows_per_table={"clubs": 10, "players": 30})) == []
    assert _errors(_league(ranged, rows_per_table={"clubs": 10, "players": 32}))


def test_validation_leaves_what_the_document_does_not_say_to_the_generator():
    assert _errors(_league()) == []  # no run
    assert _errors(_league(rows_per_table={"players": 900})) == []  # the clubs' rows unknown
    document = _league(
        {
            "type": "bigint",
            "not_null": True,
            "references": "clubs.code",
            "generate": {"at_most": 1},
        },
        rows_per_table={"clubs": 2, "players": 900},
    )
    document["tables"]["clubs"]["columns"]["code"] = {"type": "int"}  # not a key: values repeat
    assert not [e for e in _errors(document) if "at_most" in e]


def test_at_least_above_at_most_is_an_error():
    assert _errors(_league(_club_id(at_least=5, at_most=3))) == [
        "tables.players.columns.club_id.generate.at_most: 3 is below `at_least` 5: no parent can "
        "have at least that many rows and at most this many"
    ]


@pytest.mark.parametrize(
    ("club_id", "message"),
    [
        (
            {"type": "bigint", "generate": {"at_most": 3}},
            "tables.players.columns.club_id.generate.at_most: only sits on a foreign-key column",
        ),
        (
            _club_id(at_most=0),
            "tables.players.columns.club_id.generate.at_most: must be 1 or more",
        ),
        (
            _club_id(at_most=2.5),
            "tables.players.columns.club_id.generate.at_most: must be",
        ),
    ],
    ids=["not-a-foreign-key", "zero", "fraction"],
)
def test_validation_reports_an_at_most_that_cannot_be(club_id, message):
    errors = _errors(_league(club_id))
    assert any(error.startswith(message) for error in errors), errors


def test_at_most_on_a_unique_foreign_key_is_refused():
    club_id = {**_club_id(at_most=2), "unique": True}
    with pytest.raises(ModelError) as raised:
        from_dict(_league(club_id))
    assert "cannot sit on a unique foreign key" in str(raised.value)


def test_at_most_is_spec_0_9_0():
    assert _errors(_league(version="0.8.0")) == [
        "tables.players.columns.club_id.generate.at_most: `at_most` is spec 0.9.0, and the "
        "document is written against 0.8: write `model2data: 0.9.0`"
    ]
    model = from_dict(_league())
    assert document_version(replace(model, version="0.6.0")) == "0.9.0"
    assert document_version(model) == "0.9.0"


def test_dbml_with_at_most_converts_to_a_0_9_document():
    model = from_dbml(
        """
Table clubs {
  id bigint [pk]
}
Table players {
  id bigint [pk]
  club_id bigint [not null, ref: > clubs.id, note: '{"at_least": 18, "at_most": 25}']
}
"""
    )
    assert str(document_version(model)) == "0.9.0"


def test_the_cli_says_the_spec_it_conforms_to(tmp_path):
    path = tmp_path / "league.model2data.yml"
    path.write_text(yaml.safe_dump(_league()))
    result = CliRunner().invoke(app, ["validate", str(path)])
    assert result.exit_code == 0, result.output
    assert "conforms to spec 0.9.0" in result.output


# ---------------------------------------------------------------------------
# The engine's own checks (DBML notes, hand-built tables)
# ---------------------------------------------------------------------------
def _tables(note: dict, settings: Optional[set] = None) -> tuple[dict, list]:
    tables = {
        "clubs": TableDef(name="clubs", columns=[ColumnDef("id", "int", {"pk"})]),
        "players": TableDef(
            name="players",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("club_id", "int", settings or {"not null"}, note=note),
                ColumnDef("goals", "int", set(), note=note),
            ],
        ),
    }
    refs = [
        {
            "source_table": "players",
            "source_column": "club_id",
            "target_table": "clubs",
            "target_column": "id",
        }
    ]
    return tables, refs


def _problems(note: dict, settings: Optional[set] = None, column: str = "club_id") -> list[str]:
    tables, refs = _tables(note, settings)
    return [
        p.message
        for p in rollups.problems(tables, refs)
        if (p.table, p.column) == ("players", column)
    ]


def test_the_engine_reports_each_misplaced_at_most():
    assert _problems({"at_most": 2}, column="goals") == [
        '"at_most" only applies to foreign-key columns.'
    ]
    assert _problems({"at_most": 0}) == ['"at_most" must be a whole number of 1 or more (got 0).']
    assert _problems({"at_most": 2}, {"unique"}) == [
        '"at_most" cannot sit on a unique foreign key: a one-to-one takes each parent at most '
        "once, so no parent can be given several rows."
    ]
    assert _problems({"at_least": 4, "at_most": 2}) == [
        '"at_most" 2 is below "at_least" 4: no parent can have at least that many rows and at '
        "most this many."
    ]
    assert _problems({"at_least": 2, "at_most": 2}) == []


def test_the_engine_refuses_it_before_generating():
    tables, refs = _tables({"at_least": 4, "at_most": 2})
    with pytest.raises(ValueError, match=r'players\.club_id: "at_most" 2 is below "at_least" 4'):
        generate_data_from_dbml(tables, refs, base_rows=5, seed=1)


def test_derive_and_at_most_do_not_sit_together():
    document = _league()
    columns = document["tables"]["players"]["columns"]
    columns["loaned_to"] = {
        "type": "bigint",
        "references": "clubs.id",
        "generate": {"derive": {"copy": "club_id"}, "at_most": 2},
    }
    assert any("loaned_to.generate" in error and "at_most" in error for error in _errors(document))


# ---------------------------------------------------------------------------
# dbt
# ---------------------------------------------------------------------------
def test_at_most_writes_its_dbt_test():
    found = [t for t in hint_tests_for(from_dict(_league())) if t.hint == "at_most"]
    assert [(t.table, t.column, t.test, t.arguments) for t in found] == [
        ("players", "club_id", "model2data_max_rows_per_value", {"max_count": 25})
    ]


def test_the_children_macros_are_written_only_beside_a_test_that_needs_them(tmp_path):
    write_hint_macros(tmp_path / "plain")
    assert not (tmp_path / "plain" / "macros" / CHILDREN_MACROS_FILE).exists()
    write_hint_macros(tmp_path / "capped", children=True)
    sql = (tmp_path / "capped" / "macros" / CHILDREN_MACROS_FILE).read_text()
    assert "{% test model2data_max_rows_per_value(" in sql


def test_the_defects_check_reads_the_test_as_its_sql_does():
    document = _league()
    inputs = to_engine(from_dict(document))
    [test] = [
        t
        for t in dbt_tests(inputs.tables, inputs.refs, hint_tests="error")
        if t.type == "model2data_max_rows_per_value"
    ]
    frames = _generate(document, {"clubs": 12, "players": 300})
    seeded = {key: as_seeded(frame) for key, frame in frames.items()}
    assert not fails(test, seeded)
    broken = frames["players"].copy()
    broken.loc[broken.index[:26], "club_id"] = 1
    assert fails(test, {**seeded, "players": as_seeded(broken)})


DBT = shutil.which("dbt")


@pytest.mark.skipif(DBT is None, reason="dbt CLI not found on PATH")
def test_the_dbt_test_passes_on_the_data_and_catches_a_parent_over_the_cap(tmp_path, monkeypatch):
    path = tmp_path / "league.model2data.yml"
    path.write_text(yaml.safe_dump(_league(rows_per_table={"clubs": 12, "players": 290})))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        app,
        [
            "generate",
            "--file",
            str(path),
            "--seed",
            "1",
            "--as-of",
            "2026-11-22",
            "--name",
            "lg",
            "--hint-tests",
            "error",
        ],
    )
    assert result.exit_code == 0, result.output
    project = tmp_path / "dbt_lg"
    assert (project / "macros" / CHILDREN_MACROS_FILE).exists()

    def build() -> subprocess.CompletedProcess:
        return subprocess.run(
            [DBT or "dbt", "build", "--profiles-dir", "."],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=240,
        )

    clean = build()
    assert clean.returncode == 0, clean.stdout + clean.stderr
    seed = project / "seeds" / "raw" / "players.csv"
    frame = pd.read_csv(seed)
    frame.loc[frame.index[:26], "club_id"] = 1
    frame.to_csv(seed, index=False)
    broken = build()
    assert broken.returncode != 0
    assert any(
        "model2data_max_rows_per_value" in line and "FAIL" in line
        for line in broken.stdout.splitlines()
    ), broken.stdout
