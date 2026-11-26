"""`via` on a copied value (spec 0.10.0): which foreign key the value is copied through."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from typing import Any

import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.model import (
    SPEC_VERSION,
    ModelError,
    Reference,
    dump,
    fill_copy_paths,
    from_dbml,
    from_dict,
    load,
    to_dict,
    to_engine,
)
from model2data.model.document import document_version
from model2data.model.validate import check, minor_numbers, schema_url

AS_OF = date(2026, 11, 25)

LEAGUE: dict[str, Any] = {
    "model2data": "0.9.0",
    "tables": {
        "teams": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "name": {"type": "varchar"},
                "venue_id": {"type": "int", "generate": {"min": 1, "max": 5}},
            }
        },
        "fixtures": {
            "columns": {
                "id": {"type": "int", "pk": True},
                "home_team_id": {"type": "int", "not_null": True, "references": "teams.id"},
                "away_team_id": {"type": "int", "not_null": True, "references": "teams.id"},
                "venue_id": {"type": "int", "references": "teams.venue_id"},
            }
        },
    },
}


def _league(reference: Any = "teams.venue_id", version: str = "0.10.0") -> dict[str, Any]:
    document = copy.deepcopy(LEAGUE)
    document["model2data"] = version
    document["tables"]["fixtures"]["columns"]["venue_id"]["references"] = reference
    return document


def _via(via: str) -> dict[str, Any]:
    return _league({"to": "teams.venue_id", "via": via})


def _rows(document: dict[str, Any], seed: int = 5) -> dict:
    engine = to_engine(from_dict(document))
    return generate_data_from_dbml(engine.tables, engine.refs, base_rows=60, seed=seed, as_of=AS_OF)


def _digest(data: dict) -> str:
    text = {key: frame.to_csv(index=False) for key, frame in data.items()}
    return hashlib.sha256(json.dumps(text, sort_keys=True).encode()).hexdigest()


def _errors(document: dict[str, Any]) -> list[str]:
    try:
        from_dict(document)
    except ModelError as error:
        return [str(issue) for issue in error.issues]
    return []


def test_two_paths_and_no_via_is_an_error_naming_both():
    for version in ("0.9.0", "0.10.0", "0.5.0"):
        (message,) = _errors(_league(version=version))
        assert message == (
            "tables.fixtures.columns.venue_id.references: fixtures.venue_id can reach teams "
            "through home_team_id or away_team_id; add via:"
        )


def test_three_paths_are_all_named_and_the_error_carries_a_fix():
    document = _league()
    document["tables"]["fixtures"]["columns"]["neutral_id"] = {
        "type": "int",
        "references": "teams.id",
    }
    with pytest.raises(ModelError) as raised:
        from_dict(document)
    (issue,) = raised.value.issues
    assert "through home_team_id, away_team_id or neutral_id; add via:" in issue.message
    assert issue.suggestion is not None
    assert issue.suggestion.path == "tables.fixtures.columns.venue_id.references.via"
    assert issue.suggestion.value == "home_team_id"
    assert issue.suggestion.spec is None


def test_the_suggestion_names_the_spec_an_older_document_needs():
    with pytest.raises(ModelError) as raised:
        from_dict(_league(version="0.9.0"))
    assert raised.value.issues[0].suggestion is not None
    assert raised.value.issues[0].suggestion.spec == "0.10.0"


def test_via_home_copies_the_home_teams_value_and_via_away_the_away_teams():
    for via, column in (("home_team_id", "home_team_id"), ("away_team_id", "away_team_id")):
        data = _rows(_via(via))
        teams = data["teams"].set_index("id")["venue_id"]
        fixtures = data["fixtures"]
        assert (fixtures["venue_id"] == fixtures[column].map(teams)).all()
    home = _rows(_via("home_team_id"))["fixtures"]
    away = _rows(_via("away_team_id"))["fixtures"]
    assert (home["venue_id"] != away["venue_id"]).any()


def test_a_bad_via_lists_the_valid_ones():
    (message,) = _errors(_via("id"))
    assert message == (
        "tables.fixtures.columns.venue_id.references.via: `via` names 'id', which is not a "
        "foreign key of one column of fixtures onto teams: use home_team_id or away_team_id"
    )


def test_via_on_a_reference_that_is_not_a_copy_is_an_error():
    document = _via("home_team_id")
    document["tables"]["fixtures"]["columns"]["home_team_id"]["references"] = {
        "to": "teams.id",
        "via": "away_team_id",
    }
    (message,) = _errors(document)
    assert "fixtures.home_team_id is not a copy" in message
    assert message.startswith("tables.fixtures.columns.home_team_id.references.via:")


def test_a_single_path_needs_no_via_and_via_naming_it_changes_nothing():
    document = _league()
    del document["tables"]["fixtures"]["columns"]["away_team_id"]
    plain = _rows(document)
    document["tables"]["fixtures"]["columns"]["venue_id"]["references"] = {
        "to": "teams.venue_id",
        "via": "home_team_id",
    }
    assert _digest(_rows(document)) == _digest(plain)
    assert _errors(_league(version="0.9.0") | {}) != []
    # 0.9.0 documents with one path read as they always did.
    old = copy.deepcopy(document)
    old["model2data"] = "0.9.0"
    old["tables"]["fixtures"]["columns"]["venue_id"]["references"] = "teams.venue_id"
    assert _errors(old) == []
    assert _digest(_rows(old)) == _digest(plain)


def test_via_is_spec_0_10_and_an_older_document_cannot_use_it():
    (message,) = _errors(_league({"to": "teams.venue_id", "via": "home_team_id"}, "0.9.0"))
    assert message == (
        "tables.fixtures.columns.venue_id.references.via: `via` is spec 0.10.0, and the "
        "document is written against 0.9: write `model2data: 0.10.0`"
    )
    assert _errors(_via("home_team_id")) == []


def test_versions_compare_as_numbers():
    assert SPEC_VERSION == "0.10.0"
    assert minor_numbers("0.10.0") > minor_numbers("0.9.0")
    assert minor_numbers("0.10") > minor_numbers("0.9")
    assert schema_url("0.10.0").endswith("/spec/0.10.0/model.schema.json")
    assert schema_url("0.9.0").endswith("/spec/0.9.0/model.schema.json")
    for version in ("0.10.0", "0.10"):
        document = _via("home_team_id") | {"model2data": version}
        assert [i for i in check(document) if i.is_error] == []
    # 0.1.0 (DBML era) and 0.11.0 are not read; a 0.1 prefix must not pass for 0.10.
    for version in ("0.1.0", "0.11.0"):
        assert check(_via("home_team_id") | {"model2data": version})
    model = from_dict(_via("home_team_id") | {"model2data": "0.10"})
    assert document_version(model) == "0.10"
    model.version = "0.9.0"
    assert document_version(model) == "0.10.0"


def test_a_document_is_written_as_0_10_once_it_uses_via():
    model = from_dict(_via("away_team_id"))
    assert to_dict(model)["tables"]["fixtures"]["columns"]["venue_id"]["references"] == {
        "to": "teams.venue_id",
        "via": "away_team_id",
    }
    assert "model2data: 0.10.0" in dump(model)
    assert "spec/0.10.0/model.schema.json" in dump(model)


def test_roundtrips_in_yaml_and_json():
    model = from_dict(_via("away_team_id"))
    assert load(dump(model)) == model
    assert load(json.dumps(to_dict(model)), format="json") == model
    reference = load(dump(model)).tables["fixtures"].columns["venue_id"].references
    assert reference is not None and reference.via == "away_team_id"


DBML = """
Table teams {
  id int [pk]
  venue_id int
}
Table fixtures {
  id int [pk]
  home_team_id int [not null, ref: > teams.id]
  away_team_id int [not null, ref: > teams.id]
  venue_id int [ref: > teams.venue_id, note: '{"via": "away_team_id"}']
}
"""


def test_a_dbml_note_hint_carries_via_and_the_model_roundtrips():
    model = from_dbml(DBML)
    assert model.version == "0.10.0"
    reference = model.tables["fixtures"].columns["venue_id"].references
    assert reference is not None
    assert reference.via == "away_team_id"
    assert model.tables["fixtures"].columns["venue_id"].generate == {}
    assert load(dump(model)) == model
    engine = to_engine(model)
    data = generate_data_from_dbml(engine.tables, engine.refs, base_rows=40, seed=2, as_of=AS_OF)
    teams = data["teams"].set_index("id")["venue_id"]
    assert (data["fixtures"]["venue_id"] == data["fixtures"]["away_team_id"].map(teams)).all()


def test_dbml_without_via_is_refused_when_ambiguous_and_via_needs_a_ref():
    with pytest.raises(ModelError, match="add via:"):
        from_dbml(DBML.replace(""", note: '{"via": "away_team_id"}'""", ""))
    with pytest.raises(ModelError, match="no ref onto a parent's column"):
        from_dbml(DBML.replace("[ref: > teams.venue_id, note", "[note"))


def test_fill_copy_paths_writes_the_first_foreign_key_and_keeps_the_data():
    document = _league(version="0.9.0")
    filled, notes = fill_copy_paths(document)
    assert notes == ["fixtures.venue_id -> home_team_id"]
    assert filled["model2data"] == "0.10.0"
    assert filled["tables"]["fixtures"]["columns"]["venue_id"]["references"] == {
        "to": "teams.venue_id",
        "via": "home_team_id",
    }
    # The input is left alone.
    assert document["tables"]["fixtures"]["columns"]["venue_id"]["references"] == "teams.venue_id"
    assert document["model2data"] == "0.9.0"
    assert _errors(filled) == []
    # The same rows as naming the home team by hand: what earlier versions read.
    assert _digest(_rows(filled)) == _digest(_rows(_via("home_team_id")))
    data = _rows(filled)
    teams = data["teams"].set_index("id")["venue_id"]
    assert (data["fixtures"]["venue_id"] == data["fixtures"]["home_team_id"].map(teams)).all()


def test_fill_copy_paths_on_a_model_and_when_there_is_nothing_to_do():
    filled, notes = fill_copy_paths(from_dict(_via("away_team_id")))
    assert notes == [] and filled == from_dict(_via("away_team_id"))
    plain = copy.deepcopy(LEAGUE)
    del plain["tables"]["fixtures"]["columns"]["away_team_id"]
    same, notes = fill_copy_paths(plain)
    assert notes == [] and same == plain and same["model2data"] == "0.9.0"

    model = from_dict(plain)
    again, notes = fill_copy_paths(model)
    assert notes == [] and again == model


def test_fill_copy_paths_keeps_one_to_one_and_still_refuses_other_errors():
    document = _league({"to": "teams.venue_id", "one_to_one": True}, "0.9.0")
    filled, _ = fill_copy_paths(document)
    assert filled["tables"]["fixtures"]["columns"]["venue_id"]["references"] == {
        "to": "teams.venue_id",
        "via": "home_team_id",
        "one_to_one": True,
    }
    broken = _league(version="0.9.0")
    broken["tables"]["fixtures"]["columns"]["nope"] = {"type": "int", "references": "x.y"}
    with pytest.raises(ModelError):
        fill_copy_paths(broken)


def test_a_model_object_is_filled_too():
    model = from_dict(_via("home_team_id"))
    ambiguous = copy.deepcopy(model)
    ambiguous.tables["fixtures"].columns["venue_id"].references = Reference("teams.venue_id")
    ambiguous.version = "0.9.0"
    filled, notes = fill_copy_paths(ambiguous)
    assert notes == ["fixtures.venue_id -> home_team_id"]
    assert filled.version == "0.10.0"
    assert filled.tables["fixtures"].columns["venue_id"].references == Reference(
        "teams.venue_id", via="home_team_id"
    )
    assert ambiguous.tables["fixtures"].columns["venue_id"].references == Reference(
        "teams.venue_id"
    )


def test_days_follow_via():
    document = _via("away_team_id")
    document["tables"]["teams"]["incremental"] = {"new_per_day": 5}
    document["tables"]["fixtures"]["incremental"] = {"new_per_day": 10}
    results = generate_days(from_dict(document), 2, base_rows=20, seed=3, as_of=AS_OF)
    state = {key: table.state for key, table in results[-1].tables.items()}
    teams = state["teams"].set_index("id")["venue_id"]
    fixtures = state["fixtures"]
    assert (fixtures["venue_id"] == fixtures["away_team_id"].map(teams)).all()
