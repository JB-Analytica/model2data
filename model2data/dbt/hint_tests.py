"""dbt tests a model's generation hints imply: the model writes its data tests.

A hint states what valid data looks like, so the same model that generates
fixtures can guard the real pipeline. `hint_tests_for` returns those tests as
plain data; `generate_dbt_yml` writes them into the staging models' schema
YAML, and a consumer that post-processes the YAML itself (the studio's export)
can call it directly.

What becomes a test, and what does not:

| hint                      | test                                   | tolerance |
|---------------------------|----------------------------------------|-----------|
| `generate.min` / `max`    | `model2data_between`                   | none      |
| `generate.after: other`   | `model2data_not_before`                | none      |
| `generate.null_rate`      | `model2data_max_null_share`            | yes       |
| `generate.distinct: n`    | `model2data_max_distinct`              | none      |
| `generate.when`           | `model2data_when`                      | none      |
| table `grain`             | `model2data_unique_combination`        | none      |

A column with `when` and `null_rate` has its null share tested among the rows
`when` matches (`model2data_when_max_null_share`), since only those rows can
hold a value, and its `model2data_when` test lets those rows be null.

Enum columns keep the `accepted_values` test `generate_dbt_yml` always wrote.
`true_rate`, `weights`, `skew`, `distribution` and the temporal shape hints
(`business_hours`, `growth`, `seasonality`) describe a statistical shape, not a
constraint a row can break, so they produce no test.

The generic tests live in `macros/model2data_hint_tests.sql` of the generated
project (see `write_hint_macros`), and the `when` ones in
`macros/model2data_when_tests.sql`, written only for a model that has one, so
a project without `when` is what it was: plain SQL, no dbt package.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union

from model2data.generate import kinds
from model2data.model.engine import EngineInputs, to_engine
from model2data.model.types import Model
from model2data.parse.dbml import ColumnDef, TableDef

Severity = Literal["error", "warn", "off"]
SEVERITIES: tuple[str, ...] = ("error", "warn", "off")
DEFAULT_TOLERANCE = 0.1

MACROS_FILE = "model2data_hint_tests.sql"
_MACROS_SOURCE = Path(__file__).parent / "templates" / "hint_macros" / MACROS_FILE
WHEN_MACROS_FILE = "model2data_when_tests.sql"
_WHEN_MACROS_SOURCE = Path(__file__).parent / "templates" / "hint_macros" / WHEN_MACROS_FILE


@dataclass(frozen=True)
class HintTest:
    """One dbt generic test a hint implies.

    `table` is the key of the table in the tables given to `hint_tests_for`;
    `column` is None for a table-level test (grain). `test` is the generic
    test's name, `arguments` its parameters as dbt's `arguments:` block, and
    `hint` the hint it comes from (`min`, `max`, `after`, `null_rate`,
    `distinct`, `when`, `grain`). `min` and `max` of one column share one test.
    """

    table: str
    column: Optional[str]
    test: str
    arguments: dict[str, Any] = field(default_factory=dict)
    hint: str = ""

    def to_dbt(self, severity: str = "warn") -> dict[str, dict[str, Any]]:
        """The entry for a `tests:` list: `{name: {arguments: ..., config: {severity: ...}}}`."""
        body: dict[str, Any] = {"arguments": dict(self.arguments)}
        body["config"] = {"severity": severity}
        return {self.test: body}


def hint_tests_for(
    model_or_tables: Union[Model, EngineInputs, Mapping[str, TableDef]],
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[HintTest]:
    """The tests implied by the hints of a model, in table then column order.

    Takes a `Model`, the `EngineInputs` of one, or the `tables` dict (as
    `to_engine(model).tables`, or the renamed tables the CLI hands
    `generate_dbt_yml`). Table-level tests follow the table's column tests.

    `tolerance` is the absolute slack of the statistical test (the null share
    may be `null_rate + tolerance`); hard constraints take none. Severity is
    not decided here: pass each test to `HintTest.to_dbt(severity)`.
    """
    if tolerance < 0:
        raise ValueError("tolerance must not be negative")
    if isinstance(model_or_tables, Model):
        tables: Mapping[str, TableDef] = to_engine(model_or_tables).tables
    elif isinstance(model_or_tables, EngineInputs):
        tables = model_or_tables.tables
    else:
        tables = model_or_tables

    found: list[HintTest] = []
    for key, table in tables.items():
        by_name = {column.name: column for column in table.columns}
        names = set(by_name)
        for column in table.columns:
            found.extend(_column_tests(key, column, by_name, tolerance))
        grain = (table.note or {}).get("grain")
        if grain and all(name in names for name in grain):
            found.append(
                HintTest(
                    key, None, "model2data_unique_combination", {"columns": list(grain)}, "grain"
                )
            )
    return found


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_tests(
    table: str, column: ColumnDef, kinds_of: dict[str, ColumnDef], tolerance: float
) -> list[HintTest]:
    note = column.note if isinstance(column.note, dict) else {}
    if not note:
        return []
    tests: list[HintTest] = []

    if not column.enum_values and kinds.is_numeric_type(column.data_type):
        bounds = {
            f"{side}_value": note[side] for side in ("min", "max") if _is_number(note.get(side))
        }
        if bounds:
            tests.append(
                HintTest(
                    table,
                    column.name,
                    "model2data_between",
                    bounds,
                    "/".join(side for side in ("min", "max") if f"{side}_value" in bounds),
                )
            )

    after = note.get("after")
    if isinstance(after, str) and after in kinds_of and after != column.name:
        arguments: dict[str, Any] = {"other": after}
        # The generator compares a date with a timestamp at day granularity.
        if (
            kinds.temporal_kind(column.data_type) == "date"
            and kinds.temporal_kind(kinds_of[after].data_type) == "timestamp"
        ):
            arguments["granularity"] = "day"
        tests.append(HintTest(table, column.name, "model2data_not_before", arguments, "after"))

    null_rate = note.get("null_rate")
    if not isinstance(null_rate, (int, float)) or isinstance(null_rate, bool):
        null_rate = None
    conditions = _when_conditions(note.get("when"), kinds_of)
    if conditions is not None:
        when_arguments: dict[str, Any] = {"conditions": conditions}
        if null_rate is not None:
            when_arguments["required"] = False
        tests.append(HintTest(table, column.name, "model2data_when", when_arguments, "when"))

    if null_rate is not None:
        # Rounded so 0.1 + 0.1 reaches the YAML as 0.2, not 0.2000000000000001.
        limit = round(min(1.0, null_rate + tolerance), 10)
        if conditions is None:
            tests.append(
                HintTest(
                    table,
                    column.name,
                    "model2data_max_null_share",
                    {"max_share": limit},
                    "null_rate",
                )
            )
        else:
            tests.append(
                HintTest(
                    table,
                    column.name,
                    "model2data_when_max_null_share",
                    # Its own copy, or the YAML would share the mapping as an anchor.
                    {"conditions": copy.deepcopy(conditions), "max_share": limit},
                    "null_rate",
                )
            )

    distinct = note.get("distinct")
    if isinstance(distinct, int) and not isinstance(distinct, bool) and distinct > 0:
        tests.append(
            HintTest(
                table, column.name, "model2data_max_distinct", {"max_count": distinct}, "distinct"
            )
        )
    return tests


def _when_conditions(when: Any, kinds_of: dict[str, ColumnDef]) -> Optional[dict[str, list]]:
    """A `when` hint as the test's `conditions`: an enum's values as the member text it holds."""
    if not isinstance(when, dict) or not when or not all(name in kinds_of for name in when):
        return None
    return {
        name: [str(value) for value in values] if kinds_of[name].enum_values else list(values)
        for name, values in when.items()
    }


def write_hint_macros(dest: Path, *, when: bool = False) -> Path:
    """Write the generic tests into `dest/macros/`, and return the file's path.

    A project whose YAML holds `hint_tests_for` tests needs this file next to
    it; `generate_dbt_yml` writes it itself when it emits any. With `when`, the
    file of the `when` tests is written beside it (`WHEN_MACROS_FILE`), which a
    project holding a `model2data_when` test needs too.
    """
    macros = dest / "macros"
    macros.mkdir(parents=True, exist_ok=True)
    target = macros / MACROS_FILE
    target.write_text(_MACROS_SOURCE.read_text())
    if when:
        (macros / WHEN_MACROS_FILE).write_text(_WHEN_MACROS_SOURCE.read_text())
    return target
