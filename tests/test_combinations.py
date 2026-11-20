"""`combinations` (spec 0.7.0): a table with a row for every combination of its parents' rows."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest

from model2data.dbt.tests import dbt_tests
from model2data.generate.core import generate_data_from_dbml, get_unsatisfied_rules
from model2data.generate.days import generate_days
from model2data.model import dump, from_dict, load, to_engine
from model2data.model.document import document_version, to_dict
from model2data.model.validate import check

AS_OF = date(2026, 11, 20)
CLUBS = [
    "Antwerp Giants",
    "Spirou Charleroi",
    "Brussels",
    "Okapi Aalst",
    "Limburg United",
    "Coretec Oostende",
    "Kortrijk",
    "CB Liège",
    "CityX Mons",
    "Leuven Bears",
    "Kangoeroes Mechelen",
    "Zwolle",
    "Heroes Den Bosch",
    "Donar Groningen",
    "Leiden",
    "Rotterdam",
]
DUTCH = CLUBS[-5:]


def _league() -> dict[str, Any]:
    return {
        "model2data": "0.7.0",
        "name": "league",
        "enums": {
            "club": CLUBS,
            "country_code": ["BE", "NL"],
            "position": ["PG", "SG", "SF", "PF", "C"],
            "game_status": ["final", "scheduled"],
        },
        "tables": {
            "teams": {
                "columns": {
                    "id": {"type": "integer", "pk": True},
                    "name": {"type": "club", "unique": True, "not_null": True},
                    "country": {
                        "type": "country_code",
                        "generate": {
                            "derive": {
                                "cases": [{"if": {"name": {"in": DUTCH}}, "then": "NL"}],
                                "else": "BE",
                            }
                        },
                    },
                }
            },
            "players": {
                "columns": {
                    "id": {"type": "integer", "pk": True},
                    "team_id": {
                        "type": "integer",
                        "not_null": True,
                        "references": "teams.id",
                        "generate": {"at_least": 12},
                    },
                    "first_name": "text",
                    "last_name": "text",
                    "position": {"type": "position", "not_null": True},
                    "height_cm": {"type": "integer", "generate": {"min": 178, "max": 218}},
                }
            },
            "games": {
                "combinations": ["home_team_id", "away_team_id"],
                "rules": [
                    {"home_team_id": {"ne": {"column": "away_team_id"}}},
                    {"home_score": {"ne": {"column": "away_score"}}},
                ],
                "columns": {
                    "id": {"type": "bigint", "pk": True},
                    "home_team_id": {"type": "integer", "not_null": True, "references": "teams.id"},
                    "away_team_id": {"type": "integer", "not_null": True, "references": "teams.id"},
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
                        "type": "integer",
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
        "run": {
            "seed": 1,
            "as_of": "2026-11-20",
            "locale": "nl_BE",
            "rows_per_table": {"teams": 16, "players": 224},
        },
    }


def _generate(document: dict, **overrides: Any) -> dict[str, pd.DataFrame]:
    model = from_dict(document)
    inputs = to_engine(model)
    run = model.run
    return generate_data_from_dbml(
        inputs.tables,
        inputs.refs,
        base_rows=run.rows or 100,
        seed=run.seed,
        row_overrides={**(run.rows_per_table or {}), **overrides},
        locale=run.locale,
        as_of=date.fromisoformat(run.as_of) if run.as_of else None,
    )


def _errors(document: dict) -> list[str]:
    return [str(issue) for issue in check(document) if issue.is_error]


def test_the_reference_league_is_valid():
    assert _errors(_league()) == []


def test_every_team_plays_every_other_team_home_and_away():
    frames = _generate(_league())
    games, teams = frames["games"], frames["teams"]
    assert len(games) == 16 * 16 - 16 == 240

    pairs = list(zip(games["home_team_id"], games["away_team_id"], strict=True))
    assert len(set(pairs)) == 240
    assert all(home != away for home, away in pairs)
    ids = set(teams["id"])
    assert set(pairs) == {(a, b) for a in ids for b in ids if a != b}
    # Parent row order, the first column outermost.
    assert games["home_team_id"].tolist() == sorted(
        games["home_team_id"].tolist(), key=teams["id"].tolist().index
    )
    assert pairs == sorted(
        pairs, key=lambda p: (teams["id"].tolist().index(p[0]), teams["id"].tolist().index(p[1]))
    )

    assert (games["home_score"].dropna() != games["away_score"].dropna()).all()
    assert games["id"].is_unique


def test_scores_winners_and_dates_follow_the_derivations():
    games = _generate(_league())["games"]
    tip = pd.to_datetime(games["tip_off_at"])
    played = tip < pd.Timestamp(2026, 11, 20, 0, 0)
    after = tip >= pd.Timestamp(2026, 11, 20, 0, 0)
    assert played.any() and after.any()
    assert (games.loc[played, "status"] == "final").all()
    assert games.loc[played, ["home_score", "away_score", "winner_team_id"]].notna().all().all()
    # Anything on or after the anchor midnight has no result and is scheduled.
    assert games.loc[after & (tip > pd.Timestamp(2026, 11, 20)), "status"].eq("scheduled").all()
    assert (
        games.loc[(games["status"] == "scheduled"), ["home_score", "away_score", "winner_team_id"]]
        .isna()
        .all()
        .all()
    )

    final = games[games["status"] == "final"]
    assert (final["home_score"] != final["away_score"]).all()
    home_won = final["home_score"] > final["away_score"]
    assert (final.loc[home_won, "winner_team_id"] == final.loc[home_won, "home_team_id"]).all()
    assert (final.loc[~home_won, "winner_team_id"] == final.loc[~home_won, "away_team_id"]).all()
    assert [
        d == datetime.fromisoformat(str(t)).date()
        for d, t in zip(games["game_date"], games["tip_off_at"], strict=True)
    ] == [True] * 240
    assert get_unsatisfied_rules() == []


def test_the_teams_players_and_countries_of_the_league():
    frames = _generate(_league())
    teams, players = frames["teams"], frames["players"]
    assert sorted(teams["name"]) == sorted(CLUBS)
    assert teams["name"].is_unique
    for name, country in zip(teams["name"], teams["country"], strict=True):
        assert country == ("NL" if name in DUTCH else "BE")
    assert len(players) == 224
    assert (players.groupby("team_id").size() >= 12).all()
    assert set(players["team_id"]) == set(teams["id"])


def test_the_same_seed_gives_the_same_frames_and_another_seed_does_not():
    first, second = _generate(_league()), _generate(_league())
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])
    other = _league()
    other["run"]["seed"] = 2
    assert not _generate(other)["games"].equals(first["games"])
    # The combinations do not depend on the seed, the rest of the rows do.
    assert _generate(other)["games"][["home_team_id", "away_team_id"]].equals(
        first["games"][["home_team_id", "away_team_id"]]
    )


# ---------------------------------------------------------------------------
# Semantics
# ---------------------------------------------------------------------------
def _stores() -> dict[str, Any]:
    return {
        "model2data": "0.7.0",
        "name": "stock",
        "tables": {
            "stores": {"columns": {"id": {"type": "integer", "pk": True}, "city": "city"}},
            "products": {"columns": {"id": {"type": "integer", "pk": True}, "name": "text"}},
            "stock": {
                "combinations": ["store_id", "product_id"],
                "columns": {
                    "id": {"type": "integer", "pk": True},
                    "store_id": {"type": "integer", "references": "stores.id"},
                    "product_id": {"type": "integer", "references": "products.id"},
                    "on_hand": {"type": "integer", "generate": {"min": 0, "max": 50}},
                },
            },
        },
        "run": {"seed": 3, "rows": 5, "rows_per_table": {"products": 4}},
    }


def test_two_different_parents_make_their_product_and_global_rows_do_not_apply():
    document = _stores()
    assert _errors(document) == []
    frames = _generate(document)
    stock = frames["stock"]
    assert len(stock) == 5 * 4  # `run.rows` does not apply to a combination table
    assert stock["store_id"].tolist() == [i for i in frames["stores"]["id"] for _ in range(4)]
    assert stock["product_id"].tolist() == frames["products"]["id"].tolist() * 5
    assert stock["on_hand"].between(0, 50).all()


def test_a_rule_on_other_columns_never_redraws_a_combination_column():
    document = _stores()
    document["tables"]["stock"]["rules"] = [
        {"store_id": {"ne": {"column": "on_hand"}}},
        {"on_hand": {"gte": 10}},
    ]
    stock = _generate(document)["stock"]
    pairs = set(zip(stock["store_id"], stock["product_id"], strict=True))
    assert len(stock) == 20 and len(pairs) == 20
    assert (stock["on_hand"] >= 10).all()
    assert (stock["store_id"] != stock["on_hand"]).all()


def test_a_rule_on_one_combination_column_leaves_out_the_rows_that_fail_it():
    document = _stores()
    document["tables"]["stock"]["rules"] = [{"store_id": {"in": [1, 2]}}]
    stock = _generate(document)["stock"]
    assert len(stock) == 2 * 4
    assert set(stock["store_id"]) == {1, 2}


def test_the_combination_columns_are_a_unique_key_in_the_dbt_export():
    inputs = to_engine(from_dict(_stores()))
    stock = inputs.tables["stock"]
    assert {"columns": ["store_id", "product_id"], "type": "unique"} in stock.composite_keys
    found = [
        test
        for test in dbt_tests(inputs.tables, inputs.refs)
        if test.type == "unique_combination" and "stock" in test.name
    ]
    assert found, "the dbt export tests the combination as one unique key"


def test_a_composite_key_already_stating_the_combinations_is_not_added_twice():
    document = _stores()
    document["tables"]["stock"]["keys"] = [{"unique": ["store_id", "product_id"]}]
    stock = to_engine(from_dict(document)).tables["stock"]
    assert [k for k in stock.composite_keys if k["type"] == "unique"] == [
        {"columns": ["store_id", "product_id"], "type": "unique"}
    ]


def test_a_row_count_for_the_table_is_refused_at_generation():
    with pytest.raises(ValueError, match="combinations"):
        _generate(_stores(), stock=10)


def test_too_many_combinations_are_refused_naming_the_sizes():
    document = _stores()
    document["run"]["rows_per_table"] = {"stores": 2000, "products": 2000}
    with pytest.raises(ValueError, match="4,000,000 rows.*stores 2000 x products 2000"):
        _generate(document)


def test_a_combination_table_with_incremental_is_refused_with_days():
    document = _stores()
    document["tables"]["stock"]["columns"]["updated_at"] = "timestamp"
    document["tables"]["stock"]["incremental"] = {"new_per_day": 3, "updated_at": "updated_at"}
    with pytest.raises(ValueError, match="combinations"):
        generate_days(to_engine(from_dict(document)), 2, seed=1, as_of=AS_OF)


def test_a_combination_table_generates_without_the_others_changing():
    plain = _stores()
    del plain["tables"]["stock"]["combinations"]
    first = _generate(plain, stock=20)
    second = _generate(_stores())
    pd.testing.assert_frame_equal(first["stores"], second["stores"])
    pd.testing.assert_frame_equal(first["products"], second["products"])


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------
def test_the_document_round_trips_and_needs_0_7_0():
    document = _league()
    model = from_dict(document)
    assert model.tables["games"].combinations == ["home_team_id", "away_team_id"]
    assert to_dict(model)["tables"]["games"]["combinations"] == ["home_team_id", "away_team_id"]
    assert "    combinations: [home_team_id, away_team_id]\n" in dump(model)
    assert from_dict(to_dict(model)) == model

    old = copy.deepcopy(_stores())
    old["model2data"] = "0.6.0"
    assert any("combinations" in message and "0.7.0" in message for message in _errors(old))
    # A model read as an earlier version (from DBML, say) is written as 0.7.0 once it uses one.
    assert document_version(replace(model, version="0.6.0")) == "0.7.0"


def test_a_model_without_combinations_is_still_0_6_0():
    document = {
        "model2data": "0.6.0",
        "name": "m",
        "tables": {"a": {"columns": {"id": {"type": "integer", "pk": True}}}},
    }
    assert _errors(document) == []
    assert document_version(from_dict(document)) == "0.6.0"


def test_load_reads_a_combination_document(tmp_path):
    path = tmp_path / "league.model2data.yml"
    path.write_text(dump(from_dict(_league())))
    assert load(path) == from_dict(_league())


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def _with(table: str, change) -> list[str]:
    document = _stores()
    change(document["tables"][table], document)
    return _errors(document)


@pytest.mark.parametrize(
    ("change", "text"),
    [
        (lambda t, d: t.update(combinations=["store_id"]), "lists one column"),
        (lambda t, d: t.update(combinations=["store_id", "nope"]), "not a column"),
        (lambda t, d: t.update(combinations=["store_id", "on_hand"]), "not a foreign key"),
        (
            lambda t, d: t["columns"]["store_id"].update(generate={"null_rate": 0.1}),
            "takes no hint",
        ),
        (
            lambda t, d: t["columns"]["store_id"].update(generate={"at_least": 2}),
            "takes no hint",
        ),
        (
            lambda t, d: t["columns"]["store_id"].update(generate={"when": {"on_hand": [1]}}),
            "takes no hint",
        ),
        (
            lambda t, d: t["columns"]["store_id"].update(
                generate={"derive": {"column": "product_id"}}
            ),
            "takes no hint",
        ),
        (lambda t, d: t["columns"]["store_id"].update(unique=True), "by itself"),
        (lambda t, d: t["columns"]["store_id"].update(pk=True), "by itself"),
        (lambda t, d: d["run"]["rows_per_table"].update(stock=10), "sets the rows of stock"),
    ],
)
def test_validate_reports(change, text):
    messages = _with("stock", change)
    assert any(text in message for message in messages), messages


def test_validate_reports_a_self_reference_and_a_cycle():
    document = _stores()
    stock = document["tables"]["stock"]
    stock["columns"]["parent_id"] = {"type": "integer", "references": "stock.id"}
    stock["combinations"] = ["store_id", "parent_id"]
    assert any("depends on stock" in message for message in _errors(document))

    document = _stores()
    document["tables"]["stores"]["columns"]["last_stock_id"] = {
        "type": "integer",
        "references": "stock.id",
    }
    assert any("depends on stock" in message for message in _errors(document))


def test_validate_leaves_the_defect_count_of_a_combination_table_alone():
    document = _stores()
    document["model2data"] = "0.7.0"
    document["tables"]["stock"]["defects"] = [{"type": "duplicate_keys", "count": 50}]
    assert _errors(document) == []


# ---------------------------------------------------------------------------
# The edges the validation leaves to the engine, and to the schema
# ---------------------------------------------------------------------------
def _stock_engine(**generated: pd.DataFrame):
    from model2data.generate.combinations import combination_frame

    table = to_engine(from_dict(_stores())).tables["stock"]
    lookup = {("stock", "store_id"): ("stores", "id"), ("stock", "product_id"): ("products", "id")}

    def make(table=table, lookup=lookup):
        return combination_frame(
            table, "stock", lookup, generated, run_anchor=datetime(2026, 11, 20)
        )

    return table, lookup, make


def test_the_engine_refuses_a_combination_column_that_is_not_a_foreign_key():
    table, lookup, make = _stock_engine(stores=pd.DataFrame({"id": [1]}))
    lookup.pop(("stock", "product_id"))
    with pytest.raises(ValueError, match=r"stock\.product_id: `combinations` names a column"):
        make(table, lookup)


def test_the_engine_refuses_parents_that_are_not_generated_first():
    _, _, make = _stock_engine(stores=pd.DataFrame({"id": [1, 2]}))
    with pytest.raises(ValueError, match=r"needs the rows of products, which are not generated"):
        make()


def test_the_engine_refuses_combinations_that_leave_no_row():
    _, _, make = _stock_engine(
        stores=pd.DataFrame({"id": [1, 2]}), products=pd.DataFrame({"id": []})
    )
    with pytest.raises(ValueError, match="no combination of store_id, product_id is left"):
        make()


def _chain() -> dict[str, Any]:
    """stock -> stores -> regions -> countries, and stores -> countries too (a diamond)."""
    document = _stores()
    tables = document["tables"]
    tables["countries"] = {"columns": {"id": {"type": "integer", "pk": True}}}
    tables["regions"] = {
        "columns": {
            "id": {"type": "integer", "pk": True},
            "country_id": {"type": "integer", "references": "countries.id"},
        }
    }
    tables["stores"]["columns"]["region_id"] = {"type": "integer", "references": "regions.id"}
    tables["stores"]["columns"]["country_id"] = {"type": "integer", "references": "countries.id"}
    return document


def test_parents_that_share_an_ancestor_are_not_a_cycle():
    assert _errors(_chain()) == []


def test_a_parent_that_depends_on_the_table_through_another_is_a_cycle():
    document = _chain()
    document["tables"]["countries"]["columns"]["stock_id"] = {
        "type": "integer",
        "references": "stock.id",
    }
    assert any("a table that depends on stock" in message for message in _errors(document))


def test_a_malformed_combinations_is_left_to_the_schema_and_does_not_crash():
    for value in ("store_id", {"store_id": 1}):
        assert _with("stock", lambda t, d, v=value: t.update(combinations=v))
    messages = _with("stock", lambda t, d: t.update(combinations=[7, "product_id"]))
    assert messages and not any("not a column" in message for message in messages)


def test_an_extension_in_the_generate_of_a_combination_column_is_not_a_hint():
    def extend(table, document):
        table["columns"]["store_id"]["generate"] = {"x-note": "free"}

    assert _with("stock", extend) == []
