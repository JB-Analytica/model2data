"""What a dashboard file holds, its limits, and what a check reports.

The limits are the dashboard spec's (`model2data/spec/dashboards/README.md`);
`dashboard.schema.json` states the same numbers, and the tests hold the two
together.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Union

SPEC_VERSION = "0.1.0"
# The minor versions this reader implements.
READS = ("0.1",)
SCHEMA_URL = (
    f"https://www.jbanalytica.com/model2data/spec/dashboards/{SPEC_VERSION}/dashboard.schema.json"
)
VERSION_KEY = "model2data-dashboard"

GRID_COLUMNS = 12
MAX_PAGES = 20
MAX_TILES = 60
MAX_NAME = 200
MAX_TEXT = 2000
MAX_COLUMNS = 50
MAX_TABLE_ROWS = 50
MAX_Y = 1000
MAX_H = 100
# A file is text a person scrolls through: far past any real one, so that a
# reader holding one in memory has an upper bound.
MAX_BYTES = 200_000
# How many errors, and how many warnings, a check reports at most. A file in
# the wrong format would otherwise answer with one per line.
MAX_ISSUES = 50

KINDS: tuple[str, ...] = (
    "kpi",
    "line",
    "bar",
    "stacked",
    "donut",
    "table",
    "filter",
    "text",
    "note",
)
GRAINS: tuple[str, ...] = ("day", "week", "month", "quarter", "year")
COMPARES: tuple[str, ...] = ("previous",)

TOP_FIELDS: tuple[str, ...] = (VERSION_KEY, "model", "title", "pages")
PAGE_FIELDS: tuple[str, ...] = ("id", "name", "grain", "tiles")
PLACE_FIELDS: tuple[str, ...] = ("x", "y", "w", "h")
# A tile's keys, in the order the canonical file writes them.
TILE_FIELDS: tuple[str, ...] = (
    "id",
    "kind",
    "title",
    "metric",
    "comparison",
    "compare",
    "grain",
    "by",
    "across",
    "dimension",
    "table",
    "columns",
    "rows",
    "text",
    "place",
)
# Tile keys that name a column of the model, `table.column`.
COLUMN_FIELDS: tuple[str, ...] = ("by", "across", "dimension")

FILE_SUFFIXES = (".dashboard.yml", ".dashboard.yaml")
NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
NAME_RULE = (
    "A dashboard name is 1 to 64 lowercase letters, digits, '-' or '_', "
    "starting with a letter or digit."
)


@dataclass(frozen=True)
class Issue:
    """One error or warning in a dashboard file.

    `line` and `column` are 1-based, and None when no place in the text can be
    given (the file is too large, or the document was not read from text).
    `path` is the document path of the value at fault, `pages.0.tiles.2.metric`,
    or `""` for the file as a whole. An **error** is a way the file does not
    conform; a **warning** is a name the model or its metrics do not have, or
    a layout a renderer has to correct, in a file that still conforms.
    """

    line: Optional[int]
    column: Optional[int]
    message: str
    severity: Literal["error", "warning"] = "error"
    path: str = ""

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def __str__(self) -> str:
        if self.line is None:
            where = ""
        elif self.column is None:
            where = f"line {self.line}: "
        else:
            where = f"line {self.line}, column {self.column}: "
        text = f"{where}{self.message}"
        return f"warning: {text}" if self.severity == "warning" else text


class DashboardError(ValueError):
    """A file that is not a conforming dashboard; `issues` holds every error,
    `warnings` the warnings found beside them."""

    def __init__(self, issues: list[Issue], warnings: Optional[list[Issue]] = None):
        self.issues = list(issues)
        self.warnings = list(warnings or [])
        count = len(self.issues)
        heading = "1 issue" if count == 1 else f"{count} issues"
        lines = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"The dashboard has {heading}:\n{lines}")


@dataclass(frozen=True)
class Known:
    """What a dashboard's names are checked against. None for a part that is not
    known (no model, say), and then nothing in it is judged.

    `metrics` is every metric name (the metrics file's and the inferred ones),
    `tables` each table key's column names, and `model` the model's name.
    """

    metrics: Optional[frozenset[str]] = None
    tables: Optional[Mapping[str, frozenset[str]]] = None
    model: Optional[str] = None


@dataclass(frozen=True)
class Place:
    """Where a tile sits on its page's grid: column `x`, row `y`, `w` columns
    wide and `h` rows high."""

    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Tile:
    """One tile. Which of the optional fields a kind reads is in the spec."""

    id: str
    kind: str
    place: Place
    title: Optional[str] = None
    metric: Optional[str] = None
    comparison: Optional[str] = None
    compare: Optional[str] = None
    grain: Optional[str] = None
    by: Optional[str] = None
    across: Optional[str] = None
    dimension: Optional[str] = None
    table: Optional[str] = None
    columns: Optional[tuple[str, ...]] = None
    rows: Optional[int] = None
    text: Optional[str] = None


@dataclass(frozen=True)
class Page:
    id: str
    name: str
    grain: Optional[str] = None
    tiles: tuple[Tile, ...] = ()


@dataclass
class Dashboard:
    """A conforming dashboard file, as `read_dashboard` reads it.

    `warnings` holds what the check pointed out; it takes no part in equality,
    so a dashboard written and read back equals the one written.
    """

    version: str = SPEC_VERSION
    model: Optional[str] = None
    title: Optional[str] = None
    pages: tuple[Page, ...] = ()
    warnings: list[Issue] = field(default_factory=list, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """The document as plain data, in the file's shape, without unset fields."""
        document: dict[str, Any] = {VERSION_KEY: self.version}
        if self.model is not None:
            document["model"] = self.model
        if self.title is not None:
            document["title"] = self.title
        document["pages"] = [_page_dict(page) for page in self.pages]
        return document


def _page_dict(page: Page) -> dict[str, Any]:
    out: dict[str, Any] = {"id": page.id, "name": page.name}
    if page.grain is not None:
        out["grain"] = page.grain
    out["tiles"] = [tile_dict(tile) for tile in page.tiles]
    return out


def tile_dict(tile: Tile) -> dict[str, Any]:
    """A tile as plain data, keys in the canonical order, without unset fields."""
    out: dict[str, Any] = {}
    for key in TILE_FIELDS:
        if key == "place":
            place = tile.place
            out["place"] = {"x": place.x, "y": place.y, "w": place.w, "h": place.h}
            continue
        value: Union[str, int, tuple[str, ...], None] = getattr(tile, key)
        if value is not None:
            out[key] = list(value) if isinstance(value, tuple) else value
    return out
