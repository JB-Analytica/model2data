"""Which of a generated project's data tests the data in hand fails, decided in Python.

The defects report says which dbt tests a run's defects break. Rather than
predict that from the kind of defect, it runs every test `generate_dbt_yml`
writes (`model2data.dbt.tests.dbt_tests`) against the rows the seeds will hold,
so a test a defect breaks by the way (a `distinct` hint test that messy text
pushes over its count) is in the report too. Each test is checked the way its
SQL reads:

- `not_null`: any null. `unique`: a non-null value twice. `accepted_values`: a
  non-null value outside the list. `relationships`: a non-null value the parent
  column does not hold.
- `model2data_between`, `model2data_not_before`, `model2data_max_null_share`
  and `model2data_max_distinct`: as their macros in
  `dbt/templates/hint_macros/model2data_hint_tests.sql`, nulls skipped where the
  SQL skips them.
- `model2data_not_before_parent`: as its macro in
  `dbt/templates/hint_macros/model2data_parent_tests.sql`: a row dated before
  the earliest parent row its foreign key matches, by day with `granularity: day`.
- `model2data_when` and `model2data_when_max_null_share`: as their macros in
  `dbt/templates/hint_macros/model2data_when_tests.sql`, a row matching when
  each named column's text is one of its values (a boolean as `true`/`false`
  in any case, a number as a number).
- `model2data_unique_combination` and a composite key's `unique_combination`: a
  combination of values on two rows, nulls grouping together as in `group by`.
- A history table's `model2data_one_current_row` (a key with other than one
  current version) and `model2data_no_overlapping_ranges` (a version still
  valid when the next of its key begins).

Values are compared as the seed CSV writes them, numbers as numbers, so `7`
and `7.0` are one value, as they are to the warehouse.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from model2data.dbt.tests import DbtTest


def as_seeded(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` as the text its seed CSV holds, an empty field (a null) as None.

    The CSV writes each column on its own (a lone empty field as `""`), so a
    column reads back the same whichever of the frame's columns are written
    beside it: the report seeds only the columns a defect changed.

    The text is worked out column by column as the round trip would give it
    (`_seeded_texts`); a frame holding anything not known to come back so is
    written and read back as before.
    """
    lone = frame.shape[1] == 1
    texts = [_seeded_texts(column, lone) for _, column in frame.items()]
    if (
        not len(frame)
        or not texts
        or any(column is None for column in texts)
        or not frame.columns.is_unique
        or not all(_plain_name(name) for name in frame.columns)
    ):
        return _round_trip(frame)
    held = np.empty((len(texts), len(frame)), dtype=object)
    for row, column in enumerate(texts):
        held[row] = column
    # `dtype=object`, or pandas 3 (or `future.infer_string`) reads the column
    # as its `str` dtype and turns every None into NaN, which a check reads as
    # a value: the round trip's frame is object dtype with None.
    return pd.DataFrame(held.T, columns=pd.Index(list(frame.columns)), dtype=object)


def _round_trip(frame: pd.DataFrame) -> pd.DataFrame:
    """`as_seeded` the long way: the frame written as CSV and read back."""
    text = frame.to_csv(index=False)
    read = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[""])
    return read.astype(object).where(read.notna(), None)


def _plain_name(name: Any) -> bool:
    """A column name a CSV header gives back as itself."""
    return (
        isinstance(name, str)
        and name == name.strip()
        and name != ""
        and not name.startswith("Unnamed")
        and not any(c in name for c in ',"\r\n\x00\ufeff')
    )


# Nullable columns whose values the CSV holds as their `str`, a null as an empty field.
_MASKED = (
    pd.Int8Dtype,
    pd.Int16Dtype,
    pd.Int32Dtype,
    pd.Int64Dtype,
    pd.UInt8Dtype,
    pd.UInt16Dtype,
    pd.UInt32Dtype,
    pd.UInt64Dtype,
    pd.BooleanDtype,
)


def _seeded_texts(column: pd.Series, lone: bool) -> Optional[list[Optional[str]]]:
    """What `as_seeded` makes of `column`, worked out without writing a CSV.

    Each value as `DataFrame.to_csv` writes it -- a float column through numpy's
    `str`; a value of an object or nullable column as the csv module writes it,
    its `str` (a float's `repr`) -- and then as `read_csv` gives it back, an
    empty field as None. `lone` is whether the column is the frame's only one.
    None when the column holds anything not known to come back so.
    """
    dtype = column.dtype
    if isinstance(dtype, np.dtype) and dtype.kind == "f":
        array = column.to_numpy()
        return _nulled(array.astype(str).tolist(), np.isnan(array))
    if isinstance(dtype, np.dtype) and dtype.kind in "iub":
        return list(map(str, column.to_numpy().tolist()))
    if isinstance(dtype, _MASKED):
        held = column.array.to_numpy(dtype=dtype.numpy_dtype, na_value=dtype.numpy_dtype.type(0))
        return _nulled(list(map(str, held.tolist())), column.isna().to_numpy())
    if not (isinstance(dtype, np.dtype) and dtype.kind == "O"):
        return None
    values = column.tolist()
    kinds = set(map(type, values))
    if not kinds <= _WRITTEN_AS_TEXT:
        return None
    if not kinds <= {str, type(None)}:
        values = [_csv_text(value) for value in values]
    held = [value for value in values if value is not None]
    # A NUL cuts the field when read back; a carriage return is written
    # unquoted by Python before 3.12's csv and read back as a line break.
    joined = "".join(held)
    if "\x00" in joined or "\r" in joined:
        return None
    if lone and any(value and not value.strip() for value in held):
        return None  # a line of only blanks is skipped: the row would be lost
    return [value or None for value in values]


# What an object column may hold for `_csv_text` to say what the CSV holds.
_WRITTEN_AS_TEXT = {str, type(None), type(pd.NA), bool, int, float, date}


def _csv_text(value: Any) -> Optional[str]:
    """A value of an object column as the csv module writes it, a null as None."""
    if value is None or value is pd.NA or (type(value) is float and value != value):
        return None
    return repr(value) if type(value) is float else str(value)


def _nulled(texts: list[Any], nulls: np.ndarray) -> list[Optional[str]]:
    """`texts` with None where `nulls` is true."""
    for position in np.flatnonzero(nulls).tolist():
        texts[position] = None
    return texts


class Columns:
    """Each seeded frame's columns as the checks read them, worked out once.

    The checks of a project read the same column many times (its `not_null`,
    `unique` and the `relationships` of every child pointing at it), and the
    report checks the same frames over and over; this keeps each column's
    values, and its values as numbers, for as long as the frame is in use. A
    frame is known by identity: it must not change while it is cached.
    """

    def __init__(self) -> None:
        self._frames: dict[int, tuple[pd.DataFrame, dict[tuple[str, str], Any]]] = {}
        # Each text's `_number`, for every column: the seeded frames hold the same texts.
        self._number_of: dict[str, Any] = {}

    def _entry(self, frame: pd.DataFrame) -> dict[tuple[str, str], Any]:
        held = self._frames.get(id(frame))
        if held is None or held[0] is not frame:
            held = (frame, {})
            self._frames[id(frame)] = held
        return held[1]

    def share(self, frame: pd.DataFrame, base: pd.DataFrame, columns: Iterable[str]) -> None:
        """Let `frame` use what is known of `base`'s `columns`, which it holds unchanged."""
        keep = set(columns)
        mine = self._entry(frame)
        for (kind, column), value in self._entry(base).items():
            if column in keep:
                mine.setdefault((kind, column), value)

    def _get(self, frame: pd.DataFrame, kind: str, column: str, make: Callable[[], Any]) -> Any:
        entry = self._entry(frame)
        key = (kind, column)
        if key not in entry:
            entry[key] = make()
        return entry[key]

    def values(self, frame: pd.DataFrame, column: str) -> list[Any]:
        """Every value of the column, a null as None."""
        return self._get(frame, "values", column, lambda: frame[column].tolist())

    def present(self, frame: pd.DataFrame, column: str) -> list[Any]:
        """The column's values that are not null."""
        return self._get(
            frame,
            "present",
            column,
            lambda: [value for value in self.values(frame, column) if value is not None],
        )

    def numbers(self, frame: pd.DataFrame, column: str) -> list[Any]:
        """Every value of the column through `_number`, a null as None."""

        def make() -> list[Any]:
            values = self.values(frame, column)
            known = self._number_of
            converted = {}
            for value in set(values):
                if type(value) is str:
                    if value not in known:
                        known[value] = _number(value)
                    converted[value] = known[value]
                else:
                    converted[value] = _number(value)
            return [converted[value] for value in values]

        return self._get(frame, "numbers", column, make)

    def present_numbers(self, frame: pd.DataFrame, column: str) -> list[Any]:
        """The column's values that are not null, through `_number`."""
        return self._get(
            frame,
            "present_numbers",
            column,
            lambda: [value for value in self.numbers(frame, column) if value is not None],
        )

    def number_set(self, frame: pd.DataFrame, column: str) -> set[Any]:
        """The distinct values of the column that are not null, through `_number`."""
        return self._get(
            frame, "number_set", column, lambda: set(self.present_numbers(frame, column))
        )

    def has_null(self, frame: pd.DataFrame, column: str) -> bool:
        return self._get(
            frame,
            "has_null",
            column,
            lambda: len(self.present(frame, column)) < len(self.values(frame, column)),
        )


def failing(
    tests: Iterable[DbtTest],
    seeded: Mapping[str, pd.DataFrame],
    *,
    cache: Optional[Columns] = None,
) -> set[str]:
    """The names of the tests that fail on `seeded` (frames by dbt name, from `as_seeded`).

    `cache` keeps what is read of each frame for the next call on the same frames.
    """
    cache = cache if cache is not None else Columns()
    return {test.name for test in tests if fails(test, seeded, cache=cache)}


def fails(
    test: DbtTest,
    seeded: Mapping[str, pd.DataFrame],
    *,
    cache: Optional[Columns] = None,
) -> bool:
    """Whether `test` fails on `seeded`. A test of a kind not known here never fails."""
    frame = seeded.get(test.table)
    if frame is None:
        return False
    check = _CHECKS.get(test.type)
    cache = cache if cache is not None else Columns()
    return bool(check is not None and check(test, frame, seeded, cache))


_NUMBER = re.compile(r"^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$")


def _number(value: Any) -> Any:
    """A numeric text as its number, so 7 and 7.0 are one value; anything else as it is."""
    if isinstance(value, str) and _NUMBER.match(value):
        return float(value)
    return value


def _not_null(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    return cache.has_null(frame, str(test.column))


def _unique(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    column = str(test.column)
    return len(cache.number_set(frame, column)) < len(cache.present_numbers(frame, column))


def _accepted_values(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    allowed = {str(value) for value in test.arguments.get("values", [])}
    return any(value not in allowed for value in cache.present(frame, str(test.column)))


def _relationships(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    if test.parent is None:
        return False
    parent_table, parent_column = test.parent
    parent = seeded.get(parent_table)
    if parent is None:
        return False
    held = cache.number_set(parent, parent_column)
    return not cache.number_set(frame, str(test.column)) <= held


def _between(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    low = test.arguments.get("min_value")
    high = test.arguments.get("max_value")
    for number in cache.present_numbers(frame, str(test.column)):
        if not isinstance(number, float):
            continue
        if (low is not None and number < low) or (high is not None and number > high):
            return True
    return False


def _not_before(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    other = test.arguments["other"]
    pairs = frame[[str(test.column), other]].dropna()
    if pairs.empty:
        return False
    mine = pd.to_datetime(pairs[str(test.column)], format="mixed")
    theirs = pd.to_datetime(pairs[other], format="mixed")
    if test.arguments.get("granularity") == "day":
        theirs = theirs.dt.normalize()
    return bool((mine < theirs).any())


def _not_before_parent(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    assert test.parent is not None  # `dbt_tests` names the parent of every such test
    parent = seeded.get(test.parent[0])
    if parent is None:
        return False
    arguments = test.arguments
    mine = pd.DataFrame(
        {
            "key": cache.numbers(frame, arguments["foreign_key"]),
            "moment": cache.values(frame, str(test.column)),
        }
    ).dropna()
    theirs = pd.DataFrame(
        {
            "key": cache.numbers(parent, arguments["field"]),
            "parent_moment": cache.values(parent, arguments["parent_column"]),
        }
    ).dropna()
    if mine.empty or theirs.empty:
        return False
    # A key several parent rows share counts its earliest, as the SQL's `min` does.
    theirs["parent_moment"] = pd.to_datetime(theirs["parent_moment"], format="mixed")
    earliest = theirs.groupby("key", sort=False)["parent_moment"].min().reset_index()
    pairs = mine.merge(earliest, on="key")
    if pairs.empty:
        return False
    child = pd.to_datetime(pairs["moment"], format="mixed")
    parents = pairs["parent_moment"]
    if arguments.get("granularity") == "day":
        child, parents = child.dt.normalize(), parents.dt.normalize()
    return bool((child < parents).any())


def _max_null_share(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    if not len(frame):
        return False
    values = cache.values(frame, str(test.column))
    nulls = len(values) - len(cache.present(frame, str(test.column)))
    return nulls / len(frame) > test.arguments["max_share"]


def _matches(test: DbtTest, frame: pd.DataFrame, cache: Columns) -> list[bool]:
    """Per row: does each column of the test's `conditions` hold one of its values?"""
    result = [True] * len(frame)
    for column, values in test.arguments["conditions"].items():
        texts = {value for value in values if isinstance(value, str)}
        flags = {str(value).lower() for value in values if isinstance(value, bool)}
        numbers = {
            float(value)
            for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        for row, text in enumerate(cache.values(frame, column)):
            if text is None or not (
                text in texts or text.lower() in flags or _number(text) in numbers
            ):
                result[row] = False
    return result


def _when(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    required = test.arguments.get("required", True)
    values = cache.values(frame, str(test.column))
    for matches, value in zip(_matches(test, frame, cache), values, strict=True):
        if (not matches and value is not None) or (required and matches and value is None):
            return True
    return False


def _when_max_null_share(
    test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns
) -> bool:
    values = cache.values(frame, str(test.column))
    held = [
        value
        for matches, value in zip(_matches(test, frame, cache), values, strict=True)
        if matches
    ]
    if not held:
        return False
    nulls = sum(value is None for value in held)
    return nulls / len(held) > test.arguments["max_share"]


def _max_distinct(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    return len(cache.number_set(frame, str(test.column))) > test.arguments["max_count"]


def _unique_combination(
    test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns
) -> bool:
    columns = [cache.numbers(frame, column) for column in test.arguments["columns"]]
    rows = list(zip(*columns, strict=True))
    return len(set(rows)) < len(rows)


def _one_current_row(test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns) -> bool:
    key = list(test.arguments["key"])
    current = frame[test.arguments["current"]].map(lambda value: str(value).lower() == "true")
    counts = current.groupby([frame[column].map(_number) for column in key], dropna=False).sum()
    return bool((counts != 1).any())


def _no_overlapping_ranges(
    test: DbtTest, frame: pd.DataFrame, seeded: Mapping, cache: Columns
) -> bool:
    key = list(test.arguments["key"])
    begins = pd.to_datetime(frame[test.arguments["valid_from"]], format="mixed")
    ends = pd.to_datetime(frame[test.arguments["valid_to"]], format="mixed")
    keys = list(zip(*(cache.numbers(frame, column) for column in key), strict=True))
    versions: dict[tuple, list[tuple]] = {}
    for row, k in enumerate(keys):
        versions.setdefault(k, []).append((begins.iloc[row], ends.iloc[row]))
    for spans in versions.values():
        spans.sort(key=lambda span: span[0])
        for (_, end), (begin, _) in zip(spans, spans[1:], strict=False):
            if pd.isna(end) or end > begin:
                return True
    return False


_CHECKS: dict[str, Callable[[DbtTest, pd.DataFrame, Mapping, Columns], bool]] = {
    "not_null": _not_null,
    "unique": _unique,
    "accepted_values": _accepted_values,
    "relationships": _relationships,
    "model2data_between": _between,
    "model2data_not_before": _not_before,
    "model2data_not_before_parent": _not_before_parent,
    "model2data_max_null_share": _max_null_share,
    "model2data_max_distinct": _max_distinct,
    "model2data_when": _when,
    "model2data_when_max_null_share": _when_max_null_share,
    "model2data_unique_combination": _unique_combination,
    "unique_combination": _unique_combination,
    "model2data_one_current_row": _one_current_row,
    "model2data_no_overlapping_ranges": _no_overlapping_ranges,
}
