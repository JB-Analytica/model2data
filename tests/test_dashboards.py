"""The dashboard spec, its schema and the code that reads and writes it say the same thing.

`model2data/spec/dashboards/dashboard.schema.json` and `README.md` are normative;
`model2data.dashboards` implements them. Every document here is run through both
the schema and the checker, so the two cannot drift apart unnoticed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest

from model2data import dashboards
from model2data.dashboards import (
    GRAINS,
    KINDS,
    SCHEMA_URL,
    SPEC_VERSION,
    TILE_FIELDS,
    Dashboard,
    DashboardError,
    Issue,
    Known,
    Page,
    Place,
    Tile,
    check_dashboard,
    check_name,
    dashboard_name,
    from_dict,
    is_dashboard_file,
    known_names,
    read_dashboard,
    write_dashboard,
)
from model2data.dashboards.types import MAX_ISSUES
from model2data.metrics import load as load_metrics
from model2data.model import load as load_model
from model2data.model._yaml import parse_yaml

SPEC = Path(__file__).resolve().parent.parent / "model2data" / "spec"
SCHEMA = json.loads((SPEC / "dashboards" / "dashboard.schema.json").read_text(encoding="utf-8"))
README = (SPEC / "dashboards" / "README.md").read_text(encoding="utf-8")
EXAMPLE = SPEC / "examples" / "coffee_webshop_sales.dashboard.yml"
GUIDE = (SPEC.parent / "guide" / "dashboards.md").read_text(encoding="utf-8")
HEADER = f"model2data-dashboard: {SPEC_VERSION}\n"


@pytest.fixture(scope="module")
def coffee() -> Known:
    model = load_model(SPEC / "examples" / "coffee_webshop.model2data.yml")
    found = load_metrics(SPEC / "examples" / "coffee_webshop.metrics.yml", model)
    return known_names(model, found)


def _schema_ok(text: str) -> bool:
    return not list(jsonschema.Draft202012Validator(SCHEMA).iter_errors(parse_yaml(text)))


def _errors(text: str, known: Known | None = None) -> list[Issue]:
    return [issue for issue in check_dashboard(text, known) if issue.is_error]


def _warnings(text: str, known: Known | None = None) -> list[Issue]:
    return [issue for issue in check_dashboard(text, known) if not issue.is_error]


def _tile(lines: str) -> str:
    """A one-page, one-tile dashboard around a tile's YAML lines (indented by 8)."""
    return HEADER + "pages:\n  - id: p\n    name: P\n    tiles:\n" + lines


# ---------------------------------------------------------------------------
# The schema and the spec
# ---------------------------------------------------------------------------
def test_the_schema_is_a_valid_draft_2020_12_schema():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert dashboards.schema() == SCHEMA


def test_the_schema_id_is_the_url_files_point_at():
    assert SCHEMA["$id"] == SCHEMA_URL
    assert (
        SCHEMA_URL
        == f"https://www.jbanalytica.com/model2data/spec/dashboards/{SPEC_VERSION}/dashboard.schema.json"
    )
    text = EXAMPLE.read_text(encoding="utf-8")
    assert text.splitlines()[0] == f"# yaml-language-server: $schema={SCHEMA_URL}"
    assert text.splitlines()[1] == f"model2data-dashboard: {SPEC_VERSION}"
    assert README.startswith(f"# model2data dashboards — spec {SPEC_VERSION}")


def test_the_example_is_valid_and_checks_clean_against_the_example_model(coffee):
    text = EXAMPLE.read_text(encoding="utf-8")
    assert _schema_ok(text)
    assert check_dashboard(EXAMPLE, coffee) == []
    board = read_dashboard(EXAMPLE, coffee)
    assert board.model == "coffee_webshop"
    # Every kind is shown in the example.
    assert {tile.kind for page in board.pages for tile in page.tiles} == set(KINDS)


@pytest.mark.parametrize("where", ["readme", "guide"])
def test_the_documented_examples_check_clean(where, coffee):
    page = README if where == "readme" else GUIDE
    blocks = re.findall(r"```yaml\n(.*?)```", page, flags=re.S)
    assert blocks
    for block in blocks:
        assert _schema_ok(block)
        assert check_dashboard(block, coffee) == []


def test_kinds_grains_and_fields_are_the_schemas():
    tile = SCHEMA["$defs"]["tile"]
    assert tuple(tile["properties"]["kind"]["enum"]) == KINDS
    assert tuple(SCHEMA["$defs"]["grain"]["enum"]) == GRAINS
    assert tuple(tile["properties"]) == TILE_FIELDS
    assert tuple(SCHEMA["$defs"]["place"]["properties"]) == ("x", "y", "w", "h")
    assert SCHEMA["properties"]["pages"]["maxItems"] == dashboards.MAX_PAGES
    assert SCHEMA["$defs"]["page"]["properties"]["tiles"]["maxItems"] == dashboards.MAX_TILES
    assert tile["properties"]["columns"]["maxItems"] == dashboards.MAX_COLUMNS
    assert tile["properties"]["rows"]["maximum"] == dashboards.MAX_TABLE_ROWS
    assert tile["properties"]["text"]["maxLength"] == dashboards.MAX_TEXT
    assert SCHEMA["$defs"]["name"]["maxLength"] == dashboards.MAX_NAME
    assert SCHEMA["$defs"]["place"]["properties"]["w"]["maximum"] == dashboards.GRID_COLUMNS


def test_every_kind_field_and_grain_is_documented():
    kinds_section = README.split("### Tile kinds", 1)[1].split("###", 1)[0]
    for kind in SCHEMA["$defs"]["tile"]["properties"]["kind"]["enum"]:
        assert f"| `{kind}` |" in kinds_section, kind
    fields_section = README.split("### Tiles", 1)[1].split("### Tile kinds", 1)[0]
    for key in SCHEMA["$defs"]["tile"]["properties"]:
        assert f"| `{key}` |" in fields_section, key
    for key in SCHEMA["$defs"]["page"]["properties"]:
        assert f"| `{key}` |" in README.split("### Pages", 1)[1].split("###", 1)[0], key
    for key in SCHEMA["properties"]:
        assert f"| `{key}` |" in README.split("### Top level", 1)[1].split("###", 1)[0], key
    for key in SCHEMA["$defs"]["place"]["properties"]:
        assert f"| `{key}` |" in README.split("### The grid", 1)[1].split("###", 1)[0], key
    for grain in GRAINS:
        assert f"`{grain}`" in README.split("### Grains", 1)[1].split("###", 1)[0], grain
    # The guide names every kind too.
    for kind in KINDS:
        assert f"`{kind}`" in GUIDE, kind


def test_the_name_rule_is_the_documented_one():
    assert dashboards.NAME_RULE.startswith("A dashboard name is 1 to 64")
    assert "^[a-z0-9][a-z0-9_-]{0,63}$" in README
    for name, ok in [("sales", True), ("q1-2026_board", True), ("Sales", False), ("-x", False)]:
        assert (check_name(name) is None) is ok
    assert check_name("a" * 64) is None
    assert check_name("a" * 65) is not None


def test_file_names():
    assert is_dashboard_file("x/sales.dashboard.yml")
    assert is_dashboard_file("sales.dashboard.yaml")
    assert not is_dashboard_file("sales.metrics.yml")
    assert dashboard_name("sales.dashboard.yml") == "sales"
    assert dashboard_name(EXAMPLE) == "coffee_webshop_sales"
    assert check_name(dashboard_name(EXAMPLE)) is None
    assert check_name(dashboard_name("coffee_webshop.sales.dashboard.yml")) is not None


def test_publishing_lists_the_dashboard_schema():
    publishing = (SPEC / "PUBLISHING.md").read_text(encoding="utf-8")
    assert SCHEMA_URL in publishing
    assert "model2data/spec/dashboards/dashboard.schema.json` of engine 1.20.0" in publishing
    assert '"fileMatch": ["*.dashboard.yml", "*.dashboard.yaml"]' in publishing
    assert "dashboards/README.md" in (SPEC / "README.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Schema and checker agree
# ---------------------------------------------------------------------------
ACCEPTED = [
    HEADER,
    HEADER + "pages: []\n",
    HEADER + "pages:\n",
    HEADER + "title: 2024\npages: []\n",
    HEADER + "model: coffee_webshop\ntitle:\npages:\n  - id: 1\n    name: One\n    grain:\n",
    _tile(
        "      - id: t\n        kind: kpi\n        metric:\n        rows:\n"
        "        place: {x: 3.0, y: 0, w: 9, h: 1}\n"
    ),
    _tile(
        "      - id: t\n        kind: table\n        columns: [id, 2024]\n"
        "        place: {x: 11, y: 1000, w: 1, h: 100}\n"
    ),
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_accepted_by_both(text):
    assert _schema_ok(text)
    assert _errors(text) == []


# (document, line, column, a piece of the message)
REFUSED = [
    ("pages: []\n", 1, 1, "`model2data-dashboard: 0.1.0` is required."),
    ("model2data-dashboard: 1\n", 1, 23, "must be a version such as 0.1.0, not 1."),
    (HEADER + "version: 1\n", 2, 1, "the file has unknown key 'version'"),
    (HEADER + "titel: x\n", 2, 1, "unknown key 'titel' (did you mean 'title'?)"),
    (HEADER + "model: ''\n", 2, 8, "model must not be empty."),
    (HEADER + "model: 3\n", 2, 8, "model must be the model's name, as text, not 3."),
    (HEADER + "title: true\n", 2, 8, "title must be text."),
    (HEADER + f"title: {'x' * 201}\n", 2, 8, "title is at most 200 characters."),
    (HEADER + "pages: {}\n", 2, 8, "pages must be a list of pages."),
    (
        HEADER + "pages:\n" + "  - {id: p, name: P}\n" * 21,
        3,
        3,
        "A dashboard has at most 20 pages; this one has 21.",
    ),
    (HEADER + "pages:\n  - p\n", 3, 5, "page 1 must be a mapping with id, name and tiles."),
    (HEADER + "pages:\n  - name: P\n", 3, 5, "page 1 needs an id."),
    (HEADER + "pages:\n  - id: p\n", 3, 5, "page 'p' needs a name."),
    (HEADER + "pages:\n  - id: p\n    name: P\n    grain: monthly\n", 5, 12, "grain should be"),
    (HEADER + "pages:\n  - id: p\n    name: P\n    tile: []\n", 5, 5, "did you mean 'tiles'?"),
    (HEADER + "pages:\n  - id: p\n    name: P\n    tiles: x\n", 5, 12, "tiles must be a list"),
    (HEADER + "pages:\n  - id: [p]\n    name: P\n", 3, 9, "page 1: id should be a valid string"),
    (
        HEADER + "pages:\n  - id: p\n    name: P\n    tiles:\n" + "      - x\n" * 61,
        6,
        7,
        "page 'p' has 61 tiles; a page has at most 60.",
    ),
    (_tile("      - x\n"), 6, 9, "tile 1 on page 'p' must be a mapping with id, kind and place."),
    (_tile("      - kind: kpi\n        place: {x: 0, y: 0, w: 1, h: 1}\n"), 6, 9, "needs an id."),
    (
        _tile("      - id: t\n        place: {x: 0, y: 0, w: 1, h: 1}\n"),
        6,
        9,
        "tile 't' needs a kind.",
    ),
    (_tile("      - id: t\n        kind: kpi\n"), 6, 9, "tile 't' needs a place."),
    (
        _tile("      - id: t\n        kind: chart\n        place: {x: 0, y: 0, w: 1, h: 1}\n"),
        7,
        15,
        "tile 't': kind should be 'kpi', 'line', 'bar', 'stacked', 'donut', 'table', 'filter', "
        "'text' or 'note'",
    ),
    (
        _tile(
            "      - id: t\n        kind: kpi\n        colour: red\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        9,
        "tile 't' has unknown key 'colour'",
    ),
    (_tile("      - id: t\n        kind: kpi\n        place: [0, 0]\n"), 8, 16, "place must be"),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 0, w: 1}\n"),
        8,
        16,
        "place needs h.",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 0, w: 1, h: 1, d: 1}\n"),
        8,
        41,
        "tile 't': place has unknown key 'd'",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 12, y: 0, w: 1, h: 1}\n"),
        8,
        20,
        "place.x should be less than 12",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: -1, y: 0, w: 1, h: 1}\n"),
        8,
        20,
        "place.x should be greater than or equal to 0",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 1001, w: 1, h: 1}\n"),
        8,
        26,
        "place.y should be less than or equal to 1000",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 0, w: 0, h: 1}\n"),
        8,
        32,
        "place.w should be greater than or equal to 1",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 0, w: 13, h: 1}\n"),
        8,
        32,
        "place.w should be less than or equal to 12",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 0, y: 0, w: 1, h: 101}\n"),
        8,
        38,
        "place.h should be less than or equal to 100",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: 1.5, y: 0, w: 1, h: 1}\n"),
        8,
        20,
        "place.x should be a valid integer",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: true, y: 0, w: 1, h: 1}\n"),
        8,
        20,
        "place.x should be a valid integer",
    ),
    (
        _tile("      - id: t\n        kind: kpi\n        place: {x: '0', y: 0, w: 1, h: 1}\n"),
        8,
        20,
        "place.x should be a valid integer",
    ),
    (
        _tile(
            "      - id: t\n        kind: kpi\n        compare: last\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        18,
        "tile 't': compare should be 'previous'",
    ),
    (
        _tile(
            "      - id: t\n        kind: line\n        grain: hour\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        16,
        "tile 't': grain should be 'day', 'week', 'month', 'quarter' or 'year'",
    ),
    (
        _tile(
            f"      - id: t\n        kind: kpi\n        metric: {'m' * 201}\n        place: {{x: 0, y: 0, w: 1, h: 1}}\n"
        ),
        8,
        17,
        "tile 't': metric should have at most 200 characters",
    ),
    (
        _tile(
            "      - id: t\n        kind: kpi\n        title: [a]\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        16,
        "tile 't': title should be a valid string",
    ),
    (
        _tile(
            "      - id: t\n        kind: table\n        rows: 0\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        15,
        "tile 't': rows should be greater than or equal to 1",
    ),
    (
        _tile(
            "      - id: t\n        kind: table\n        rows: 51\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        15,
        "tile 't': rows should be less than or equal to 50",
    ),
    (
        _tile(
            "      - id: t\n        kind: table\n        columns: id\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        18,
        "tile 't': columns should be a valid list",
    ),
    (
        _tile(
            "      - id: t\n        kind: table\n        columns: [id, [x]]\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        23,
        "tile 't': columns[2] should be a valid string",
    ),
    (
        _tile(
            "      - id: t\n        kind: table\n        columns: ["
            + ", ".join(f"c{i}" for i in range(51))
            + "]\n        place: {x: 0, y: 0, w: 1, h: 1}\n"
        ),
        8,
        18,
        "tile 't': columns should have at most 50 items, not 51",
    ),
    (
        _tile(
            f"      - id: t\n        kind: text\n        text: {'x' * 2001}\n        place: {{x: 0, y: 0, w: 1, h: 1}}\n"
        ),
        8,
        15,
        "tile 't': text should have at most 2000 characters",
    ),
]


@pytest.mark.parametrize("text, line, column, message", REFUSED)
def test_refused_by_both_with_the_place_of_the_value(text, line, column, message):
    assert not _schema_ok(text)
    errors = _errors(text)
    assert errors, text
    first = errors[0]
    assert message in first.message, errors
    assert (first.line, first.column) == (line, column), errors
    with pytest.raises(DashboardError):
        read_dashboard(text)


# What the schema cannot say: it accepts these, the checker does not.
BEYOND = [
    (HEADER.replace("0.1.0", "0.2.0"), 1, 23, "written against dashboard spec 0.2.0"),
    (
        HEADER + "pages:\n  - id: p\n    name: P\n  - id: p\n    name: Q\n",
        5,
        9,
        "page id 'p' is used twice (first on line 3); ids must be unique.",
    ),
    (
        HEADER
        + "pages:\n  - id: p\n    name: P\n    tiles:\n"
        + "      - {id: t, kind: kpi, place: {x: 0, y: 0, w: 1, h: 1}}\n"
        + "  - id: q\n    name: Q\n    tiles:\n"
        + "      - {id: t, kind: kpi, place: {x: 0, y: 0, w: 1, h: 1}}\n",
        10,
        14,
        "tile id 't' is used twice (first on line 6); ids must be unique.",
    ),
]


@pytest.mark.parametrize("text, line, column, message", BEYOND)
def test_checks_beyond_the_schema(text, line, column, message):
    assert _schema_ok(text)
    (error,) = _errors(text)
    assert message in error.message
    assert (error.line, error.column) == (line, column)


def test_a_page_and_a_tile_may_share_an_id():
    text = _tile("      - {id: p, kind: kpi, place: {x: 0, y: 0, w: 1, h: 1}}\n")
    assert _errors(text) == []


def test_every_error_of_a_file_is_reported_in_one_pass():
    text = _tile(
        "      - id: t\n        kind: chart\n        rows: 0\n        place: {x: 12, y: 0, w: 0, h: 1}\n"
    )
    messages = [issue.message for issue in _errors(text)]
    assert len(messages) == 4, messages


def test_errors_are_capped():
    text = HEADER + "pages:\n  - id: p\n    name: P\n    tiles:\n" + "      - {}\n" * 60
    errors = _errors(text)
    assert len(errors) == MAX_ISSUES


# ---------------------------------------------------------------------------
# Reading the text
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, line, column, message",
    [
        ("", 1, 1, "The file is empty. A dashboard starts with `model2data-dashboard: 0.1.0`."),
        ("# only a comment\n", 1, 1, "The file is empty."),
        ("- a\n", 1, 1, "A dashboard file is a mapping with `model2data-dashboard`"),
        (HEADER + "title: [a\n", 3, 1, "Not valid YAML:"),
        (HEADER + "title: &t x\n", 2, 8, "anchors (&t) are not allowed"),
        (HEADER + "title: !!str x\n", 2, 8, "tags (!!str) are not allowed"),
        (HEADER + "title: a\ntitle: b\n", 3, 1, "duplicate key 'title', first written on line 2"),
        (HEADER + "<<: {title: x}\n", 2, 1, "merge keys (<<) are not allowed"),
    ],
)
def test_unreadable_files_say_where(text, line, column, message):
    (error,) = check_dashboard(text)
    assert error.is_error
    assert message in error.message
    assert (error.line, error.column) == (line, column)


def test_more_than_one_document_is_refused():
    (error,) = check_dashboard(HEADER + "---\n" + HEADER)
    assert "one YAML document" in error.message


def test_a_file_over_the_size_cap_is_refused():
    text = HEADER + "title: x\n" + "# padding\n" * 25_000
    (error,) = check_dashboard(text)
    assert error.message == "A dashboard file is at most 200 KB; this one is 250 KB."
    assert error.line is None


def test_a_file_that_is_not_utf8_is_refused(tmp_path):
    path = tmp_path / "x.dashboard.yml"
    path.write_bytes(b"model2data-dashboard: 0.1.0\ntitle: \xff\n")
    (error,) = check_dashboard(path)
    assert "is not UTF-8" in error.message


def test_issues_print_with_their_place():
    (error,) = _errors(HEADER + "titel: x\n")
    assert (
        str(error) == "line 2, column 1: the file has unknown key 'titel' (did you mean 'title'?)"
    )
    assert error.path == "titel"
    warning = Issue(3, 4, "m", "warning", "title")
    assert str(warning) == "warning: line 3, column 4: m"


# ---------------------------------------------------------------------------
# Warnings against the model and its metrics
# ---------------------------------------------------------------------------
def _one_tile(fields: str) -> str:
    return _tile(f"      - id: t\n{fields}        place: {{x: 0, y: 0, w: 3, h: 2}}\n")


@pytest.mark.parametrize(
    "fields, line, column, message",
    [
        (
            "        kind: kpi\n        metric: revnue\n",
            8,
            17,
            "tile 't': metric 'revnue' is not a metric of this model (did you mean 'revenue'?).",
        ),
        (
            "        kind: bar\n        metric: revenue\n        by: country\n",
            9,
            13,
            "tile 't': by 'country' should be `table.column`.",
        ),
        (
            "        kind: bar\n        metric: revenue\n        by: customer.country\n",
            9,
            13,
            "tile 't': by 'customer.country' names table 'customer', which is not in this model "
            "(did you mean 'customers'?).",
        ),
        (
            "        kind: stacked\n        metric: orders\n        by: orders.channel\n"
            "        across: customers.contry\n",
            10,
            17,
            "tile 't': across 'customers.contry' is not a column of this model "
            "(did you mean 'customers.country'?).",
        ),
        (
            "        kind: filter\n        dimension: orders.state\n",
            8,
            20,
            "tile 't': dimension 'orders.state' is not a column of this model "
            "(did you mean 'orders.status'?).",
        ),
        (
            "        kind: table\n        table: order\n",
            8,
            16,
            "tile 't': table 'order' is not a table of this model (did you mean 'orders'?).",
        ),
        (
            "        kind: table\n        table: orders\n        columns: [id, totl_amount]\n",
            9,
            23,
            "tile 't': column 'totl_amount' is not a column of 'orders' "
            "(did you mean 'total_amount'?).",
        ),
        (
            "        kind: kpi\n        metric: orders_total_amount\n",
            None,
            None,
            None,
        ),
    ],
)
def test_names_the_model_lacks_are_warnings(fields, line, column, message, coffee):
    text = _one_tile(fields)
    assert _errors(text, coffee) == []
    warnings = _warnings(text, coffee)
    if message is None:  # an inferred metric is a metric of the model
        assert warnings == []
        return
    (warning,) = warnings
    assert warning.message == message
    assert (warning.line, warning.column) == (line, column)
    # Without a model nothing is judged.
    assert _warnings(text) == []
    # And the dashboard reads, with the warning on it.
    assert read_dashboard(text, coffee).warnings == [warning]


def test_a_model_of_another_name_is_a_warning(coffee):
    (warning,) = _warnings(HEADER + "model: coffee_shop\n", coffee)
    assert warning.message == (
        "model is 'coffee_shop', but the dashboard is checked against model 'coffee_webshop'."
    )
    assert (warning.line, warning.column) == (2, 8)


def test_a_tile_past_the_grid_is_a_warning_without_a_model():
    text = _tile("      - id: t\n        kind: kpi\n        place: {x: 10, y: 0, w: 4, h: 2}\n")
    assert _schema_ok(text)
    (warning,) = _warnings(text)
    assert warning.message == (
        "tile 't': place runs past the grid: x + w is 14, and a page is 12 columns wide."
    )
    assert (warning.line, warning.column) == (8, 16)


def test_known_names_includes_inferred_metrics_and_tables(coffee):
    assert {"revenue", "web_share", "orders_total_amount", "order_items_count"} <= set(
        coffee.metrics or ()
    )
    assert "country" in (coffee.tables or {})["customers"]
    assert coffee.model == "coffee_webshop"
    model = load_model(SPEC / "examples" / "coffee_webshop.model2data.yml")
    implied = known_names(model)
    assert "revenue" not in (implied.metrics or ())
    assert "orders_total_amount" in (implied.metrics or ())


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def test_the_example_round_trips(coffee):
    board = read_dashboard(EXAMPLE, coffee)
    text = write_dashboard(board)
    again = read_dashboard(text, coffee)
    assert again == board
    assert write_dashboard(again) == text
    assert _schema_ok(text)
    assert check_dashboard(text, coffee) == []


def test_the_canonical_style():
    board = Dashboard(
        model="shop",
        title="2024",
        pages=(
            Page(
                id="p",
                name="Page: one",
                grain="week",
                tiles=(
                    Tile(
                        id="t",
                        kind="table",
                        place=Place(0, 1, 6, 4),
                        title="Latest",
                        table="orders",
                        columns=("id", "no", "2024"),
                        rows=5,
                    ),
                    Tile(
                        id="note",
                        kind="note",
                        place=Place(6, 1, 6, 3),
                        text="Line one\nLine two\n",
                    ),
                ),
            ),
            Page(id="empty", name="Empty"),
        ),
    )
    text = write_dashboard(board)
    assert text == (
        f"# yaml-language-server: $schema={SCHEMA_URL}\n"
        "model2data-dashboard: 0.1.0\n"
        "model: shop\n"
        'title: "2024"\n'
        "pages:\n"
        "  - id: p\n"
        '    name: "Page: one"\n'
        "    grain: week\n"
        "    tiles:\n"
        "      - id: t\n"
        "        kind: table\n"
        "        title: Latest\n"
        "        table: orders\n"
        '        columns: [id, "no", "2024"]\n'
        "        rows: 5\n"
        "        place: {x: 0, y: 1, w: 6, h: 4}\n"
        "      - id: note\n"
        "        kind: note\n"
        "        text: |\n"
        "          Line one\n"
        "          Line two\n"
        "        place: {x: 6, y: 1, w: 6, h: 3}\n"
        "  - id: empty\n"
        "    name: Empty\n"
        "    tiles: []\n"
    )
    assert read_dashboard(text) == board


def test_a_tile_writes_its_keys_in_the_canonical_order():
    tile = Tile(
        id="t",
        kind="stacked",
        place=Place(0, 0, 1, 1),
        **{key: key for key in ("title", "metric", "comparison", "by", "across", "dimension")},
        compare="previous",
        grain="day",
        table="tb",
        columns=("c",),
        rows=1,
        text="x",
    )
    text = write_dashboard(Dashboard(pages=(Page(id="p", name="P", tiles=(tile,)),)))
    keys = re.findall(r"^ {6}(?:- )?([a-z]+):|^ {8}([a-z]+):", text, flags=re.M)
    written = [a or b for a, b in keys]
    assert written == list(TILE_FIELDS)
    assert read_dashboard(text).pages[0].tiles[0] == tile


@pytest.mark.parametrize(
    "value",
    ["plain", "with: colon", "# hash", "true", "null", "0.1", "  padded", "a\nb", "a\n\n", "'q'"],
)
def test_awkward_text_round_trips(value):
    board = Dashboard(
        title=value if "\n" not in value else None,
        pages=(
            Page(
                id="p",
                name="P",
                tiles=(Tile(id=value, kind="text", place=Place(0, 0, 1, 1), text=value),),
            ),
        ),
    )
    assert read_dashboard(write_dashboard(board)) == board


def test_a_document_is_written_after_it_is_checked():
    document = read_dashboard(EXAMPLE).to_dict()
    assert from_dict(document) == read_dashboard(EXAMPLE)
    assert write_dashboard(document) == write_dashboard(read_dashboard(EXAMPLE))
    with pytest.raises(DashboardError) as raised:
        write_dashboard({"model2data-dashboard": "0.1.0", "pages": [{"id": "p"}]})
    (issue,) = raised.value.issues
    assert issue.path == "pages.0"
    assert issue.line is None
    assert "page 'p' needs a name." in str(raised.value)


# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line, message",
    [
        ("title: 1.5", None),
        ("title: .nan", "title must be text."),
        ("model2data-dashboard: true", "not true."),
        ("model2data-dashboard: '1'", "not '1'."),
        ("model2data-dashboard:", "not null."),
        ("model2data-dashboard: [1]", "not a list."),
        ("model2data-dashboard: {a: 1}", "not a mapping."),
    ],
)
def test_odd_scalars(line, message):
    text = line + "\n" if line.startswith("model2data") else HEADER + line + "\n"
    errors = _errors(text)
    if message is None:
        assert errors == []
        assert read_dashboard(text).title == "1.5"
    else:
        assert message in errors[0].message, errors


def test_a_key_that_is_not_text_is_refused():
    (error,) = check_dashboard(HEADER + "? [a]\n: b\n")
    assert error.message == "A key must be text."
    assert (error.line, error.column) == (2, 3)


def test_a_yaml_error_without_a_place():
    import yaml

    from model2data.dashboards.check import _yaml_issue

    issue = _yaml_issue(yaml.YAMLError("x"))
    assert (issue.line, issue.column) == (None, None)
    assert issue.message.startswith("Not valid YAML:")


def test_an_unknown_key_in_parsed_data_has_a_path_and_no_line():
    with pytest.raises(DashboardError) as raised:
        from_dict({"model2data-dashboard": "0.1.0", "x": 1})
    (issue,) = raised.value.issues
    assert (issue.path, issue.line) == ("x", None)


def test_metrics_known_without_tables():
    text = _one_tile("        kind: bar\n        metric: nope\n        by: a.b\n")
    known = Known(metrics=frozenset({"revenue"}))
    assert [issue.message for issue in _warnings(text, known)] == [
        "tile 't': metric 'nope' is not a metric of this model."
    ]


def test_paths_and_names():
    assert dashboard_name("notes.yml") == "notes"
    assert check_dashboard(str(EXAMPLE)) == []
    assert str(Issue(3, None, "m")) == "line 3: m"


def test_a_dashboard_without_pages_is_written_with_an_empty_list():
    text = write_dashboard(Dashboard())
    assert text.endswith("model2data-dashboard: 0.1.0\npages: []\n")
    assert read_dashboard(text) == Dashboard()
