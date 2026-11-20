"""`derive` and date windows (spec 0.7.0): derived columns, and `min`/`max` on dates."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest

from model2data.generate import derive, windows
from model2data.generate.core import generate_data_from_dbml, get_unsatisfied_rules
from model2data.generate.days import generate_days
from model2data.model import dump, from_dict, load, to_engine
from model2data.model.document import document_version, to_dict
from model2data.model.validate import check
from model2data.parse.dbml import ColumnDef, TableDef

AS_OF = date(2026, 11, 20)
ANCHOR = datetime(2026, 11, 20)


def _season() -> dict[str, Any]:
    return {
        "model2data": "0.7.0",
        "name": "season",
        "enums": {"game_status": ["final", "scheduled"]},
        "tables": {
            "teams": {"columns": {"id": {"type": "integer", "pk": True}, "name": "text"}},
            "games": {
                "rules": [
                    {"home_team_id": {"ne": {"column": "away_team_id"}}},
                    {"home_score": {"ne": {"column": "away_score"}}},
                ],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "home_team_id": {"type": "bigint", "not_null": True, "references": "teams.id"},
                    "away_team_id": {"type": "bigint", "not_null": True, "references": "teams.id"},
                    "tip_off_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {"min": "2026-10-02", "max": "2027-04-30"},
                    },
                    "game_date": {
                        "type": "date",
                        "generate": {"derive": {"date_of": "tip_off_at"}},
                    },
                    "status": {
                        "type": "game_status",
                        "generate": {
                            "derive": {
                                "cases": [
                                    {
                                        "if": {"tip_off_at": {"lte": {"run": "as_of"}}},
                                        "then": "final",
                                    }
                                ],
                                "else": "scheduled",
                            }
                        },
                    },
                    "home_score": {
                        "type": "integer",
                        "generate": {"min": 55, "max": 125, "when": {"status": ["final"]}},
                    },
                    "away_score": {
                        "type": "integer",
                        "generate": {"min": 55, "max": 125, "when": {"status": ["final"]}},
                    },
                    "winner_team_id": {
                        "type": "bigint",
                        "references": "teams.id",
                        "generate": {
                            "when": {"status": ["final"]},
                            "derive": {
                                "cases": [
                                    {
                                        "if": {"home_score": {"gt": {"column": "away_score"}}},
                                        "then": {"column": "home_team_id"},
                                    }
                                ],
                                "else": {"column": "away_team_id"},
                            },
                        },
                    },
                },
            },
        },
    }


def _generate(document: dict, rows: int = 240, seed: int = 11, **overrides: int) -> dict:
    inputs = to_engine(from_dict(document))
    return generate_data_from_dbml(
        inputs.tables,
        inputs.refs,
        base_rows=rows,
        seed=seed,
        as_of=AS_OF,
        row_overrides={"teams": 16, **overrides},
    )


def _messages(document: dict) -> list[str]:
    return [str(i) for i in check(document) if i.is_error]


def test_a_season_holds_its_rules_and_its_derivations():
    frames = _generate(_season())
    games = frames["games"]
    assert len(games) == 240 and len(frames["teams"]) == 16
    assert (games["home_team_id"] != games["away_team_id"]).all()

    tip = pd.to_datetime(games["tip_off_at"])
    played = tip <= pd.Timestamp(ANCHOR)
    assert played.any() and (~played).any()  # some games lie after as_of
    assert (tip >= "2026-10-02").all() and (tip < "2027-05-01").all()

    assert (games.loc[played, "status"] == "final").all()
    assert (games.loc[~played, "status"] == "scheduled").all()
    assert games.loc[~played, ["home_score", "away_score", "winner_team_id"]].isna().all().all()

    final = games[played]
    assert final[["home_score", "away_score", "winner_team_id"]].notna().all().all()
    assert (final["home_score"] != final["away_score"]).all()
    home_won = final["home_score"] > final["away_score"]
    assert (final.loc[home_won, "winner_team_id"] == final.loc[home_won, "home_team_id"]).all()
    assert (final.loc[~home_won, "winner_team_id"] == final.loc[~home_won, "away_team_id"]).all()
    assert set(final["winner_team_id"]) <= set(frames["teams"]["id"])

    assert [
        d == datetime.fromisoformat(t).date()
        for d, t in zip(games["game_date"], games["tip_off_at"], strict=True)
    ] == [True] * 240
    assert get_unsatisfied_rules() == []


def test_the_same_seed_gives_the_same_frames():
    first, second = _generate(_season()), _generate(_season())
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])
    assert _generate(_season(), seed=12)["games"].equals(first["games"]) is False


# ---------------------------------------------------------------------------
# The order: rules, when and derived columns agree in the final frame
# ---------------------------------------------------------------------------
def test_a_rule_that_redraws_an_input_of_a_derivation_leaves_everything_in_step():
    document = _season()
    # Every game is on or after 1 Dec: the rule redraws tip_off_at on the rows before, and
    # the status, scores and winner of those rows follow the new date.
    document["tables"]["games"]["rules"].append({"tip_off_at": {"gte": "2026-12-01"}})
    frames = _generate(document)
    games = frames["games"]
    assert (pd.to_datetime(games["tip_off_at"]) >= "2026-12-01").all()
    assert (games["status"] == "scheduled").all()
    assert games[["home_score", "away_score", "winner_team_id"]].isna().all().all()
    assert all(
        d == datetime.fromisoformat(t).date()
        for d, t in zip(games["game_date"], games["tip_off_at"], strict=True)
    )
    assert get_unsatisfied_rules() == []


def test_a_rule_that_moves_games_into_the_past_fills_their_scores_and_winner():
    document = _season()
    document["tables"]["games"]["rules"].append({"tip_off_at": {"lte": "2026-12-15"}})
    games = _generate(document)["games"]
    tip = pd.to_datetime(games["tip_off_at"])
    assert (tip <= "2026-12-15").all()
    played = tip <= pd.Timestamp(ANCHOR)
    assert (games.loc[played, "status"] == "final").all()
    assert games.loc[played, ["home_score", "away_score", "winner_team_id"]].notna().all().all()
    assert games.loc[~played, ["home_score", "away_score", "winner_team_id"]].isna().all().all()
    assert (games.loc[played, "home_score"] != games.loc[played, "away_score"]).all()
    home_won = played & (games["home_score"] > games["away_score"])
    assert (games.loc[home_won, "winner_team_id"] == games.loc[home_won, "home_team_id"]).all()


def test_a_derived_column_may_read_another_declared_after_it():
    document = _season()
    columns = document["tables"]["games"]["columns"]
    columns["day_copy"] = {"type": "date", "generate": {"derive": {"copy": "game_date"}}}
    games = _generate(document)["games"]
    assert list(games["day_copy"]) == list(games["game_date"])
    # And one declared before the column it reads.
    document = _season()
    columns = document["tables"]["games"]["columns"]
    first = {"early_copy": {"type": "date", "generate": {"derive": {"copy": "game_date"}}}}
    document["tables"]["games"]["columns"] = {"id": columns.pop("id"), **first, **columns}
    games = _generate(document)["games"]
    assert list(games["early_copy"]) == list(games["game_date"])


def test_a_derived_column_reads_the_final_value_of_a_column_with_when():
    document = _season()
    columns = document["tables"]["games"]["columns"]
    columns["points"] = {
        "type": "integer",
        "generate": {
            "derive": {
                "cases": [{"if": {"home_score": {"gt": {"column": "away_score"}}}, "then": 3}],
                "else": 1,
            }
        },
    }
    games = _generate(document)["games"]
    scored = games["home_score"].notna()
    # A scheduled game has null scores, so no case holds: the else.
    assert (games.loc[~scored, "points"] == 1).all()
    home_won = scored & (games["home_score"] > games["away_score"])
    assert (games.loc[home_won, "points"] == 3).all()
    assert (games.loc[scored & ~home_won, "points"] == 1).all()


def test_a_rollup_over_a_derived_column_adds_up_its_final_values():
    document = _season()
    games = document["tables"]["games"]["columns"]
    games["points"] = {
        "type": "integer",
        "generate": {
            "derive": {
                "cases": [{"if": {"status": ["final"]}, "then": 3}],
                "else": 0,
            }
        },
    }
    games["home_team_id"]["generate"] = {"at_least": 1}
    document["tables"]["teams"]["columns"]["home_points"] = {
        "type": "integer",
        "generate": {"rollup": {"of": "games", "via": "home_team_id", "sum": "points"}},
    }
    frames = _generate(document, rows=100)
    by_team = frames["games"].groupby("home_team_id")["points"].sum()
    teams = frames["teams"].set_index("id")["home_points"]
    assert (teams == by_team.reindex(teams.index).fillna(0)).all()


def test_a_derivation_reading_a_rollup_is_an_error():
    document = _season()
    document["tables"]["teams"]["columns"]["n_games"] = {
        "type": "integer",
        "generate": {"rollup": {"of": "games", "via": "home_team_id", "count": True}},
    }
    document["tables"]["teams"]["columns"]["flag"] = {
        "type": "integer",
        "generate": {"derive": {"copy": "n_games"}},
    }
    messages = _messages(document)
    assert any("flag" in m and "rollup" in m for m in messages), messages


# ---------------------------------------------------------------------------
# The document: checks, version, round trip
# ---------------------------------------------------------------------------
def _columns(document: dict) -> dict:
    return document["tables"]["games"]["columns"]


def _changed(change) -> list[str]:
    document = _season()
    change(document)
    return _messages(document)


def _has(messages: list[str], path: str, text: str) -> bool:
    return any(path in m and text in m for m in messages)


def test_a_key_cannot_be_derived_and_a_value_hint_cannot_sit_beside_derive():
    messages = _changed(lambda d: _columns(d)["id"].update(generate={"derive": {"copy": "id"}}))
    assert _has(messages, "columns.id.generate.derive", "cannot sit on a column that is in the")
    for hint, value in (("min", "2026-01-01"), ("weights", {"final": 2}), ("null_rate", 0.1)):
        messages = _changed(
            lambda d, h=hint, v=value: _columns(d)["status"]["generate"].update({h: v})
        )
        assert _has(messages, f"status.generate.{hint}", "`derive` decides its value"), messages


def test_a_derivation_cycle_is_an_error_even_through_a_when():
    def loop(document):
        _columns(document)["a"] = {"type": "date", "generate": {"derive": {"copy": "b"}}}
        _columns(document)["b"] = {"type": "date", "generate": {"derive": {"copy": "a"}}}

    assert _has(_changed(loop), "generate.derive", "read each other in a loop: a -> b -> a")

    def through_when(document):
        # status reads winner_team_id, which has `when` on status.
        _columns(document)["status"]["generate"]["derive"]["cases"][0]["if"] = {
            "winner_team_id": {"is_null": True}
        }

    assert _has(_changed(through_when), "columns.status.generate.derive", "in a loop")


def test_a_value_that_does_not_fit_the_column_is_an_error():
    def set_else(value):
        return lambda d: _columns(d)["status"]["generate"]["derive"].update({"else": value})

    assert _has(_changed(set_else("playing")), "derive.else", "not a member of game_status")

    def derive(column, spec):
        return lambda d: _columns(d)[column]["generate"].update(derive=spec)

    messages = _changed(derive("game_date", {"copy": "home_score"}))
    assert _has(messages, "game_date.generate.derive.copy", "cannot be its value")
    messages = _changed(derive("game_date", {"date_of": "home_score"}))
    assert _has(messages, "derive.date_of", "takes a timestamp column")
    messages = _changed(
        lambda d: _columns(d).update(
            x={"type": "text", "generate": {"derive": {"date_of": "tip_off_at"}}}
        )
    )
    assert _has(messages, "columns.x.generate.derive.date_of", "goes into a date column")
    messages = _changed(derive("game_date", {"date_of": "nope"}))
    assert _has(messages, "derive.date_of", 'names "nope", which is not a column of games')


def test_a_derived_foreign_key_comes_from_a_foreign_key_onto_the_same_parent():
    def take(value):
        return lambda d: _columns(d)["winner_team_id"]["generate"]["derive"].update({"else": value})

    messages = _changed(take({"column": "home_score"}))
    assert _has(messages, "derive.else.column", "foreign key onto teams.id")
    assert _has(_changed(take(3)), "derive.else", "is a foreign key")
    assert _changed(lambda d: None) == []


def test_nothing_follows_a_derived_column_and_a_rule_cannot_read_only_derived_ones():
    messages = _changed(
        lambda d: _columns(d).update(z={"type": "timestamp", "generate": {"after": "game_date"}})
    )
    assert _has(messages, "columns.z.generate.after", "which is derived")
    messages = _changed(
        lambda d: d["tables"]["games"]["rules"].append({"game_date": {"gte": "2026-10-01"}})
    )
    assert _has(messages, "rules.2.game_date", "reads only derived columns")


def test_derive_and_date_windows_need_a_0_7_document():
    messages = _changed(lambda d: d.update(model2data="0.6.0"))
    assert _has(messages, "game_date.generate.derive", "`derive` is spec 0.7.0")
    assert _has(messages, "tip_off_at.generate.min", "is spec 0.7.0")
    assert _has(messages, "tip_off_at.generate.max", "is spec 0.7.0")


def test_a_document_that_uses_them_is_written_as_0_7_and_round_trips():
    model = from_dict(_season())
    assert str(document_version(model)) == "0.7.0"
    again = load(dump(model))
    assert again == model
    status = to_dict(again)["tables"]["games"]["columns"]["status"]
    assert status["generate"]["derive"]["else"] == "scheduled"
    assert again.tables["games"].columns["tip_off_at"].generate["min"] == "2026-10-02"


def test_a_model_without_them_is_untouched_by_the_new_vocabulary():
    document = _season()
    for column in ("game_date", "status", "winner_team_id", "home_score", "away_score"):
        document["tables"]["games"]["columns"][column] = "text"
    document["tables"]["games"]["columns"]["tip_off_at"]["generate"] = {}
    document["model2data"] = "0.6.0"
    document["tables"]["games"].pop("rules")
    assert _messages(document) == []
    assert str(document_version(from_dict(document))) == "0.6.0"


def test_days_refuses_a_derived_or_windowed_table_that_moves_on():
    def refused(change, pattern):
        document = _season()
        document["tables"]["games"].pop("rules")
        document["tables"]["games"]["incremental"] = {"new_per_day": 3}
        change(document)
        with pytest.raises(ValueError, match=pattern):
            generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)

    refused(lambda d: None, '"min"/"max"')
    refused(lambda d: _columns(d)["tip_off_at"].update(generate={}), '"derive"')

    def only_window(document):
        for name in ("game_date", "status", "winner_team_id", "home_score", "away_score"):
            _columns(document).pop(name)

    refused(only_window, '"min"/"max"')
    document = _season()
    document["tables"]["games"].pop("rules")
    generate_days(from_dict(document), 1, base_rows=20, seed=1, as_of=AS_OF)  # no table moves


# ---------------------------------------------------------------------------
# Date windows
# ---------------------------------------------------------------------------
def _dated(generate: dict, kind: str = "timestamp", rows: int = 400, **run: Any) -> pd.Series:
    document = {
        "model2data": "0.7.0",
        "tables": {
            "t": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "at": {"type": kind, "not_null": True, "generate": generate},
                }
            }
        },
    }
    inputs = to_engine(from_dict(document))
    frames = generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=rows, seed=5, as_of=AS_OF, **run
    )
    return pd.to_datetime(frames["t"]["at"])


def test_a_window_may_lie_after_as_of_and_holds_both_ends():
    at = _dated({"min": "2026-12-01", "max": "2027-01-31"})
    assert at.min() >= pd.Timestamp("2026-12-01") and at.max() < pd.Timestamp("2027-02-01")
    assert (at > pd.Timestamp(ANCHOR)).all()
    # A date-only max on a timestamp is the whole of that day.
    on_the_last = _dated({"min": "2027-01-30", "max": "2027-01-31"}, rows=600)
    assert (on_the_last.dt.date == date(2027, 1, 31)).any()
    # A date column holds days of the window, the ends included.
    days = _dated({"min": "2027-01-01", "max": "2027-01-03"}, kind="date", rows=300)
    assert set(days.dt.date) == {date(2027, 1, d) for d in (1, 2, 3)}


def test_one_bound_alone_keeps_the_default_for_the_other():
    before = _dated({"min": "2026-06-01"}, rows=600)
    assert before.min() >= pd.Timestamp("2026-06-01") and before.max() <= pd.Timestamp(ANCHOR)
    after = _dated({"min": "2027-03-01"})  # on or after as_of: the default length from min
    assert after.min() >= pd.Timestamp("2027-03-01")
    assert after.max() <= pd.Timestamp("2028-03-01")
    ahead = _dated({"max": "2027-02-01"}, rows=600)
    assert ahead.max() <= pd.Timestamp("2027-02-02") and (ahead > pd.Timestamp(ANCHOR)).any()
    assert ahead.min() >= pd.Timestamp("2025-11-20")
    old = _dated({"max": "2020-01-01"}, kind="date")
    assert old.max() <= pd.Timestamp("2020-01-01") and old.min() >= pd.Timestamp("2018-01-01")


def test_growth_seasonality_and_business_hours_shape_the_draw_inside_the_window():
    from model2data.generate.options import TimeProfile

    window = {"min": "2026-12-01", "max": "2027-03-31"}
    flat = _dated(window, rows=2000)
    grown = _dated({**window, "growth": 4.0}, rows=2000)
    assert (grown.mean() - flat.mean()).days > 10  # later in the window
    assert grown.min() >= pd.Timestamp("2026-12-01") and grown.max() < pd.Timestamp("2027-04-01")
    hours = _dated({**window, "business_hours": True}, rows=2000)
    night = ((hours.dt.hour < 6) | (hours.dt.hour >= 21)).mean()
    assert night < 0.1 and ((flat.dt.hour < 6) | (flat.dt.hour >= 21)).mean() > 0.3
    assert hours.dt.dayofweek.ge(5).mean() < 0.15
    # The run's own profile applies too, and growth runs across the window's span.
    profile = _dated(window, rows=2000, time_profile=TimeProfile(growth=4.0))
    assert (profile.mean() - flat.mean()).days > 10


def test_a_window_and_after_agree_and_a_follower_of_a_future_date_lands_after_it():
    document = _season()
    document["tables"]["games"].pop("rules")
    columns = _columns(document)
    columns["settled_at"] = {
        "type": "timestamp",
        "generate": {"after": "tip_off_at", "max": "2027-05-15"},
    }
    columns["reviewed_at"] = {"type": "timestamp", "generate": {"after": "tip_off_at"}}
    games = _generate(document)["games"]
    tip = pd.to_datetime(games["tip_off_at"])
    for name in ("settled_at", "reviewed_at"):
        later = pd.to_datetime(games[name])
        assert (later[later.notna()] >= tip[later.notna()]).all()
    settled = pd.to_datetime(games["settled_at"]).dropna()
    assert settled.max() < pd.Timestamp("2027-05-16")
    assert (settled > pd.Timestamp(ANCHOR)).any()


def test_a_child_follows_a_parent_date_that_lies_after_as_of():
    document = {
        "model2data": "0.7.0",
        "tables": {
            "games": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "tip_off_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {"min": "2026-12-01", "max": "2027-02-01"},
                    },
                }
            },
            "tickets": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "game_id": {"type": "bigint", "not_null": True, "references": "games.id"},
                    "sold_at": {
                        "type": "timestamp",
                        "not_null": True,
                        "generate": {
                            "min": "2026-11-25",
                            "max": "2027-02-05",
                            "after": "games.tip_off_at",
                        },
                    },
                }
            },
        },
    }
    inputs = to_engine(from_dict(document))
    frames = generate_data_from_dbml(
        inputs.tables, inputs.refs, base_rows=200, seed=3, as_of=AS_OF, row_overrides={"games": 30}
    )
    merged = frames["tickets"].merge(frames["games"], left_on="game_id", right_on="id")
    assert (pd.to_datetime(merged["sold_at"]) >= pd.to_datetime(merged["tip_off_at"])).all()


def test_a_window_that_makes_an_after_impossible_is_an_error():
    def settle(max_value):
        def change(document):
            _columns(document)["settled_at"] = {
                "type": "timestamp",
                "generate": {"max": max_value, "after": "tip_off_at"},
            }

        return change

    messages = _changed(settle("2026-09-01"))
    assert _has(messages, "settled_at.generate.after", "no later than 2026-09-01")
    assert _changed(settle("2026-10-02")) == []

    # With the run's as_of known, a default start counts too.
    def with_run(document):
        settle("2025-01-01")(document)
        _columns(document)["tip_off_at"]["generate"] = {}
        document["run"] = {"as_of": "2026-11-20"}

    assert _has(_changed(with_run), "settled_at.generate.after", "cannot be after it")


def test_the_windowed_model_generates_the_same_bytes_twice_and_other_seeds_differ():
    one = _dated({"min": "2026-12-01", "max": "2027-03-31"}, rows=50)
    two = _dated({"min": "2026-12-01", "max": "2027-03-31"}, rows=50)
    assert one.equals(two)


# ---------------------------------------------------------------------------
# What a derivation may be, checked one by one
# ---------------------------------------------------------------------------
def _derived(name: str, kind: str, derive: Any, **column: Any):
    def change(document):
        _columns(document)[name] = {"type": kind, "generate": {"derive": derive}, **column}

    return change


def test_an_enum_goes_only_into_a_column_of_its_own_type_or_into_text():
    def another_enum(document):
        document["enums"]["tier"] = ["gold"]
        _columns(document)["tier"] = {"type": "tier"}

    def copy_into(name, kind, source):
        def change(document):
            another_enum(document)
            _derived(name, kind, {"copy": source})(document)

        return change

    messages = _changed(copy_into("x", "tier", "status"))
    assert _has(messages, "columns.x.generate.derive.copy", "an enum goes only into a column")
    assert _changed(copy_into("x", "text", "status")) == []
    assert _changed(copy_into("x", "decimal(10, 2)", "home_score")) == []  # whole into decimal
    messages = _changed(copy_into("x", "date", "tip_off_at"))
    assert _has(messages, "columns.x.generate.derive.copy", "`date_of` takes its date")


def test_a_derive_that_is_not_the_shape_the_schema_wants_does_not_stop_the_other_checks():
    messages = _changed(
        lambda d: _columns(d)["status"]["generate"]["derive"].update(
            cases=["nope", {"if": {"tip_off_at": {"lte": {"run": "as_of"}}}, "then": "final"}]
        )
    )
    assert messages and all("games.columns.status" in m for m in messages)
    messages = _changed(lambda d: _columns(d)["status"]["generate"].update(derive="final"))
    assert messages and all("status" in m for m in messages)


def test_else_belongs_to_cases_and_a_derivation_cannot_name_itself():
    messages = _changed(_derived("x", "integer", {"copy": "home_score", "else": 1}))
    assert _has(messages, "columns.x.generate.derive.else", "belongs to `cases`")
    messages = _changed(_derived("x", "integer", {"copy": "x"}))
    assert _has(messages, "columns.x.generate.derive.copy", "cannot be derived from itself")
    messages = _changed(_derived("x", "integer", {"copy": 5}))
    assert messages and not _has(messages, "columns.x", "cannot be derived from itself")
    messages = _changed(_derived("x", "date", {"date_of": 5}))
    assert messages and not _has(messages, "columns.x", "takes a timestamp column")


def test_a_then_may_be_the_run_anchor_and_nothing_else_of_the_run():
    def then(value, kind="timestamp", **column):
        return _derived(
            "x", kind, {"cases": [{"if": {"home_score": {"gt": 1}}, "then": value}]}, **column
        )

    assert _changed(then({"run": "as_of"})) == []
    assert _has(_changed(then({"run": "now"})), "cases.0.then", "is not a value of the run")
    messages = _changed(then({"run": "as_of"}, kind="integer"))
    assert _has(messages, "cases.0.then", "{run: as_of} is a timestamp")
    messages = _changed(then(None, not_null=True))
    assert _has(messages, "cases.0.then", "is null, and x is not_null")
    assert _changed(then(None)) == []
    assert _changed(then({"a": 1, "b": 2}))  # a mapping that is no operand is the schema's


@pytest.mark.parametrize(
    ("window", "text"),
    [
        ({"min": 5}, "not the number 5"),
        ({"min": [1]}, None),
        ({"max": "next week"}, "is not an ISO 8601 date or timestamp"),
        ({"min": "2027-01-01", "max": "2026-01-01"}, "is after max"),
    ],
)
def test_a_window_that_is_not_two_dates_in_order_is_an_error(window, text):
    messages = _changed(lambda d: _columns(d)["tip_off_at"].update(generate=window))
    if text is None:
        assert messages  # the schema refuses it
    else:
        assert _has(messages, "tip_off_at.generate", text), messages


def test_a_rule_that_reads_a_derived_column_and_an_extension_is_still_read():
    def rule(document):
        document["tables"]["games"]["rules"].append(
            {"game_date": {"gte": "2026-10-01"}, "nope": {"eq": 1}, "x-owner": "someone"}
        )

    messages = _changed(rule)
    assert _has(messages, "rules.2.game_date", "reads only derived columns")
    assert _has(messages, "rules.2.nope", "not a column of games")


# ---------------------------------------------------------------------------
# The engine reads a table of its own, whatever the validation says
# ---------------------------------------------------------------------------
def _engine(*columns: ColumnDef, **table: Any) -> dict[str, TableDef]:
    return {"t": TableDef(name="t", columns=[ColumnDef("id", "int", {"pk"}), *columns], **table)}


def _refused(tables: dict[str, TableDef], pattern: str) -> None:
    with pytest.raises(ValueError, match=pattern):
        generate_data_from_dbml(tables, [], base_rows=5, seed=1, as_of=AS_OF)


def _note(derive: Any, kind: str = "int", settings: set | None = None, **more: Any) -> ColumnDef:
    return ColumnDef("c", kind, settings or set(), note={"derive": derive, **more})


@pytest.mark.parametrize(
    ("column", "pattern"),
    [
        (_note("a"), "must be an object"),
        (_note({}), "needs exactly one of"),
        (_note({"copy": "a", "date_of": "a"}), "needs exactly one of"),
        (_note({"copy": "a"}, settings={"unique"}), "cannot sit on a primary key"),
        (_note({"copy": "a"}, min=1), 'cannot also have "min"'),
        (_note({"cases": []}), "non-empty list"),
        (_note({"cases": [{"if": [], "then": 1}]}), "entries need an `if`"),
        (_note({"cases": [{"if": {"a": 1}}]}), "entries need an `if`"),
        (_note({"cases": [{"if": {"a": {"gt": [1]}}, "then": 1}]}), '"derive" `if`:'),
        (_note({"copy": 3}), "must name a column"),
        (_note({"copy": "nope"}), "which is not a column of t"),
        (_note({"copy": "c"}), "reads the column itself"),
    ],
)
def test_the_engine_refuses_a_malformed_derive(column, pattern):
    _refused(_engine(ColumnDef("a", "int"), column), pattern)


def test_the_engine_refuses_derived_columns_that_read_each_other():
    columns = (
        ColumnDef("a", "int", note={"derive": {"copy": "b"}}),
        ColumnDef("b", "int", note={"derive": {"copy": "a"}}),
    )
    _refused(_engine(*columns), "reads itself through other derived columns: a -> b -> a")


def test_a_derivation_that_is_not_readable_reads_nothing():
    assert derive.derivation_reads({"cases": [1]}) == []
    assert derive.derivation_reads({"copy": "a"}) == ["a"]
    assert repr(derive.NOTHING) == "<null>"


def test_a_value_is_stored_as_the_column_it_goes_into_stores_it():
    as_target = derive._as_target
    assert as_target(None, "date") is None and as_target(float("nan"), "text") is None
    assert as_target(datetime(2026, 1, 2, 3, 4, 5), "timestamp") == "2026-01-02 03:04:05"
    assert as_target(date(2026, 1, 2), "timestamp") == "2026-01-02 00:00:00"
    assert as_target("2026-01-02", "timestamp") == "2026-01-02 00:00:00"
    assert as_target("2026-01-02 03:04:05", "timestamp") == "2026-01-02 03:04:05"
    assert as_target(7, "timestamp") == 7
    assert as_target(datetime(2026, 1, 2, 3), "date") == date(2026, 1, 2)
    assert as_target(date(2026, 1, 2), "date") == date(2026, 1, 2)
    assert as_target("2026-01-02", "date") == date(2026, 1, 2)
    assert as_target(7, "date") == 7
    assert as_target(5, "enum") == "5"


def test_the_run_anchor_is_the_only_value_of_the_run():
    assert derive._run_value("as_of", ANCHOR) == ANCHOR
    with pytest.raises(ValueError, match="is not a value of the run: only `as_of` is"):
        derive._run_value("now", ANCHOR)
    frame = pd.DataFrame({"a": [1, 2, 3]})
    rows = pd.Series([True, False, True]).to_numpy()
    stored = derive._values(frame, {"run": "as_of"}, rows, "timestamp", ANCHOR)
    assert stored == ["2026-11-20 00:00:00"] * 2


def test_a_table_with_nothing_derived_is_left_as_it_is():
    frame = pd.DataFrame({"a": [1, 2]})
    table = TableDef(name="t", columns=[ColumnDef("a", "int")])
    derive.settle(
        frame, table, run_anchor=ANCHOR, cap=ANCHOR, seed_for=lambda name: None, as_of=AS_OF
    )
    assert frame["a"].tolist() == [1, 2]


def test_a_date_default_window_on_the_leap_day_goes_back_to_the_28th():
    start, end = windows.default_window("date", date(2028, 2, 29))
    assert (start, end) == (datetime(2026, 2, 28), datetime(2028, 2, 29))
    start, _ = windows.default_window("date", date(2026, 3, 1))
    assert start == datetime(2024, 3, 1)


@pytest.mark.parametrize(
    ("note", "pattern"),
    [
        ({"min": "5"}, r't\.c: "min" must be a number on a numeric column'),
        ({"max": "high"}, r't\.c: "max" must be a number on a numeric column'),
    ],
)
def test_a_numeric_bound_written_as_text_is_refused_by_the_engine(note, pattern):
    _refused(_engine(ColumnDef("c", "int", note=note)), pattern)


@pytest.mark.parametrize(
    ("note", "pattern"),
    [
        ({"min": "soon"}, r't\.c: "min" must be an ISO 8601 date or timestamp string on a date'),
        ({"min": "2027-01-01", "max": "2026-01-01"}, r'"min" .* is after "max"'),
    ],
)
def test_a_window_that_is_not_two_dates_in_order_is_refused_by_the_engine(note, pattern):
    _refused(_engine(ColumnDef("c", "date", note=note)), pattern)


def test_a_derived_foreign_key_on_a_broken_cycle_edge_is_left_to_its_derivation():
    document = {
        "model2data": "0.7.0",
        "name": "loop",
        "tables": {
            "a": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "b_id": {"type": "bigint", "references": "b.id"},
                    "b2_id": {
                        "type": "bigint",
                        "references": "b.id",
                        "generate": {"derive": {"copy": "b_id"}},
                    },
                }
            },
            "b": {
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "a_id": {"type": "bigint", "not_null": True, "references": "a.id"},
                }
            },
        },
    }
    inputs = to_engine(from_dict(document))
    frames = generate_data_from_dbml(inputs.tables, inputs.refs, base_rows=6, seed=1, as_of=AS_OF)
    assert len(frames["a"]) == 6 and frames["a"]["b_id"].notna().any()
