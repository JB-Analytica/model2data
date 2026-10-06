"""Reading a metrics file from a file or from text, and checking it against its model."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional, Union

from model2data.metrics.check import (
    all_metrics,
    check_against,
    check_document,
    check_references,
)
from model2data.metrics.types import (
    KIND_KEYS,
    Condition,
    DimensionOverride,
    Metric,
    Metrics,
    Ratio,
    Where,
)
from model2data.model._yaml import locate_issues, parse_yaml
from model2data.model.errors import Issue, ModelError
from model2data.model.reader import read_text
from model2data.model.types import Model

Source = Union[str, "os.PathLike[str]"]

# The suffixes a metrics file is recognised by, beside `<stem>.model2data.yml`.
SUFFIXES = (".metrics.yml", ".metrics.yaml", ".metrics.json")


class MetricsError(ModelError):
    """A metrics file that does not conform, with every issue found in it.

    A `ModelError`, so code that reports a model's issues reports these the same way.
    """

    def __init__(self, issues: Sequence[Issue], warnings: Sequence[Issue] = ()):
        super().__init__(issues, warnings)
        count = len(self.issues)
        heading = "1 issue" if count == 1 else f"{count} issues"
        lines = "\n".join(f"  - {issue}" for issue in self.issues)
        ValueError.__init__(self, f"The metrics file has {heading}:\n{lines}")


def is_metrics_file(path: Union[str, Path]) -> bool:
    """Whether a file's name says it is a metrics file (`*.metrics.yml`, `.yaml`, `.json`)."""
    return str(path).lower().endswith(SUFFIXES)


def metrics_stem(path: Path) -> str:
    """`coffee.metrics.yml` -> `coffee`."""
    name = path.name
    for suffix in SUFFIXES:
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def sibling_model(path: Path) -> Optional[Path]:
    """The model beside a metrics file, by stem: `coffee.model2data.yml` (or `.yaml`,
    `.json`, or `coffee.dbml`) next to `coffee.metrics.yml`; None when there is none."""
    stem = metrics_stem(path)
    for suffix in (".model2data.yml", ".model2data.yaml", ".model2data.json", ".dbml"):
        candidate = path.with_name(stem + suffix)
        if candidate.is_file():
            return candidate
    return None


def _as_path(source: Source) -> Optional[Path]:
    if isinstance(source, os.PathLike):
        return Path(source)
    if "\n" in source or len(source) > 4096:
        return None
    path = Path(source)
    if path.suffix.lower() in (".yml", ".yaml", ".json") and path.is_file():
        return path
    return None


def _parse(source: Source) -> tuple[str, Any]:
    path = _as_path(source)
    try:
        if path is not None:
            text = read_text(path)
            is_json = path.suffix.lower() == ".json"
        else:
            text = str(source)
            is_json = text.lstrip().startswith("{")
        if is_json:
            try:
                return text, json.loads(text)
            except json.JSONDecodeError as error:
                raise ModelError(
                    [Issue("", f"not valid JSON: {error.msg} (column {error.colno})", error.lineno)]
                ) from None
        return text, parse_yaml(text)
    except ModelError as error:
        raise MetricsError(error.issues) from None


def load(
    source: Source,
    model: Optional[Model] = None,
    *,
    model_name: Optional[str] = None,
) -> Metrics:
    """Read a metrics file from a file, or the text of one, and check it conforms.

    `source` is a path (`coffee.metrics.yml`, `.json`) or the document's text,
    YAML under the model spec's YAML profile, or JSON. With `model`, the file is
    also checked against it: every table and column it names, every filter and
    time path. `model_name` is the name a model without `name` goes by. Raises
    `MetricsError` listing every error; the warnings of a file that conforms are
    on the returned `Metrics.warnings`.
    """
    text, document = _parse(source)
    return from_dict(document, model, model_name=model_name, text=text)


def from_dict(
    document: Any,
    model: Optional[Model] = None,
    *,
    model_name: Optional[str] = None,
    text: Optional[str] = None,
) -> Metrics:
    """The metrics a parsed document describes, as `load` reads them; `text`, when given,
    is the document's source, and puts a line on each issue."""
    issues = check_document(document)
    metrics: Optional[Metrics] = None
    if not any(issue.is_error for issue in issues):
        metrics = _build(document)
        if model is not None:
            issues += check_against(metrics, model, model_name=model_name)
        elif not metrics.infer:
            issues += check_references(metrics, all_metrics(metrics, None))
    if text is not None:
        issues = locate_issues(text, issues)
    errors = [issue for issue in issues if issue.is_error]
    warnings = [issue for issue in issues if not issue.is_error]
    if errors or metrics is None:
        raise MetricsError(errors, warnings)
    metrics.warnings = warnings
    return metrics


def validate(
    source: Source, model: Optional[Model] = None, *, model_name: Optional[str] = None
) -> list[Issue]:
    """Every issue in a metrics file, errors and warnings; no errors means it conforms."""
    try:
        metrics = load(source, model, model_name=model_name)
    except ModelError as error:
        return [*error.issues, *error.warnings]
    return list(metrics.warnings)


# ---------------------------------------------------------------------------
# Building the typed value from a document that passed `check_document`
# ---------------------------------------------------------------------------
def _extensions(mapping: Mapping) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in mapping.items()
        if isinstance(key, str) and key.startswith("x-")
    }


def _build(document: Mapping) -> Metrics:
    return Metrics(
        version=document["model2data-metrics"],
        model=document["model"],
        metrics={name: _metric(metric) for name, metric in (document.get("metrics") or {}).items()},
        dimensions={
            path: _dimension(value) for path, value in (document.get("dimensions") or {}).items()
        },
        infer=document.get("infer", True),
        extensions=_extensions(document),
    )


def _metric(data: Mapping) -> Metric:
    key = next(key for key in KIND_KEYS if key in data)
    ratio = data.get("ratio")
    return Metric(
        kind=KIND_KEYS[key],  # type: ignore[arg-type]
        measure=data.get("measure"),
        agg=data.get("agg"),
        count=data.get("count"),
        ratio=Ratio(ratio["numerator"], ratio["denominator"], _extensions(ratio))
        if ratio is not None
        else None,
        expression=data.get("expression"),
        label=data.get("label"),
        description=data.get("description"),
        format=data.get("format"),
        ai_context=data.get("ai_context"),
        time=data.get("time"),
        where=_where(data["where"]) if "where" in data else None,
        extensions=_extensions(data),
    )


def _where(data: Mapping, any_: bool = False) -> Where:
    terms: list[Union[Condition, Where]] = []
    for key, value in data.items():
        if key in ("any", "all"):
            terms.append(Where([_where(item) for item in value], any=key == "any"))
        elif "." in key:
            terms.append(_condition(key, value))
    return Where(terms, any=any_, extensions=_extensions(data))


def _condition(column: str, value: Any) -> Condition:
    if isinstance(value, list):
        return Condition(column, {"in": list(value)}, written="list")
    if isinstance(value, Mapping):
        operators = {
            key: list(item) if isinstance(item, list) else item
            for key, item in value.items()
            if not key.startswith("x-")
        }
        return Condition(column, operators, extensions=_extensions(value))
    return Condition(column, {"eq": value}, written="value")


def _dimension(value: Any) -> DimensionOverride:
    if isinstance(value, bool):
        return DimensionOverride(offered=value)
    return DimensionOverride(
        offered=True,
        label=value.get("label"),
        description=value.get("description"),
        extensions=_extensions(value),
    )
