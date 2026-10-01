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
from typing import Any, Callable

import pandas as pd

from model2data.dbt.tests import DbtTest


def as_seeded(frame: pd.DataFrame) -> pd.DataFrame:
    """`frame` as the text its seed CSV holds, an empty field (a null) as None."""
    text = frame.to_csv(index=False)
    read = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[""])
    return read.astype(object).where(read.notna(), None)


def failing(tests: Iterable[DbtTest], seeded: Mapping[str, pd.DataFrame]) -> set[str]:
    """The names of the tests that fail on `seeded` (frames by dbt name, from `as_seeded`)."""
    return {test.name for test in tests if fails(test, seeded)}


def fails(test: DbtTest, seeded: Mapping[str, pd.DataFrame]) -> bool:
    """Whether `test` fails on `seeded`. A test of a kind not known here never fails."""
    frame = seeded.get(test.table)
    if frame is None:
        return False
    check = _CHECKS.get(test.type)
    return bool(check is not None and check(test, frame, seeded))


_NUMBER = re.compile(r"^-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?$")


def _number(value: Any) -> Any:
    """A numeric text as its number, so 7 and 7.0 are one value; anything else as it is."""
    if isinstance(value, str) and _NUMBER.match(value):
        return float(value)
    return value


def _values(frame: pd.DataFrame, column: str) -> list[Any]:
    return [value for value in frame[column].tolist() if value is not None]


def _not_null(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    return any(value is None for value in frame[test.column].tolist())


def _unique(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    values = [_number(value) for value in _values(frame, str(test.column))]
    return len(set(values)) < len(values)


def _accepted_values(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    allowed = {str(value) for value in test.arguments.get("values", [])}
    return any(value not in allowed for value in _values(frame, str(test.column)))


def _relationships(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    if test.parent is None:
        return False
    parent_table, parent_column = test.parent
    parent = seeded.get(parent_table)
    if parent is None:
        return False
    held = {_number(value) for value in _values(parent, parent_column)}
    return any(_number(value) not in held for value in _values(frame, str(test.column)))


def _between(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    low = test.arguments.get("min_value")
    high = test.arguments.get("max_value")
    for value in _values(frame, str(test.column)):
        number = _number(value)
        if not isinstance(number, float):
            continue
        if (low is not None and number < low) or (high is not None and number > high):
            return True
    return False


def _not_before(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    other = test.arguments["other"]
    pairs = frame[[str(test.column), other]].dropna()
    if pairs.empty:
        return False
    mine = pd.to_datetime(pairs[str(test.column)], format="mixed")
    theirs = pd.to_datetime(pairs[other], format="mixed")
    if test.arguments.get("granularity") == "day":
        theirs = theirs.dt.normalize()
    return bool((mine < theirs).any())


def _max_null_share(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    if not len(frame):
        return False
    nulls = sum(value is None for value in frame[test.column].tolist())
    return nulls / len(frame) > test.arguments["max_share"]


def _max_distinct(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    distinct = {_number(value) for value in _values(frame, str(test.column))}
    return len(distinct) > test.arguments["max_count"]


def _unique_combination(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    columns = list(test.arguments["columns"])
    rows = [
        tuple(_number(value) for value in row) for row in frame[columns].itertuples(index=False)
    ]
    return len(set(rows)) < len(rows)


def _one_current_row(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    key = list(test.arguments["key"])
    current = frame[test.arguments["current"]].map(lambda value: str(value).lower() == "true")
    counts = current.groupby([frame[column].map(_number) for column in key], dropna=False).sum()
    return bool((counts != 1).any())


def _no_overlapping_ranges(test: DbtTest, frame: pd.DataFrame, seeded: Mapping) -> bool:
    key = list(test.arguments["key"])
    begins = pd.to_datetime(frame[test.arguments["valid_from"]], format="mixed")
    ends = pd.to_datetime(frame[test.arguments["valid_to"]], format="mixed")
    keys = [tuple(_number(v) for v in row) for row in frame[key].itertuples(index=False)]
    versions: dict[tuple, list[tuple]] = {}
    for row, k in enumerate(keys):
        versions.setdefault(k, []).append((begins.iloc[row], ends.iloc[row]))
    for spans in versions.values():
        spans.sort(key=lambda span: span[0])
        for (_, end), (begin, _) in zip(spans, spans[1:], strict=False):
            if pd.isna(end) or end > begin:
                return True
    return False


_CHECKS: dict[str, Callable[[DbtTest, pd.DataFrame, Mapping], bool]] = {
    "not_null": _not_null,
    "unique": _unique,
    "accepted_values": _accepted_values,
    "relationships": _relationships,
    "model2data_between": _between,
    "model2data_not_before": _not_before,
    "model2data_max_null_share": _max_null_share,
    "model2data_max_distinct": _max_distinct,
    "model2data_unique_combination": _unique_combination,
    "unique_combination": _unique_combination,
    "model2data_one_current_row": _one_current_row,
    "model2data_no_overlapping_ranges": _no_overlapping_ranges,
}
