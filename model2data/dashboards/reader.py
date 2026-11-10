"""Reading a dashboard file, checking it, and what its names are checked against."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from model2data.dashboards.check import check_data, check_text
from model2data.dashboards.types import (
    FILE_SUFFIXES,
    NAME_PATTERN,
    NAME_RULE,
    Dashboard,
    DashboardError,
    Issue,
    Known,
)

if TYPE_CHECKING:
    from model2data.metrics.types import Metrics
    from model2data.model.types import Model

Source = Union[str, "os.PathLike[str]"]

_NAME = re.compile(NAME_PATTERN)


def is_dashboard_file(path: Union[str, Path]) -> bool:
    """Whether a file's name says it is a dashboard (`*.dashboard.yml` or `.yaml`)."""
    return str(path).lower().endswith(FILE_SUFFIXES)


def dashboard_name(path: Union[str, Path]) -> str:
    """The dashboard's name: its file name without `.dashboard.yml`
    (`coffee_webshop_sales.dashboard.yml` -> `coffee_webshop_sales`). `check_name` says
    whether it is a valid one."""
    name = Path(path).name
    for suffix in FILE_SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return Path(path).stem


def check_name(name: str) -> Optional[str]:
    """Why `name` cannot name a dashboard, or None when it can."""
    return None if _NAME.match(name) else NAME_RULE


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        message = f"{path.name} is not UTF-8 (byte {error.start} cannot be decoded)."
        raise DashboardError([Issue(None, None, message)]) from None


def _text_of(source: Source) -> str:
    if isinstance(source, os.PathLike):
        return _read(Path(source))
    if "\n" not in source and len(source) <= 4096:
        path = Path(source)
        if path.suffix.lower() in (".yml", ".yaml") and path.is_file():
            return _read(path)
    return str(source)


def check_dashboard(source: Source, known: Optional[Known] = None) -> list[Issue]:
    """Every error and warning in a dashboard file, in the order they sit in it.

    `source` is a path (`sales.dashboard.yml`) or the file's text. With `known`,
    a metric, a `table.column` or a table it does not hold is a warning. No
    errors means the file conforms.
    """
    try:
        text = _text_of(source)
    except DashboardError as error:
        return error.issues
    issues, _ = check_text(text, known)
    return issues


def read_dashboard(source: Source, known: Optional[Known] = None) -> Dashboard:
    """The dashboard in a file, or the text of one; `DashboardError` listing every
    error when it does not conform. Its warnings are on `Dashboard.warnings`."""
    issues, dashboard = check_text(_text_of(source), known)
    return _result(issues, dashboard)


def from_dict(document: Any, known: Optional[Known] = None) -> Dashboard:
    """The dashboard a parsed document (the file's shape as plain data) describes, as
    `read_dashboard` reads it. Its issues carry a path and no line."""
    issues, dashboard = check_data(document, known)
    return _result(issues, dashboard)


def _result(issues: list[Issue], dashboard: Optional[Dashboard]) -> Dashboard:
    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    if errors or dashboard is None:
        raise DashboardError(errors, warnings)
    dashboard.warnings = warnings
    return dashboard


def known_names(
    model: Model, metrics: Optional[Metrics] = None, *, model_name: Optional[str] = None
) -> Known:
    """What a dashboard for `model` may name: its tables and their columns, and every
    metric, the metrics file's (`metrics`, as `model2data.metrics.load` read it
    against `model`) and the ones the model implies. `model_name` is the name a
    model without `name` goes by (its file's stem)."""
    from model2data.metrics import resolve

    semantic = resolve(model, metrics, model_name=model_name)
    return Known(
        metrics=frozenset(semantic.metrics),
        tables={key: frozenset(table.columns) for key, table in model.tables.items()},
        model=model.name or model_name,
    )
