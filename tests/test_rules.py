"""`rules` (spec 0.7.0): comparisons every generated row of a table satisfies."""

from __future__ import annotations

import copy
import os
from datetime import datetime
from typing import Any

import pandas as pd
import pytest
from typer.testing import CliRunner

from model2data.cli import app
from model2data.generate.core import generate_data_from_dbml, get_unsatisfied_rules
from model2data.generate.days import generate_days
from model2data.model import dump, from_dict, load, to_engine
from model2data.model.document import document_version, to_dict
from model2data.model.errors import ModelError
from model2data.model.validate import check

AS_OF = datetime(2026, 1, 1)
RULES = [
    {"home_team_id": {"ne": {"column": "away_team_id"}}},
    {"home_score": {"ne": {"column": "away_score"}}},
]


def _games(rules: Any = RULES) -> dict[str, Any]:
    document: dict[str, Any] = {
        "model2data": "0.7.0",
        "name": "season",
        "tables": {
            "teams": {"columns": {"id": {"type": "bigint", "pk": True}, "name": "text"}},
            "games": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "home_team_id": {"type": "bigint", "not_null": True, "references": "teams.id"},
                    "away_team_id": {"type": "bigint", "not_null": True, "references": "teams.id"},
                    "home_score": {"type": "int", "generate": {"min": 0, "max": 4}},
                    "away_score": {"type": "int", "generate": {"min": 0, "max": 4}},
                    "played_at": "timestamp",
                },
            },
        },
    }
    if rules is not None:
        document["tables"]["games"]["rules"] = copy.deepcopy(rules)
    return document


def _generate(document: dict, rows: int = 300, seed: int = 7) -> dict[str, pd.DataFrame]:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables,
        inputs.refs,
        base_rows=rows,
        seed=seed,
        as_of=AS_OF,
        row_overrides={"teams": 12},
    )


def _messages(document: dict) -> list[str]:
    with pytest.raises(ModelError) as raised:
        from_dict(document)
    return [str(issue) for issue in raised.value.issues]


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def test_every_row_holds_every_rule_and_the_foreign_keys_still_name_a_team():
    frames = _generate(_games())
    games = frames["games"]
    assert (games["home_team_id"] != games["away_team_id"]).all()
    assert (games["home_score"] != games["away_score"]).all()
    teams = set(frames["teams"]["id"])
    assert set(games["home_team_id"]) <= teams and set(games["away_team_id"]) <= teams
    assert games["home_score"].between(0, 4).all() and games["away_score"].between(0, 4).all()
    assert get_unsatisfied_rules() == []


def test_the_later_column_is_the_one_redrawn_and_the_earlier_one_is_untouched():
    plain = _generate(_games(rules=None))["games"]
    ruled = _generate(_games())["games"]
    assert (plain["home_team_id"] == plain["away_team_id"]).any()  # the rule had work to do
    # home_* come earlier in the table than away_*: they are what the plain run drew.
    assert plain["home_team_id"].equals(ruled["home_team_id"])
    assert plain["home_score"].equals(ruled["home_score"])
    changed = ruled["away_team_id"] != plain["away_team_id"]
    assert changed.any()
    # Rows that broke no rule keep what they drew.
    broke = (plain["home_team_id"] == plain["away_team_id"]) | (
        plain["home_score"] == plain["away_score"]
    )
    assert plain.loc[~broke, "away_team_id"].equals(ruled.loc[~broke, "away_team_id"])
    assert plain.loc[~broke, "away_score"].equals(ruled.loc[~broke, "away_score"])


def test_a_table_without_rules_and_the_other_tables_generate_the_same_bytes():
    plain = _generate(_games(rules=None))
    ruled = _generate(_games())
    assert plain["teams"].equals(ruled["teams"])
    for column in ("id", "home_team_id", "home_score", "played_at"):
        assert plain["games"][column].equals(ruled["games"][column])


def test_the_same_seed_gives_the_same_rows_and_another_seed_other_ones():
    first = _generate(_games())["games"]
    assert first.equals(_generate(_games())["games"])
    assert not first.equals(_generate(_games(), seed=8)["games"])


def test_a_constant_and_the_run_anchor_on_the_right():
    document = _games(
        [
            {"home_score": {"gte": 2}},
            {"played_at": {"lte": {"run": "as_of"}}},
            {"away_score": {"in": [0, 1, 2], "ne": 1}},
        ]
    )
    games = _generate(document)["games"]
    assert (games["home_score"] >= 2).all()
    assert games["away_score"].dropna().isin([0, 2]).all()
    assert (pd.to_datetime(games["played_at"]).dropna() <= AS_OF).all()


def test_a_null_operand_exempts_the_row_and_when_keeps_its_nulls():
    document = _games([{"home_score": {"ne": {"column": "away_score"}}}])
    games_def = document["tables"]["games"]["columns"]
    games_def["away_score"]["generate"]["null_rate"] = 0.3
    games = _generate(document)["games"]
    assert games["away_score"].isna().sum() > 0  # nulls are not turned into values
    known = games.dropna(subset=["home_score", "away_score"])
    assert (known["home_score"] != known["away_score"]).all()


def test_a_when_column_is_only_redrawn_where_it_has_a_value():
    document = _games([{"away_score": {"gte": {"column": "home_score"}}}])
    games_def = document["tables"]["games"]["columns"]
    document["enums"] = {"state": ["live", "done"]}
    games_def["state"] = "state"
    games_def["away_score"] = {
        "type": "int",
        "generate": {"min": 0, "max": 4, "when": {"state": ["done"]}},
    }
    # `home_score` and `away_score`: only done games have an away score.
    games = _generate(document)["games"]
    assert games.loc[games["state"] != "done", "away_score"].isna().all()
    done = games[games["state"] == "done"].dropna(subset=["home_score", "away_score"])
    assert len(done) > 0
    assert (done["away_score"] >= done["home_score"]).all()


def test_a_rule_the_value_space_cannot_meet_is_reported_not_forced():
    document = {
        "model2data": "0.7.0",
        "enums": {"only": ["one"]},
        "tables": {
            "t": {
                "rules": [{"a": {"ne": {"column": "b"}}}],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "a": {"type": "only", "not_null": True},
                    "b": {"type": "only", "not_null": True},
                },
            }
        },
    }
    inputs = to_engine(from_dict(document))
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=10, seed=1, as_of=AS_OF)
    assert len(frames["t"]) == 10
    assert get_unsatisfied_rules() == ["t: rule 1 (a ne {column: b}): 10 row(s)"]


def test_a_key_is_passed_over_for_the_other_column():
    document = {
        "model2data": "0.7.0",
        "tables": {
            "t": {
                "rules": [{"id": {"ne": {"column": "n"}}}],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "n": {"type": "int", "generate": {"min": 1, "max": 3}},
                },
            }
        },
    }
    inputs = to_engine(from_dict(document))
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=40, seed=1, as_of=AS_OF)
    assert (frames["t"]["id"] != frames["t"]["n"]).all()
    assert frames["t"]["id"].tolist() == list(range(1, 41))
    assert get_unsatisfied_rules() == []


def test_days_refuses_rules_over_a_table_that_moves_on():
    document = _games()
    document["tables"]["games"]["incremental"] = {"new_per_day": 5}
    with pytest.raises(ValueError, match="rules"):
        generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)
    # Days that move no table with rules are fine.
    generate_days(from_dict(_games()), 1, base_rows=20, seed=1, as_of=AS_OF)


# ---------------------------------------------------------------------------
# The document: version, reading, writing
# ---------------------------------------------------------------------------
def test_rules_in_an_older_document_are_an_error_that_says_which_version():
    for old in ("0.2.0", "0.5.0", "0.6.0"):
        document = _games()
        document["model2data"] = old
        messages = _messages(document)
        assert any(
            "`rules` is spec 0.7.0" in m and "write `model2data: 0.7.0`" in m for m in messages
        )


def test_a_document_that_uses_rules_is_written_as_0_7_and_round_trips():
    model = from_dict(_games())
    assert str(document_version(model)) == "0.7.0"
    text = dump(model)
    assert (
        "# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.7.0/" in text
    )
    assert "    rules:\n      - {home_team_id: {ne: {column: away_team_id}}}\n" in text
    again = load(text)
    assert again == model
    assert again.tables["games"].rules == RULES
    assert to_dict(again)["tables"]["games"]["rules"] == RULES


def test_an_older_document_without_rules_is_written_as_it_was():
    document = _games(rules=None)
    for version in ("0.2.0", "0.5.0", "0.6.0"):
        document["model2data"] = version
        assert str(document_version(from_dict(document))) == version
    # A reader of 0.7.0 reads a 0.7 document that uses no rules, too.
    document["model2data"] = "0.7.0"
    assert str(from_dict(document).version) == "0.7.0"


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "rule, path, message",
    [
        ({"hom_score": {"ne": 1}}, "tables.games.rules.0.hom_score", "not a column of games"),
        (
            {"home_score": {"ne": {"column": "nope"}}},
            "tables.games.rules.0.home_score.ne.column",
            "not a column of games",
        ),
        (
            {"home_score": {"eq": {"column": "played_at"}}},
            "tables.games.rules.0.home_score.eq.column",
            "only things of one kind",
        ),
        (
            {"home_score": {"lt": {"run": "as_of"}}},
            "tables.games.rules.0.home_score.lt",
            "only things of one kind",
        ),
        (
            {"home_score": {"gt": "high"}},
            "tables.games.rules.0.home_score.gt",
            "must be a number",
        ),
        (
            {"home_score": {"nonsense": 1}},
            "tables.games.rules.0.home_score.nonsense",
            "unknown operator",
        ),
        (
            {"home_score": {"in": {"column": "away_score"}}},
            "tables.games.rules.0.home_score.in",
            "takes a list of values",
        ),
        (
            {"home_score": {"ne": {"column": "away_score", "via": "x"}}},
            "tables.games.rules.0.home_score.ne",
            "not supported yet",
        ),
        (
            {"home_score": {"ne": {"column": "home_score"}}},
            "tables.games.rules.0.home_score.ne",
            "never differs from itself",
        ),
        (
            {"home_score": {"gt": 4}},
            "tables.games.rules.0.home_score.gt",
            "can never hold: home_score is generated between 0 and 4",
        ),
        (
            {"played_at": {"between": ["2026-01-01", "x"]}},
            "tables.games.rules.0.played_at.between.1",
            "ISO 8601",
        ),
    ],
)
def test_a_rule_that_cannot_hold_or_cannot_be_read_is_an_error_with_its_path(rule, path, message):
    issues = [i for i in check(_games([rule])) if i.is_error]
    assert any(i.path == path and message in i.message for i in issues), issues


def test_a_text_column_has_no_order():
    document = _games([{"name": {"gt": {"column": "name"}}}])
    document["tables"]["teams"]["rules"] = document["tables"]["games"].pop("rules")
    issues = [i for i in check(document) if i.is_error]
    assert any("compares by order" in i.message for i in issues), issues


def test_two_numeric_columns_whose_ranges_leave_no_room():
    document = _games([{"home_score": {"gt": {"column": "away_score"}}}])
    columns = document["tables"]["games"]["columns"]
    columns["home_score"]["generate"] = {"min": 0, "max": 3}
    columns["away_score"]["generate"] = {"min": 3, "max": 9}
    issues = [i for i in check(document) if i.is_error]
    assert [i.path for i in issues] == ["tables.games.rules.0.home_score.gt"]
    assert "away_score is generated between 3 and 9" in issues[0].message
    columns["away_score"]["generate"] = {"min": 2, "max": 9}
    assert [i for i in check(document) if i.is_error] == []


def test_a_rollup_cannot_hold_a_rule():
    document = _games([{"home_score": {"gte": 0}}])
    document["tables"]["games"]["columns"]["home_score"]["generate"] = {
        "rollup": {"of": "goals", "count": True}
    }
    document["tables"]["goals"] = {
        "columns": {
            "id": {"type": "bigint", "pk": True},
            "game_id": {"type": "bigint", "references": "games.id"},
        }
    }
    issues = [i for i in check(document) if i.is_error]
    assert any("is a rollup" in i.message and "rule" in i.message for i in issues), issues


def test_a_valid_rule_list_has_no_issue_and_extensions_pass():
    document = _games([{"home_score": {"gte": 0, "x-note": "free"}, "x-owner": "someone"}])
    assert [i for i in check(document) if i.is_error] == []
    assert [i for i in check(_games(rules=[])) if i.is_error] == []


# ---------------------------------------------------------------------------
# The CLI
# ---------------------------------------------------------------------------
def test_the_cli_prints_the_rules_it_could_not_meet(tmp_path):
    document = {
        "model2data": "0.7.0",
        "name": "stuck",
        "enums": {"only": ["one"]},
        "tables": {
            "t": {
                "rules": [{"a": {"ne": {"column": "b"}}}],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "a": {"type": "only", "not_null": True},
                    "b": {"type": "only", "not_null": True},
                },
            }
        },
        "run": {"rows": 5, "seed": 1, "as_of": "2026-01-01"},
    }
    path = tmp_path / "stuck.model2data.yml"
    path.write_text(dump(from_dict(document)), encoding="utf-8")
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = CliRunner().invoke(app, ["--file", str(path)])
    finally:
        os.chdir(original)
    assert result.exit_code == 0, result.output
    assert "Rules some rows still break" in result.output
    assert "t: rule 1 (a ne {column: b}): 5 row(s)" in result.output
