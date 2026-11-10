"""Dashboards as files: dashboard spec 0.1.0, read, checked and written.

A dashboard file is `<name>.dashboard.yml`, as `model2data/spec/dashboards/README.md`
and `dashboard.schema.json` define it: pages of tiles on a 12-column grid, each
tile naming a metric, a `table.column` or a table of a model2data model, never a
data source or a value. The engine does not draw a dashboard or compute its
numbers; it reads, checks and writes the file. model2data studio draws it with
the numbers of the model's generated data.

    >>> from model2data import dashboards, metrics
    >>> from model2data.model import load as load_model
    >>> model = load_model("coffee_webshop.model2data.yml")
    >>> found = metrics.load("coffee_webshop.metrics.yml", model)
    >>> known = dashboards.known_names(model, found)        # metrics, tables, columns
    >>> for issue in dashboards.check_dashboard("coffee_webshop_sales.dashboard.yml", known):
    ...     print(issue.line, issue.column, issue.severity, issue.message)
    >>> board = dashboards.read_dashboard("coffee_webshop_sales.dashboard.yml", known)  # DashboardError on errors
    >>> board.pages[0].tiles[0].metric, board.warnings
    >>> text = dashboards.write_dashboard(board)            # the canonical file

An error is a way the file does not conform (bad YAML, an unknown key, a
missing or repeated id, a value out of range). A warning is a name `known`
does not hold, or a tile running past the grid's right edge: the file still
conforms. Every issue has a 1-based `line` and `column` when it was read from
text.
"""

from model2data.dashboards.check import schema
from model2data.dashboards.reader import (
    check_dashboard,
    check_name,
    dashboard_name,
    from_dict,
    is_dashboard_file,
    known_names,
    read_dashboard,
)
from model2data.dashboards.types import (
    GRAINS,
    GRID_COLUMNS,
    KINDS,
    MAX_BYTES,
    MAX_COLUMNS,
    MAX_NAME,
    MAX_PAGES,
    MAX_TABLE_ROWS,
    MAX_TEXT,
    MAX_TILES,
    NAME_RULE,
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
)
from model2data.dashboards.writer import write_dashboard

__all__ = [
    "GRAINS",
    "GRID_COLUMNS",
    "KINDS",
    "MAX_BYTES",
    "MAX_COLUMNS",
    "MAX_NAME",
    "MAX_PAGES",
    "MAX_TABLE_ROWS",
    "MAX_TEXT",
    "MAX_TILES",
    "NAME_RULE",
    "SCHEMA_URL",
    "SPEC_VERSION",
    "TILE_FIELDS",
    "Dashboard",
    "DashboardError",
    "Issue",
    "Known",
    "Page",
    "Place",
    "Tile",
    "check_dashboard",
    "check_name",
    "dashboard_name",
    "from_dict",
    "is_dashboard_file",
    "known_names",
    "read_dashboard",
    "schema",
    "write_dashboard",
]
