"""Does a dashboard file conform, and do its names exist: the checks of the dashboard spec.

`check_text(text, known)` reads the text under the model spec's YAML profile,
keeping the line and column of every key and value, and checks the document;
`check_data(document, known)` checks an already parsed document, whose issues
then have a path but no line. Both return every error and warning, and the
`Dashboard` when there is no error.

What is checked is what `dashboard.schema.json` says (types, ranges, lengths,
the kinds and grains, unknown keys) plus what it cannot say: a version this
reader implements, page ids unique in the file and tile ids unique across all
pages, and the size of the file. Against a `Known`, a metric, a
`table.column` or a table it does not have is a warning, never an error. A
tile that runs past the grid's right edge is a warning too.
"""

from __future__ import annotations

import difflib
import json
import math
from collections.abc import Iterable, Mapping
from functools import lru_cache
from importlib import resources
from typing import Any, Optional, Union

import yaml
from yaml.composer import ComposerError
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from model2data.dashboards.types import (
    COLUMN_FIELDS,
    COMPARES,
    GRAINS,
    GRID_COLUMNS,
    KINDS,
    MAX_BYTES,
    MAX_COLUMNS,
    MAX_H,
    MAX_ISSUES,
    MAX_NAME,
    MAX_PAGES,
    MAX_TABLE_ROWS,
    MAX_TEXT,
    MAX_TILES,
    MAX_Y,
    PAGE_FIELDS,
    PLACE_FIELDS,
    READS,
    SPEC_VERSION,
    TILE_FIELDS,
    TOP_FIELDS,
    VERSION_KEY,
    Dashboard,
    Issue,
    Known,
    Page,
    Place,
    Tile,
)
from model2data.model._yaml import _CoreLoader, _ProfileError
from model2data.model.errors import format_path

PathKey = tuple[Union[str, int], ...]
Position = tuple[Optional[int], Optional[int]]
_NOWHERE: Position = (None, None)


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    """The packaged, normative JSON Schema of the dashboard spec."""
    text = (
        resources.files("model2data")
        .joinpath("spec/dashboards/dashboard.schema.json")
        .read_text("utf-8")
    )
    return json.loads(text)


# ---------------------------------------------------------------------------
# Reading text with a place for every node
# ---------------------------------------------------------------------------
class _Located:
    """Where each value, and each mapping key, of a document sits in its text."""

    def __init__(self) -> None:
        self.values: dict[PathKey, Position] = {}
        self.keys: dict[PathKey, Position] = {}


class _Unreadable(Exception):
    def __init__(self, issues: list[Issue]):
        super().__init__(issues)
        self.issues = issues


def _mark(node: Node) -> tuple[int, int]:
    return node.start_mark.line + 1, node.start_mark.column + 1


def _load(text: str) -> tuple[Any, _Located]:
    """The document in `text` and where its nodes are, or `_Unreadable`."""
    located = _Located()
    loader = _CoreLoader(text)
    try:
        try:
            node = loader.get_single_node()
        except _ProfileError as error:
            raise _Unreadable([Issue(error.line, error.column, f"{error.message}.")]) from None
        except ComposerError as error:
            if "single document" in str(error):
                message = "A dashboard is one YAML document; this file holds more than one."
                raise _Unreadable([Issue(None, None, message)]) from None
            raise _Unreadable([_yaml_issue(error)]) from None
        except yaml.YAMLError as error:
            raise _Unreadable([_yaml_issue(error)]) from None
        if node is None:
            return None, located
        issues: list[Issue] = []
        data = _build(loader, node, (), located, issues)
        if issues:
            raise _Unreadable(issues)
        return data, located
    finally:
        loader.dispose()


def _yaml_issue(error: yaml.YAMLError) -> Issue:
    mark = getattr(error, "problem_mark", None) or getattr(error, "context_mark", None)
    problem = getattr(error, "problem", None) or getattr(error, "context", None)
    message = f"Not valid YAML: {problem or 'this is not valid YAML'}."
    if mark is None:
        return Issue(None, None, message)
    return Issue(mark.line + 1, mark.column + 1, message)


def _build(
    loader: _CoreLoader, node: Node, path: PathKey, located: _Located, issues: list[Issue]
) -> Any:
    located.values[path] = _mark(node)
    if isinstance(node, MappingNode):
        out: dict[str, Any] = {}
        first: dict[str, int] = {}
        for key_node, value_node in node.value:
            line, column = _mark(key_node)
            where = format_path(path)
            if not isinstance(key_node, ScalarNode):
                issues.append(Issue(line, column, "A key must be text.", path=where))
                continue
            key = str(key_node.value)
            if key == "<<" and key_node.style is None:
                message = "merge keys (<<) are not allowed: write the keys out."
                issues.append(Issue(line, column, message, path=where))
                continue
            if key in first:
                message = (
                    f"duplicate key {key!r}, first written on line {first[key]}: a key may "
                    "appear only once in a mapping."
                )
                issues.append(Issue(line, column, message, path=format_path([*path, key])))
                continue
            first[key] = line
            located.keys[(*path, key)] = (line, column)
            out[key] = _build(loader, value_node, (*path, key), located, issues)
        return out
    if isinstance(node, SequenceNode):
        return [
            _build(loader, item, (*path, index), located, issues)
            for index, item in enumerate(node.value)
        ]
    return loader.construct_object(node, deep=True)


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------
def _quoted(value: Any) -> str:
    return f"'{value}'"


def _did_you_mean(word: str, choices: Iterable[str]) -> str:
    close = difflib.get_close_matches(word, list(choices), n=1, cutoff=0.6)
    return f" (did you mean {_quoted(close[0])}?)" if close else ""


def _one_of(choices: Iterable[str]) -> str:
    quoted = [_quoted(choice) for choice in choices]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _as_text(value: Any) -> Any:
    """A scalar meant as text, as text: `title: 2024` is the string "2024"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return value
        return str(value)
    return value


def _show(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return repr(value)
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return "a mapping" if isinstance(value, dict) else "a list"
    return str(value)


Checked = tuple[Any, Optional[str]]


def _name(value: Any, limit: int = MAX_NAME) -> Checked:
    value = _as_text(value)
    if not isinstance(value, str):
        return None, "should be a valid string"
    if len(value) > limit:
        return None, f"should have at most {limit} characters"
    return value, None


def _choice(value: Any, choices: tuple[str, ...]) -> Checked:
    if isinstance(value, str) and value in choices:
        return value, None
    return None, f"should be {_one_of(choices)}"


def _integer(value: Any, low: int, high: int, *, below: bool = False) -> Checked:
    """A whole number from `low` to `high` (`below`: up to but not including `high`)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "should be a valid integer"
    if isinstance(value, float):
        if not value.is_integer():
            return None, "should be a valid integer"
        value = int(value)
    if value < low:
        return None, f"should be greater than or equal to {low}"
    if below and value >= high:
        return None, f"should be less than {high}"
    if not below and value > high:
        return None, f"should be less than or equal to {high}"
    return value, None


# Each optional tile field and how its value is checked, in the order a check reports them.
_NAME_FIELDS = ("title", "metric", "comparison", "by", "across", "dimension", "table")


def _place_value(key: str, value: Any) -> Checked:
    if key == "x":
        return _integer(value, 0, GRID_COLUMNS, below=True)
    if key == "y":
        return _integer(value, 0, MAX_Y)
    if key == "w":
        return _integer(value, 1, GRID_COLUMNS)
    return _integer(value, 1, MAX_H)


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------
class _Checker:
    def __init__(self, located: Optional[_Located], known: Optional[Known]) -> None:
        self.located = located
        self.known = known
        self.errors: list[Issue] = []
        self.warnings: list[Issue] = []
        self.page_ids: dict[str, Optional[int]] = {}
        self.tile_ids: dict[str, Optional[int]] = {}

    # Where -----------------------------------------------------------------

    def at(self, path: PathKey) -> Position:
        """Where the value at `path` is, or the nearest value holding it."""
        if self.located is None:
            return _NOWHERE
        while True:
            if path in self.located.values:
                return self.located.values[path]
            if not path:
                return _NOWHERE
            path = path[:-1]

    def at_key(self, path: PathKey) -> Position:
        if self.located is not None and path in self.located.keys:
            return self.located.keys[path]
        return self.at(path)

    def error(self, path: PathKey, message: str, *, key: bool = False) -> None:
        line, column = self.at_key(path) if key else self.at(path)
        self.errors.append(Issue(line, column, message, "error", format_path(path)))

    def warn(self, path: PathKey, message: str) -> None:
        line, column = self.at(path)
        self.warnings.append(Issue(line, column, message, "warning", format_path(path)))

    def unknown_keys(
        self, node: Mapping, path: PathKey, allowed: tuple[str, ...], where: str
    ) -> None:
        for key in node:
            if key not in allowed:
                self.error(
                    (*path, key),
                    f"{where} has unknown key {_quoted(key)}{_did_you_mean(str(key), allowed)}",
                    key=True,
                )

    def unique(self, seen: dict[str, Optional[int]], value: str, path: PathKey, what: str) -> None:
        if value in seen:
            first = seen[value]
            on = f" (first on line {first})" if first is not None else ""
            self.error(path, f"{what} id {_quoted(value)} is used twice{on}; ids must be unique.")
        else:
            seen[value] = self.at(path)[0]

    # The file ----------------------------------------------------------------

    def document(self, data: Any) -> Optional[Dashboard]:
        if data is None:
            self.errors.append(
                Issue(
                    1,
                    1,
                    f"The file is empty. A dashboard starts with `{VERSION_KEY}: {SPEC_VERSION}`.",
                )
            )
            return None
        if not isinstance(data, Mapping):
            line, column = self.at(())
            self.errors.append(
                Issue(
                    line or 1,
                    column or 1,
                    f"A dashboard file is a mapping with `{VERSION_KEY}`, an optional `model` "
                    "and `title`, and `pages`.",
                )
            )
            return None
        self.unknown_keys(data, (), TOP_FIELDS, "the file")

        version = data.get(VERSION_KEY)
        if VERSION_KEY not in data:
            self.error((), f"`{VERSION_KEY}: {SPEC_VERSION}` is required.")
        else:
            self.version(version)

        model = data.get("model")
        if model is not None:
            if not isinstance(model, str):
                self.error(
                    ("model",), f"model must be the model's name, as text, not {_show(model)}."
                )
                model = None
            elif not model:
                self.error(("model",), "model must not be empty.")
                model = None

        title = _as_text(data.get("title"))
        if title is not None:
            if not isinstance(title, str):
                self.error(("title",), "title must be text.")
                title = None
            elif len(title) > MAX_NAME:
                self.error(("title",), f"title is at most {MAX_NAME} characters.")

        pages_node = data.get("pages")
        pages: list[Page] = []
        if pages_node is not None:
            if not isinstance(pages_node, list):
                self.error(("pages",), "pages must be a list of pages.")
            elif len(pages_node) > MAX_PAGES:
                self.error(
                    ("pages",),
                    f"A dashboard has at most {MAX_PAGES} pages; this one has {len(pages_node)}.",
                )
            else:
                for index, page in enumerate(pages_node):
                    read = self.page(page, index)
                    if read is not None:
                        pages.append(read)

        if self.known is not None and isinstance(model, str) and self.known.model is not None:
            if model != self.known.model:
                self.warn(
                    ("model",),
                    f"model is {_quoted(model)}, but the dashboard is checked against model "
                    f"{_quoted(self.known.model)}.",
                )

        if self.errors:
            return None
        return Dashboard(
            version=str(version),
            model=model,
            title=title or None,
            pages=tuple(pages),
        )

    def version(self, version: Any) -> None:
        path = (VERSION_KEY,)
        parts = version.split(".") if isinstance(version, str) else []
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            self.error(
                path,
                f"{VERSION_KEY} must be a version such as {SPEC_VERSION}, not {_show(version)}.",
            )
        elif ".".join(parts[:2]) not in READS:
            self.error(
                path,
                f"the file is written against dashboard spec {version}, and this reader "
                f"implements dashboard spec {SPEC_VERSION} (0.1.x). Upgrade model2data to read it.",
            )

    # A page ------------------------------------------------------------------

    def page(self, node: Any, index: int) -> Optional[Page]:
        path: PathKey = ("pages", index)
        if not isinstance(node, Mapping):
            self.error(path, f"page {index + 1} must be a mapping with id, name and tiles.")
            return None
        page_id = _as_text(node.get("id"))
        where = f"page {_quoted(page_id)}" if isinstance(page_id, str) else f"page {index + 1}"
        self.unknown_keys(node, path, PAGE_FIELDS, where)
        failed = len(self.errors)

        if page_id is None:
            self.error(path, f"{where} needs an id. Links and comments anchor on page ids.")
        elif isinstance(page_id, str):
            self.unique(self.page_ids, page_id, (*path, "id"), "page")
        if node.get("name") is None:
            self.error(path, f"{where} needs a name.")

        values: dict[str, Any] = {}
        for key in ("id", "name", "grain"):
            if node.get(key) is None:
                continue
            if key == "grain":
                value, problem = _choice(node[key], GRAINS)
            else:
                value, problem = _name(node[key])
            if problem:
                self.error((*path, key), f"{where}: {key} {problem}")
            values[key] = value

        tiles_node = node.get("tiles")
        tiles: list[Tile] = []
        if tiles_node is not None:
            if not isinstance(tiles_node, list):
                self.error((*path, "tiles"), f"{where}: tiles must be a list of tiles.")
            elif len(tiles_node) > MAX_TILES:
                self.error(
                    (*path, "tiles"),
                    f"{where} has {len(tiles_node)} tiles; a page has at most {MAX_TILES}.",
                )
            else:
                for number, tile in enumerate(tiles_node):
                    read = self.tile(tile, (*path, "tiles", number), number, where)
                    if read is not None:
                        tiles.append(read)

        if len(self.errors) > failed:
            return None
        return Page(
            id=values["id"], name=values["name"], grain=values.get("grain"), tiles=tuple(tiles)
        )

    # A tile ------------------------------------------------------------------

    def tile(self, node: Any, path: PathKey, index: int, page: str) -> Optional[Tile]:
        if not isinstance(node, Mapping):
            self.error(
                path, f"tile {index + 1} on {page} must be a mapping with id, kind and place."
            )
            return None
        tile_id = _as_text(node.get("id"))
        where = (
            f"tile {_quoted(tile_id)}"
            if isinstance(tile_id, str)
            else f"tile {index + 1} on {page}"
        )
        failed = len(self.errors)
        self.unknown_keys(node, path, TILE_FIELDS, where)

        for key in ("id", "kind", "place"):
            if node.get(key) is None:
                hint = " Links and comments anchor on tile ids." if key == "id" else ""
                self.error(path, f"{where} needs {'an' if key == 'id' else 'a'} {key}.{hint}")
        if isinstance(tile_id, str):
            self.unique(self.tile_ids, tile_id, (*path, "id"), "tile")

        values: dict[str, Any] = {}
        # Every field is checked even after an error, so one pass reports them all.
        if node.get("id") is not None:
            values["id"] = self.field(path, where, "id", _name(node["id"]))
        if node.get("kind") is not None:
            values["kind"] = self.field(path, where, "kind", _choice(node["kind"], KINDS))

        place = node.get("place")
        if place is not None:
            if not isinstance(place, Mapping):
                self.error((*path, "place"), f"{where}: place must be {{x: 0, y: 0, w: 3, h: 2}}.")
            else:
                self.unknown_keys(place, (*path, "place"), PLACE_FIELDS, f"{where}: place")
                for key in PLACE_FIELDS:
                    if key not in place:
                        self.error((*path, "place"), f"{where}: place needs {key}.")
                    else:
                        value, problem = _place_value(key, place[key])
                        if problem:
                            self.error((*path, "place", key), f"{where}: place.{key} {problem}")
                        values[key] = value

        for key in TILE_FIELDS:
            if key in ("id", "kind", "place") or node.get(key) is None:
                continue
            raw = node[key]
            if key in _NAME_FIELDS:
                values[key] = self.field(path, where, key, _name(raw))
            elif key == "compare":
                values[key] = self.field(path, where, key, _choice(raw, COMPARES))
            elif key == "grain":
                values[key] = self.field(path, where, key, _choice(raw, GRAINS))
            elif key == "rows":
                values[key] = self.field(path, where, key, _integer(raw, 1, MAX_TABLE_ROWS))
            elif key == "text":
                values[key] = self.field(path, where, key, _name(raw, MAX_TEXT))
            elif key == "columns":
                values[key] = self.columns(path, where, raw)

        if len(self.errors) > failed:
            return None
        tile = Tile(
            id=values["id"],
            kind=values["kind"],
            place=Place(values["x"], values["y"], values["w"], values["h"]),
            **{key: values.get(key) for key in _NAME_FIELDS},
            compare=values.get("compare"),
            grain=values.get("grain"),
            columns=values.get("columns"),
            rows=values.get("rows"),
            text=values.get("text"),
        )
        self.tile_warnings(path, where, tile)
        return tile

    def field(self, path: PathKey, where: str, key: str, checked: Checked) -> Any:
        value, problem = checked
        if problem:
            self.error((*path, key), f"{where}: {key} {problem}")
        return value

    def columns(self, path: PathKey, where: str, raw: Any) -> Optional[tuple[str, ...]]:
        if not isinstance(raw, list):
            self.error((*path, "columns"), f"{where}: columns should be a valid list")
            return None
        if len(raw) > MAX_COLUMNS:
            self.error(
                (*path, "columns"),
                f"{where}: columns should have at most {MAX_COLUMNS} items, not {len(raw)}",
            )
            return None
        names: list[str] = []
        for index, item in enumerate(raw):
            value, problem = _name(item)
            if problem:
                self.error((*path, "columns", index), f"{where}: columns[{index + 1}] {problem}")
            else:
                names.append(value)
        return tuple(names)

    # Warnings ----------------------------------------------------------------

    def tile_warnings(self, path: PathKey, where: str, tile: Tile) -> None:
        place = tile.place
        if place.x + place.w > GRID_COLUMNS:
            self.warn(
                (*path, "place"),
                f"{where}: place runs past the grid: x + w is {place.x + place.w}, and a page "
                f"is {GRID_COLUMNS} columns wide.",
            )
        known = self.known
        if known is None:
            return
        if tile.metric and known.metrics is not None and tile.metric not in known.metrics:
            self.warn(
                (*path, "metric"),
                f"{where}: metric {_quoted(tile.metric)} is not a metric of this model"
                f"{_did_you_mean(tile.metric, sorted(known.metrics))}.",
            )
        tables = known.tables
        if tables is None:
            return
        for key in COLUMN_FIELDS:
            value = getattr(tile, key)
            if value:
                problem = _column_problem(value, tables)
                if problem:
                    self.warn((*path, key), f"{where}: {key} {problem}.")
        if tile.table:
            if tile.table not in tables:
                self.warn(
                    (*path, "table"),
                    f"{where}: table {_quoted(tile.table)} is not a table of this model"
                    f"{_did_you_mean(tile.table, tables)}.",
                )
            else:
                columns = tables[tile.table]
                for index, name in enumerate(tile.columns or ()):
                    if name not in columns:
                        self.warn(
                            (*path, "columns", index),
                            f"{where}: column {_quoted(name)} is not a column of "
                            f"{_quoted(tile.table)}{_did_you_mean(name, sorted(columns))}.",
                        )


def _column_problem(path: str, tables: Mapping[str, frozenset[str]]) -> Optional[str]:
    """Why `table.column` is not a column of the model, or None when it is."""
    table, _, column = path.rpartition(".")
    if not table:
        return f"{_quoted(path)} should be `table.column`"
    if table not in tables:
        return f"{_quoted(path)} names table {_quoted(table)}, which is not in this model" + (
            _did_you_mean(table, tables)
        )
    if column not in tables[table]:
        return f"{_quoted(path)} is not a column of this model" + _did_you_mean(
            path, sorted(f"{table}.{name}" for name in tables[table])
        )
    return None


def _ordered(errors: list[Issue], warnings: list[Issue]) -> list[Issue]:
    """At most `MAX_ISSUES` of each, in the order they sit in the file."""
    issues = [*errors[:MAX_ISSUES], *warnings[:MAX_ISSUES]]
    return sorted(
        issues,
        key=lambda issue: (
            issue.line is None,
            issue.line or 0,
            issue.column or 0,
        ),
    )


def check_data(
    document: Any, known: Optional[Known] = None
) -> tuple[list[Issue], Optional[Dashboard]]:
    """Every issue in a parsed document, with paths and no lines, and the dashboard
    when there is no error."""
    checker = _Checker(None, known)
    dashboard = checker.document(document)
    return _ordered(checker.errors, checker.warnings), dashboard


def check_text(text: str, known: Optional[Known] = None) -> tuple[list[Issue], Optional[Dashboard]]:
    """Every issue in a dashboard file's text, each with its line and column, and the
    dashboard when there is no error."""
    size = len(text.encode("utf-8"))
    if size > MAX_BYTES:
        message = (
            f"A dashboard file is at most {MAX_BYTES // 1000} KB; this one is {size // 1000} KB."
        )
        return [Issue(None, None, message)], None
    try:
        data, located = _load(text)
    except _Unreadable as error:
        return error.issues[:MAX_ISSUES], None
    checker = _Checker(located, known)
    dashboard = checker.document(data)
    return _ordered(checker.errors, checker.warnings), dashboard


def declared_model(text: str) -> Optional[str]:
    """The `model` a dashboard file names, when it reads and names one."""
    try:
        data, _ = _load(text)
    except _Unreadable:
        return None
    model = data.get("model") if isinstance(data, Mapping) else None
    return model if isinstance(model, str) and model else None
