"""Writing a dashboard as its `.dashboard.yml` file, in the canonical style.

- The `yaml-language-server` schema comment first, then `model2data-dashboard`,
  `model`, `title` and `pages`.
- A page's keys in the order `id`, `name`, `grain`, `tiles`; a tile's in the
  order `id`, `kind`, `title`, its content fields, `place`
  (`types.TILE_FIELDS`). Unset fields are left out.
- `place` and `columns` in flow style, on one line:
  `place: {x: 0, y: 0, w: 3, h: 2}`.
- A `text` of several lines as a literal (`|`) block.
- A scalar quoted only when a plain one would not read back as the same value
  under the YAML profile (`"2024"` for a title that is the text 2024).

The writer starts from the document, not from a file's text, so it keeps no
comments: a tool that wants to keep a person's comments edits the text instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Union

from model2data.dashboards.types import (
    SCHEMA_URL,
    TILE_FIELDS,
    VERSION_KEY,
    Dashboard,
    DashboardError,
    Page,
    Tile,
)
from model2data.model._yaml import scalar
from model2data.model.dump import _block_scalar

_INDENT = "  "


def write_dashboard(dashboard: Union[Dashboard, Mapping[str, Any]]) -> str:
    """The canonical file for a dashboard, or for a document in the file's shape.

    A document (a mapping) is checked first and raises `DashboardError` when it
    does not conform. `read_dashboard(write_dashboard(d)) == d`.
    """
    if not isinstance(dashboard, Dashboard):
        from model2data.dashboards.check import check_data

        issues, checked = check_data(dashboard)
        if checked is None:
            errors = [issue for issue in issues if issue.is_error]
            raise DashboardError(errors, [issue for issue in issues if not issue.is_error])
        dashboard = checked
    lines = [
        f"# yaml-language-server: $schema={SCHEMA_URL}",
        f"{VERSION_KEY}: {scalar(dashboard.version)}",
    ]
    if dashboard.model is not None:
        lines.append(f"model: {scalar(dashboard.model)}")
    if dashboard.title is not None:
        lines += _entry("title", dashboard.title, 0)
    if not dashboard.pages:
        lines.append("pages: []")
    else:
        lines.append("pages:")
        for page in dashboard.pages:
            lines += _page(page)
    return "\n".join(lines) + "\n"


def _page(page: Page) -> list[str]:
    lines = [f"{_INDENT}- id: {scalar(page.id)}"]
    lines += _entry("name", page.name, 2)
    if page.grain is not None:
        lines.append(f"{_INDENT * 2}grain: {page.grain}")
    if not page.tiles:
        lines.append(f"{_INDENT * 2}tiles: []")
        return lines
    lines.append(f"{_INDENT * 2}tiles:")
    for tile in page.tiles:
        entries: list[str] = []
        for key in TILE_FIELDS:
            entries += _tile_entry(tile, key)
        lines.append(f"{_INDENT * 3}- {entries[0].lstrip()}")
        lines += entries[1:]
    return lines


def _tile_entry(tile: Tile, key: str) -> list[str]:
    level = 4
    pad = _INDENT * level
    if key == "place":
        place = tile.place
        return [f"{pad}place: {{x: {place.x}, y: {place.y}, w: {place.w}, h: {place.h}}}"]
    value = getattr(tile, key)
    if value is None:
        return []
    if key == "columns":
        return [f"{pad}columns: [{', '.join(scalar(name, flow=True) for name in value)}]"]
    return _entry(key, value, level)


def _entry(key: str, value: Any, level: int) -> list[str]:
    pad = _INDENT * level
    if isinstance(value, str) and "\n" in value:
        block = _block_scalar(value, level + 1)
        if block is not None:
            return [f"{pad}{key}: {block[0]}", *block[1:]]
    return [f"{pad}{key}: {scalar(value)}"]
