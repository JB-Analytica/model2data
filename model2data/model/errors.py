"""What is wrong with a document, and where."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal, Union

PathPart = Union[str, int]


def format_path(parts: Iterable[PathPart]) -> str:
    """`("tables", "orders", "keys", 0)` -> `tables.orders.keys.0`."""
    return ".".join(str(part) for part in parts)


@dataclass(frozen=True)
class Issue:
    """One thing wrong with a document: where it is, and what is wrong with it.

    `path` is the document path of the offending value,
    `tables.orders.columns.status.generate.weights`, or `""` for the document
    as a whole (it is not YAML, it is empty). `line` is set when the problem was
    found while reading YAML, before there was a document to have paths in.

    `severity` is `"error"` for a way the document does not conform, and
    `"warning"` for one the spec asks a reader to point out in a document that
    still conforms (a reference onto a column that is not a key).
    """

    path: str
    message: str
    line: Union[int, None] = None
    severity: Literal["error", "warning"] = "error"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"

    def __str__(self) -> str:
        line = f"line {self.line}" if self.line else ""
        where = f"{self.path} ({line})" if self.path and line else self.path or line
        text = f"{where}: {self.message}" if where else self.message
        return f"warning: {text}" if self.severity == "warning" else text


class ModelError(ValueError):
    """A document that does not conform to the spec, with every issue found in it.

    `issues` holds the errors, which are why the document does not conform;
    `warnings` the warnings found beside them.
    """

    def __init__(self, issues: Sequence[Issue], warnings: Sequence[Issue] = ()):
        self.issues: list[Issue] = list(issues)
        self.warnings: list[Issue] = list(warnings)
        count = len(self.issues)
        heading = "1 issue" if count == 1 else f"{count} issues"
        lines = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"The model has {heading}:\n{lines}")
