"""The sports league example: a worked model, metrics file and dashboard, and what it guarantees."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import jsonschema
import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

from model2data.cli import app
from model2data.generate.core import (
    generate_data_from_dbml,
    get_capped_tables,
    get_unsatisfied_rules,
)
from model2data.metrics import known_values, load, resolve
from model2data.model import load as load_model
from model2data.model import to_engine

EXAMPLES = Path(__file__).resolve().parent.parent / "model2data" / "spec" / "examples"
MODEL = EXAMPLES / "sports_league.model2data.yml"
METRICS = EXAMPLES / "sports_league.metrics.yml"
DASHBOARD = EXAMPLES / "sports_league_standings.dashboard.yml"
SPEC = EXAMPLES.parent


@pytest.fixture(scope="module")
def league():
    model = load_model(MODEL)
    inputs = to_engine(model)
    run = model.run
    frames = generate_data_from_dbml(
        tables=inputs.tables,
        refs=inputs.refs,
        base_rows=run.rows or 100,
        seed=run.seed,
        row_overrides=run.rows_per_table,
        locale=run.locale,
        as_of=date.fromisoformat(str(run.as_of)),
    )
    return model, run, frames


def test_every_club_plays_every_other_club_home_and_away(league):
    _, _, frames = league
    clubs, games = frames["clubs"], frames["games"]
    assert len(clubs) == 12
    assert len(games) == 12 * 11
    assert not (games["home_team_id"] == games["away_team_id"]).any()
    pairs = set(zip(games["home_team_id"], games["away_team_id"], strict=True))
    assert len(pairs) == len(games)
    assert get_unsatisfied_rules() == []
    assert get_capped_tables() == []  # the 12 rows were asked for, not capped


def test_scores_only_on_played_games(league):
    _, run, frames = league
    games = frames["games"]
    played = games["status"] == "final"
    assert played.any() and (~played).any()
    # A game is played exactly when its tip-off has reached the run's day.
    tip_off = pd.to_datetime(games["tip_off_at"])
    assert (played == (tip_off <= pd.Timestamp(str(run.as_of)))).all()
    # Nothing after the run's day has a score, a winner or a posted result.
    future = games[tip_off > pd.Timestamp(str(run.as_of))]
    assert len(future) > 0
    for column in ("home_score", "away_score", "winner_team_id", "result_posted_at"):
        assert future[column].isna().all()
    assert games.loc[played, ["home_score", "away_score"]].notna().all().all()
    final = games[played]
    assert (pd.to_datetime(final["result_posted_at"]) >= pd.to_datetime(final["tip_off_at"])).all()
    assert (games["game_date"].astype(str) == tip_off.dt.strftime("%Y-%m-%d")).all()


def test_the_winner_is_the_side_with_more_goals_and_a_draw_has_none(league):
    _, _, frames = league
    final = frames["games"].query("status == 'final'")
    home_won = final["home_score"] > final["away_score"]
    away_won = final["away_score"] > final["home_score"]
    assert (final.loc[home_won, "winner_team_id"] == final.loc[home_won, "home_team_id"]).all()
    assert (final.loc[away_won, "winner_team_id"] == final.loc[away_won, "away_team_id"]).all()
    drawn = ~(home_won | away_won)
    assert drawn.any()
    assert final.loc[drawn, "winner_team_id"].isna().all()


def test_goals_are_poisson_counts():
    # Over many seasons the means hold: 1.5 at home, 1.2 away, about a draw in four.
    model = load_model(MODEL)
    inputs = to_engine(model)
    goals = []
    for seed in range(12):
        frames = generate_data_from_dbml(
            tables=inputs.tables,
            refs=inputs.refs,
            seed=seed,
            row_overrides=model.run.rows_per_table,
            as_of=date(2027, 6, 1),  # the whole season played
        )
        goals.append(frames["games"][["home_score", "away_score"]])
    scores = pd.concat(goals)
    assert scores.notna().all().all()
    assert scores["home_score"].mean() == pytest.approx(1.5, abs=0.1)
    assert scores["away_score"].mean() == pytest.approx(1.2, abs=0.1)
    assert ((scores["home_score"] + scores["away_score"]).mean()) == pytest.approx(2.7, abs=0.15)
    draws = (scores["home_score"] == scores["away_score"]).mean()
    assert 0.2 < draws < 0.32
    assert scores.max().max() <= 9 and scores.min().min() >= 0


def test_every_club_has_a_squad(league):
    _, _, frames = league
    squads = frames["players"].groupby("club_id").size()
    assert set(squads.index) == set(frames["clubs"]["id"])
    assert (squads >= 18).all()
    assert (squads <= 25).all()
    assert squads.min() < 25  # a range, not every club at the cap
    assert frames["players"]["first_name"].notna().all()


def test_the_known_home_win_rate_is_the_ratio_in_the_frame(league):
    model, run, frames = league
    semantic = resolve(model, load(METRICS, model))
    values = known_values(semantic, frames, seed=run.seed, as_of=str(run.as_of)).values
    final = frames["games"].query("status == 'final'")
    home_wins = int((final["winner_team_id"] == final["home_team_id"]).sum())
    assert values["games_played"].value == len(final)
    assert values["home_wins"].value == home_wins
    assert float(values["home_win_rate"].value) == pytest.approx(home_wins / len(final), abs=1e-3)
    points = final["home_score"].sum() + final["away_score"].sum()
    assert float(values["points_per_game"].value) == pytest.approx(points / len(final), abs=1e-3)


@pytest.mark.parametrize(
    ("example", "schema"),
    [
        (METRICS, SPEC / "metrics" / "metrics.schema.json"),
        (DASHBOARD, SPEC / "dashboards" / "dashboard.schema.json"),
    ],
    ids=lambda path: path.name,
)
def test_the_metrics_and_dashboard_validate_against_their_schemas(example, schema):
    document = yaml.safe_load(example.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(yaml.safe_load(schema.read_text(encoding="utf-8"))).validate(
        document
    )
    assert document["model2data-metrics" if "metrics" in example.name else "model2data-dashboard"]


def test_the_dashboard_checks_clean():
    result = CliRunner().invoke(app, ["dashboard", "check", str(DASHBOARD)])
    assert result.exit_code == 0, result.output
    assert "0 errors, 0 warnings, checked against sports_league.model2data.yml" in result.output
