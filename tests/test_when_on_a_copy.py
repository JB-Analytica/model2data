"""A `when` naming a column copied from the row's own parent.

`predictions.match_status` references `matches.status` beside `predictions.match_id`
references `matches.id`: it holds that match's status, so `points` can depend on it.
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.model import from_dict, to_engine
from model2data.model.errors import ModelError

AS_OF = date(2026, 11, 25)

GAME: dict[str, Any] = {
    "model2data": "0.9.0",
    "enums": {"match_status": ["scheduled", "live", "finished"]},
    "tables": {
        "matches": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "status": {"type": "match_status", "not_null": True},
                "goals": {
                    "type": "int",
                    "generate": {"max": 7, "when": {"status": ["finished"]}},
                },
            }
        },
        "predictions": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "match_id": {"type": "int", "not_null": True, "references": "matches.id"},
                "match_status": {"type": "match_status", "references": "matches.status"},
                "points": {
                    "type": "int",
                    "generate": {"max": 3, "when": {"match_status": ["finished"]}},
                },
                "settled_at": {
                    "type": "timestamp",
                    "generate": {"when": {"match_status": ["finished"]}},
                },
            }
        },
    },
}


def _rows(document: dict[str, Any], seed: int = 7) -> dict[str, Any]:
    engine = to_engine(from_dict(document))
    return generate_data_from_dbml(
        engine.tables, engine.refs, base_rows=200, seed=seed, as_of=AS_OF
    )


def _holds(rows: dict[str, Any]) -> None:
    predictions, matches = rows["predictions"], rows["matches"].set_index("id")
    linked = predictions["match_id"].notna()
    status = predictions["match_id"].map(matches["status"])
    assert (predictions["match_status"][linked] == status[linked]).all()
    finished = status == "finished"
    assert finished.any() and (~finished).any()
    for column in ("points", "settled_at"):
        assert (predictions[column].notna() == finished).all()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_a_value_is_held_on_the_rows_the_parent_allows(seed):
    _holds(_rows(GAME, seed))


def test_a_derived_column_can_depend_on_the_copy():
    document = copy.deepcopy(GAME)
    columns = document["tables"]["predictions"]["columns"]
    columns["match_goals"] = {"type": "int", "references": "matches.goals"}
    columns["points"] = {
        "type": "int",
        "generate": {
            "when": {"match_status": ["finished"]},
            "derive": {"cases": [{"if": {"match_goals": {"gte": 4}}, "then": 3}], "else": 0},
        },
    }
    rows = _rows(document)
    _holds(rows)
    predictions = rows["predictions"].dropna(subset=["points"])
    high = predictions["match_goals"].astype(int) >= 4
    assert (predictions["points"].astype(int) == high.map({True: 3, False: 0})).all()


def test_a_key_drawn_last_is_followed_too():
    """A cycle of foreign keys broken at the key the copy is read through."""
    document = copy.deepcopy(GAME)
    document["tables"]["predictions"]["columns"]["match_id"].pop("not_null")
    document["tables"]["matches"]["columns"]["first_prediction_id"] = {
        "type": "int",
        "not_null": True,
        "references": "predictions.id",
    }
    _holds(_rows(document))


def test_the_foreign_key_itself_is_still_refused():
    document = copy.deepcopy(GAME)
    points = document["tables"]["predictions"]["columns"]["points"]
    points["generate"]["when"] = {"match_id": [1, 2]}
    with pytest.raises(ModelError, match="names match_id, a foreign key"):
        from_dict(document)


def test_a_value_the_copy_cannot_hold_is_still_refused():
    document = copy.deepcopy(GAME)
    points = document["tables"]["predictions"]["columns"]["points"]
    points["generate"]["when"] = {"match_status": ["over"]}
    with pytest.raises(ModelError, match="not a member of match_status"):
        from_dict(document)


def test_later_days_keep_it():
    document = copy.deepcopy(GAME)
    document["tables"]["predictions"]["incremental"] = {"new_per_day": 20}
    document["tables"]["matches"]["incremental"] = {"new_per_day": 5, "update_rate": 0.3}
    results = generate_days(from_dict(document), 3, base_rows=60, seed=5, as_of=AS_OF)
    frame = results[-1].tables["predictions"].state
    assert len(frame) == 120
    assert (frame["points"].notna() == (frame["match_status"] == "finished")).all()
