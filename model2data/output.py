"""From what a run generated to what it writes: history tables added, defects applied.

The CLI and the studio both generate the clean data first -- with
`generate_data_from_dbml`, or `generate_days` -- and then call `finish_run`,
which does everything between that and writing the dbt project:

    >>> from model2data.model import load
    >>> from model2data.generate.days import generate_days
    >>> from model2data.defects import planned_defects
    >>> from model2data.output import finish_run
    >>> model = load("examples/ecommerce_training.model2data.yml")
    >>> days = generate_days(model, 3)
    >>> out = finish_run(model, days, planned_defects(model, days=3), seed=42)
    >>> out.frames["orders_history"]       # every version of every order
    >>> out.report.expected_failures       # the tests `dbt build` fails

`out.tables` and `out.refs` are what `generate_dbt_yml` takes (after renaming
to dbt names, `model2data.dbt.naming.for_dbt`), `out.frames` what the seeds
hold, `out.days` what `write_batches` / `write_changelog` write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Optional, Union, cast

import pandas as pd

from model2data.dbt.hint_tests import DEFAULT_TOLERANCE
from model2data.dbt.naming import dbt_names
from model2data.defects.apply import break_tables, rewrite_days
from model2data.defects.report import DefectsReport
from model2data.generate.days import DayResult, TableDay
from model2data.generate.faker import AsOf, _anchor_date
from model2data.generate.history import history_frames, history_tables
from model2data.model.engine import EngineInputs, to_engine
from model2data.model.types import Defect, Model
from model2data.parse.dbml import TableDef


@dataclass
class RunOutput:
    """What a run writes.

    `tables` are the model's tables and its history tables (`<table>_history`),
    by key; `refs` the model's refs. `frames` hold what each table's seed holds,
    defects applied. `days` are the days with the defects in them, empty when
    `finish_run` was given the frames of a single day. `report` is None when the
    run applied no defect.
    """

    tables: dict[str, TableDef]
    refs: list[dict]
    frames: dict[str, pd.DataFrame]
    days: list[DayResult] = field(default_factory=list)
    report: Optional[DefectsReport] = None


def finish_run(
    model: Union[Model, EngineInputs],
    data: Union[Mapping[str, pd.DataFrame], list[DayResult]],
    defects: Optional[Mapping[str, Sequence[Defect]]] = None,
    *,
    seed: Optional[int] = None,
    preset: Optional[str] = None,
    as_of: AsOf = None,
    hint_tests: str = "warn",
    test_tolerance: float = DEFAULT_TOLERANCE,
    names: Optional[Mapping[str, str]] = None,
) -> RunOutput:
    """The clean `data` of a run, with its history tables and its `defects`.

    `data` is what `generate_days` returned, or the frames of
    `generate_data_from_dbml` (one day; `as_of` then dates it, as it dated the
    generation). `defects` is `planned_defects(model, ...)`; None or empty applies
    none. The history tables are built from the clean days, so the defects of a
    table do not reach its history; `overlapping_history` breaks the history.
    `seed`, `preset`, `hint_tests`, `test_tolerance` and `names` are as
    `apply_defects` takes them; `names` must cover the history tables too, and
    defaults to the CLI's names for all of them. `data` is not modified.
    """
    inputs = to_engine(model) if isinstance(model, Model) else model
    days: list[DayResult] = []
    if isinstance(data, Mapping):
        day = _anchor_date(as_of)
        given = cast(Mapping[str, pd.DataFrame], data)
        first = {k: TableDay(f, f.iloc[0:0].copy(), f) for k, f in given.items()}
        results = [DayResult(0, day, first)]
    else:
        results = days = list(data)
    histories = history_tables(inputs)
    tables = {**inputs.tables, **histories}
    frames = {**results[-1].state, **history_frames(inputs, results)}
    if not defects:
        return RunOutput(tables, list(inputs.refs), frames, days)
    with_history = EngineInputs(
        tables, inputs.refs, inputs.many_to_many, inputs.run, inputs.incremental
    )
    broken, applied, report = break_tables(
        with_history,
        frames,
        defects,
        results=results,
        seed=seed,
        preset=preset,
        hint_tests=hint_tests,
        test_tolerance=test_tolerance,
        names=names if names is not None else dbt_names(tables),
    )
    if days:
        days = rewrite_days(days, [entry for entry in applied if entry[0] in inputs.tables])
    return RunOutput(tables, list(inputs.refs), broken, days, report)
