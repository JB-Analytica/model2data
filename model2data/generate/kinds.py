"""What kind of values a column type holds, as spec 0.2.0 defines it.

One place answers "is this type numeric / an integer / temporal / boolean" for
the generator, the hint validation and the model reader, so a hint the model
accepts always lands on a generator branch that reads it. The rules are the
spec's (model2data/spec/README.md, "Generation hints"):

- the **base type** is the type lower-cased, cut at the first `(`, trimmed;
- **integer**: the base type contains `int`;
- **decimal**: it contains `decimal`, `numeric`, `float`, `double` or `real`,
  or is `money` or `number`;
- **numeric**: integer or decimal;
- **boolean**: it contains `bool`;
- **temporal**: it contains `date` or `timestamp` (`time` alone is not):
  a timestamp when it contains `timestamp`, or both `date` and `time`
  (`datetime`), and otherwise a date.
"""

from __future__ import annotations

from typing import Literal, Optional

_DECIMAL_WORDS = ("decimal", "numeric", "float", "double", "real")
_DECIMAL_NAMES = frozenset({"money", "number"})


def base_type(data_type: str) -> str:
    """The type lower-cased, cut at the first `(`, and trimmed: `NUMERIC(10,2)` -> `numeric`."""
    return data_type.lower().split("(")[0].strip()


def is_integer_type(data_type: str) -> bool:
    return "int" in base_type(data_type)


def is_decimal_type(data_type: str) -> bool:
    base = base_type(data_type)
    return not is_integer_type(data_type) and (
        base in _DECIMAL_NAMES or any(word in base for word in _DECIMAL_WORDS)
    )


def is_numeric_type(data_type: str) -> bool:
    return is_integer_type(data_type) or is_decimal_type(data_type)


def is_boolean_type(data_type: str) -> bool:
    return "bool" in base_type(data_type)


def temporal_kind(data_type: str) -> Optional[Literal["timestamp", "date"]]:
    """`"timestamp"`, `"date"`, or None for anything else, plain `time` included."""
    base = base_type(data_type)
    if "timestamp" in base or ("date" in base and "time" in base):
        return "timestamp"
    if "date" in base:
        return "date"
    return None


def is_temporal_type(data_type: str) -> bool:
    return temporal_kind(data_type) is not None
