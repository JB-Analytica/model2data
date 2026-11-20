"""The generator catalogue: what a type can name, its aliases, the unknown-type warning,
text keys that fit their length, and where a lone country comes from."""

from __future__ import annotations

import random
import re
from importlib import resources

import pytest
from faker import Faker

from model2data.generate import catalogue
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.faker import (
    _NAME_PATTERNS,
    _infer_by_type,
    generate_column_values,
    is_generator_type,
    set_locale,
)
from model2data.model import validate
from model2data.parse.dbml import ColumnDef, TableDef

README = resources.files("model2data").joinpath("spec/README.md").read_text("utf-8")


# -- the catalogue is true ----------------------------------------------------------
@pytest.mark.parametrize("name", catalogue.generator_names())
def test_every_listed_name_works_as_a_column_type(name):
    assert is_generator_type(name)
    assert _infer_by_type(name) is not None
    set_locale(None)
    random.seed(1)
    values = generate_column_values(
        ColumnDef(name="c", data_type=name, settings={"not null"}), row_count=5, table_name="t"
    )
    assert len(values) == 5 and all(isinstance(value, str) and value for value in values)


def test_the_readme_carries_the_catalogue_and_the_name_patterns_verbatim():
    assert catalogue.render_generators_table() in README
    assert catalogue.render_name_inference_table() in README


def test_the_name_inference_lists_exactly_the_engines_patterns_in_order():
    assert [pattern for pattern, _ in catalogue.NAME_INFERENCE] == [
        pattern for pattern, _ in _NAME_PATTERNS
    ]


def test_every_name_pattern_generates_a_listed_generator_or_free_text():
    known = set(catalogue.generator_names())
    for pattern, generates in catalogue.NAME_INFERENCE:
        assert generates in known or generates == "free text" or is_generator_type(generates), (
            pattern
        )


def test_the_readme_says_team_name_matches_nothing():
    assert "`team_name`, `player_name` and `round_name`" in README


# -- aliases ----------------------------------------------------------------------
def _one_column(data_type: str, name: str = "c") -> list:
    set_locale(None)
    random.seed(7)
    Faker.seed(7)
    return generate_column_values(
        ColumnDef(name=name, data_type=data_type, settings={"not null"}),
        row_count=20,
        table_name="t",
    )


@pytest.mark.parametrize(
    "alias, target",
    [("full_name", "name"), ("person_name", "name"), ("company_name", "company")],
)
def test_an_alias_produces_the_values_of_its_target(alias, target):
    assert _one_column(alias, "x") == _one_column(target, "x")


def test_the_aliases_are_listed_in_the_table():
    for alias in ("full_name", "person_name", "company_name"):
        assert f"`{alias}`" in catalogue.render_generators_table()


def test_an_aliased_person_type_is_coherent_with_the_row():
    table = TableDef(
        name="people",
        columns=[
            ColumnDef(name="id", data_type="int", settings={"pk"}),
            ColumnDef(name="who", data_type="person_name", settings={"not null"}),
            ColumnDef(name="mail", data_type="email", settings={"not null"}),
        ],
    )
    df = generate_data_from_dbml({"people": table}, [], base_rows=30, seed=3)["people"]
    for who, mail in zip(df["who"], df["mail"], strict=True):
        first, last = who.split(" ", 1)
        assert re.sub(r"[^a-z]", "", last.lower())[:4] in mail.lower()


# -- the unknown-type warning ---------------------------------------------------------
def _model(**columns: str) -> str:
    body = "\n".join(f"      {name}: {type_}" for name, type_ in columns.items())
    return f"model2data: 0.6.0\ntables:\n  teams:\n    columns:\n      id: int\n{body}\n"


def test_an_unknown_type_warns_with_a_suggestion():
    issues = validate(_model(team_name="club_name"))
    assert len(issues) == 1
    issue = issues[0]
    assert issue.severity == "warning"
    assert issue.path == "tables.teams.columns.team_name.type"
    assert issue.line == 6
    assert issue.message.startswith(
        "'club_name' is not a generator or an SQL type; it will generate generic text."
    )
    assert "Did you mean 'color_name'" in issue.message


def test_an_unknown_type_with_nothing_close_has_no_suggestion():
    (issue,) = validate(_model(c="wibblewobble"))
    assert issue.message == (
        "'wibblewobble' is not a generator or an SQL type; it will generate generic text."
    )


def test_a_close_name_is_suggested_for_a_typo():
    (issue,) = validate(_model(c="emial"))
    assert "Did you mean 'email'" in issue.message


@pytest.mark.parametrize(
    "type_",
    [
        "varchar(32)",
        "VARCHAR(255)",
        "number(38,0)",
        "numeric(10, 2)",
        "timestamp_ntz",
        "timestamptz",
        "int64",
        "bigint",
        "string",
        "text",
        "bool",
        "boolean",
        "jsonb",
        "uuid",
        "double precision",
        "timestamp with time zone",
        "character varying",
        "float64",
        "bytea",
        "date",
        "array<string>",
        "text[]",
        "name",
        "full_name",
        "person_name",
        "company_name",
        "email",
        "ssn",
        "iban",
    ],
)
def test_a_legitimate_type_does_not_warn(type_):
    assert validate(_model(c=f'"{type_}"')) == []


def test_an_enum_of_the_model_is_not_an_unknown_type():
    document = _model(status="order_status") + "enums:\n  order_status: [open, closed]\n"
    assert validate(document) == []


def test_the_warning_does_not_make_the_document_fail():
    from model2data.model import load

    assert load(_model(team_name="club_name")) is not None


# -- text keys respect the declared length ------------------------------------------------
def _key_column(data_type: str, rows: int = 200) -> list:
    set_locale(None)
    random.seed(5)
    return generate_column_values(
        ColumnDef(name="id", data_type=data_type, settings={"pk", "not null"}),
        row_count=rows,
        ensure_unique=True,
        table_name="t",
    )


def test_a_varchar_32_key_is_a_uuid_without_dashes():
    values = _key_column("VARCHAR(32)")
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize(
    "data_type, length", [("char(8)", 8), ("string(12)", 12), ("varchar(20)", 20)]
)
def test_a_shorter_text_key_is_hex_of_that_length(data_type, length):
    values = _key_column(data_type)
    assert all(re.fullmatch(rf"[0-9a-f]{{{length}}}", value) for value in values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize("data_type", ["varchar(36)", "varchar(255)", "varchar"])
def test_a_long_or_unsized_text_key_keeps_the_dashed_uuid(data_type):
    values = _key_column(data_type)
    assert all(re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", v) for v in values)


def test_a_short_text_key_is_deterministic_and_foreign_keys_match_it():
    parent = TableDef(
        name="parents", columns=[ColumnDef(name="id", data_type="varchar(32)", settings={"pk"})]
    )
    child = TableDef(
        name="children",
        columns=[
            ColumnDef(name="id", data_type="int", settings={"pk"}),
            ColumnDef(name="parent_id", data_type="varchar(32)", settings={"not null"}),
        ],
    )
    refs = [
        {
            "source_table": "children",
            "source_column": "parent_id",
            "target_table": "parents",
            "target_column": "id",
        }
    ]

    def run():
        return generate_data_from_dbml(
            {"parents": parent, "children": child}, refs, base_rows=40, seed=9
        )

    first, second = run(), run()
    assert first["parents"].equals(second["parents"])
    assert set(first["children"]["parent_id"]) <= set(first["parents"]["id"])
    assert all(len(value) == 32 for value in first["parents"]["id"])


# -- no home country without a locale --------------------------------------------------------
def _countries(locale):
    table = TableDef(
        name="customers",
        columns=[
            ColumnDef(name="id", data_type="int", settings={"pk"}),
            ColumnDef(name="country", data_type="varchar", settings={"not null"}),
        ],
    )
    return generate_data_from_dbml({"customers": table}, [], base_rows=600, seed=1, locale=locale)[
        "customers"
    ]["country"]


def test_with_no_locale_a_lone_country_comes_from_the_whole_world():
    shares = _countries(None).value_counts(normalize=True)
    assert shares.iloc[0] < 0.2
    assert len(shares) > 50


def test_with_a_locale_a_lone_country_still_favours_its_home():
    counts = _countries("nl_BE").value_counts(normalize=True)
    assert counts.index[0] == "Belgium" and counts.iloc[0] > 0.5


def test_an_explicit_default_locale_is_a_locale():
    assert _countries("en_US").value_counts(normalize=True).iloc[0] > 0.5
