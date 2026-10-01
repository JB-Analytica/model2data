"""Reading a model from a file or from text."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional, Union

from model2data.model._yaml import parse_yaml
from model2data.model.document import from_dict
from model2data.model.errors import Issue, ModelError
from model2data.model.types import Model

Format = Literal["yaml", "json", "dbml"]
Source = Union[str, "os.PathLike[str]"]

_SUFFIX_FORMATS: dict[str, Format] = {
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".dbml": "dbml",
}


def _as_path(source: Source) -> Optional[Path]:
    """`source` as a file to read, or None when it is the document's text itself.

    A path object is always a path. A string is a path when it is one line
    naming an existing file with a model suffix; anything else is text.
    """
    if isinstance(source, os.PathLike):
        return Path(source)
    if "\n" in source or len(source) > 4096:
        return None
    path = Path(source)
    try:
        if path.suffix.lower() in _SUFFIX_FORMATS and path.is_file():
            return path
    except OSError:
        return None
    return None


def format_of(path: Path) -> Format:
    """The format a file's name says it is in: `.json`, `.dbml`, else YAML."""
    return _SUFFIX_FORMATS.get(path.suffix.lower(), "yaml")


def read_text(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ModelError(
            [Issue("", f"{path.name} is not UTF-8 (byte {error.start} cannot be decoded)")]
        ) from None


def load(source: Source, *, format: Optional[Format] = None) -> Model:
    """Read a model from a file, or from the text of one, and check it conforms.

    `source` is a path (`orders.model2data.yml`, `orders.json`, `orders.dbml`)
    or the document's text. `format` is `"yaml"`, `"json"` or `"dbml"`; left
    out, a file's suffix decides (YAML unless `.json` or `.dbml`), and text is
    JSON when it starts with `{` and YAML otherwise. YAML is read under the
    spec's YAML profile. Raises `ModelError` listing every error found; the
    warnings of a document that conforms are on the model's `warnings`.
    """
    path = _as_path(source)
    if path is not None:
        text = read_text(path)
        format = format or format_of(path)
    else:
        text = str(source)
        if format is None:
            format = "json" if text.lstrip().startswith("{") else "yaml"
    if format == "dbml":
        from model2data.model.dbml import from_dbml

        return from_dbml(text)
    if format == "json":
        return from_dict(_parse_json(text))
    return from_dict(parse_yaml(text))


def _parse_json(text: str):
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelError(
            [Issue("", f"not valid JSON: {error.msg} (column {error.colno})", error.lineno)]
        ) from None


def validate(source: Source, *, format: Optional[Format] = None) -> list[Issue]:
    """Every issue in `source`, errors and warnings; no errors means it conforms.

    An `Issue`'s `severity` says which it is. `load` raises on the errors and
    keeps the warnings on the model it returns (`Model.warnings`).
    """
    try:
        model = load(source, format=format)
    except ModelError as error:
        return [*error.issues, *error.warnings]
    return list(model.warnings)
