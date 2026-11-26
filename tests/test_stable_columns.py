"""A column's values depend on the run's settings and the column's own definition.

Not on which other columns the table holds, nor on where they sit, nor on which other
tables the model has. Every column (and every step that works on a whole table: ordering
dates, repairing keys, applying rules) draws from a stream of its own, derived from the
seed, the table and the column's name, so a model can grow without the rows it already
generated moving.

What may still move a column is a real dependency: a column derived from, ordered
`after`, or ruled with another changes when that one does, and a column draws from the
rows of the parent it points at.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import pytest

from model2data.generate.core import generate_data_from_dbml
from model2data.generate.days import generate_days
from model2data.model import load, to_engine
from model2data.parse.dbml import ColumnDef, TableDef

ROOT = Path(__file__).resolve().parent.parent
MODELS = sorted(
    [
        *(ROOT / "examples").glob("*.model2data.yml"),
        *(ROOT / "examples").glob("*.dbml"),
        *(ROOT / "model2data" / "spec" / "examples").glob("*.model2data.yml"),
    ]
)
COFFEE = ROOT / "model2data" / "spec" / "examples" / "coffee_webshop.model2data.yml"
AS_OF = date(2026, 3, 15)
ROWS = 40


class Inputs:
    """A model's engine inputs and the run settings it generates with."""

    def __init__(self, path: Path) -> None:
        model = load(path)
        engine = to_engine(model)
        self.tables: dict[str, TableDef] = engine.tables
        self.refs: list[dict] = engine.refs
        run = model.run
        self.seed = run.seed if run is not None and run.seed is not None else 7
        self.locale = run.locale if run is not None else None
        self.as_of = AS_OF
        self.rows_per_table = dict(run.rows_per_table or {}) if run is not None else {}
        # Small enough to be quick, large enough that a column has something to draw.
        self.rows_per_table = {name: min(rows, 120) for name, rows in self.rows_per_table.items()}

    def copy(self) -> "Inputs":
        other = copy.copy(self)
        other.tables = copy.deepcopy(self.tables)
        other.refs = copy.deepcopy(self.refs)
        other.rows_per_table = dict(self.rows_per_table)
        return other

    def generate(self, *, seed: Optional[int] = None, table_seeds=None) -> dict[str, pd.DataFrame]:
        return generate_data_from_dbml(
            tables=self.tables,
            refs=self.refs,
            base_rows=ROWS,
            seed=self.seed if seed is None else seed,
            row_overrides=self.rows_per_table or None,
            table_seeds=table_seeds,
            locale=self.locale,
            as_of=self.as_of,
        )


def moved(before: dict[str, pd.DataFrame], after: dict[str, pd.DataFrame], skip=()) -> list[str]:
    """`table.column` of every column of `before` that `after` holds differently."""
    out = []
    for table, frame in before.items():
        for column in frame.columns:
            if (table, column) in skip:
                continue
            if column not in after[table].columns or not frame[column].equals(after[table][column]):
                out.append(f"{table}.{column}")
    return out


def descendants(refs: list[dict], table: str) -> set[str]:
    """Every table that points at `table`, directly or through others."""
    found: set[str] = set()
    todo = [table]
    while todo:
        current = todo.pop()
        for ref in refs:
            if ref["target_table"] == current and ref["source_table"] not in found:
                found.add(ref["source_table"])
                todo.append(ref["source_table"])
    return found


def parent_for(inputs: Inputs, table: str) -> Optional[tuple[str, str, str]]:
    """A table and key a new column of `table` can point at without making a cycle."""
    below = descendants(inputs.refs, table) | {table}
    # A table already pointed at would be reached through two keys, which a cross-table
    # `after` cannot tell apart.
    below |= {ref["target_table"] for ref in inputs.refs if ref["source_table"] == table}
    for name, other in inputs.tables.items():
        if name in below:
            continue
        for column in other.columns:
            if "pk" in column.settings and not column.enum_values:
                return name, column.name, column.data_type
    return None


# Columns whose names say nothing, so nothing about the table can change how they are drawn.
KINDS = {
    "text": lambda: ColumnDef("x_note", "varchar"),
    "int": lambda: ColumnDef("x_num", "int"),
    "date": lambda: ColumnDef("x_day", "date"),
    "enum": lambda: ColumnDef("x_kind", "x_kind_type", enum_values=["a", "b", "c"]),
    "person": lambda: ColumnDef("x_full_name", "varchar"),
    "email": lambda: ColumnDef("x_email", "varchar"),
}
POSITIONS = {"first": lambda n: 0, "middle": lambda n: n // 2, "last": lambda n: n}


def insert(inputs: Inputs, table: str, position: str, names: list[str]) -> Inputs:
    """A copy of `inputs` with columns of the given kinds put in `table` at `position`."""
    grown = inputs.copy()
    columns = grown.tables[table].columns
    at = POSITIONS[position](len(columns))
    for offset, kind in enumerate(names):
        if kind == "fk":
            parent = parent_for(grown, table)
            if parent is None:
                raise pytest.skip.Exception("no table to point at without a cycle")
            parent_table, parent_column, data_type = parent
            column = ColumnDef("x_ref_id", data_type)
            grown.refs.append(
                {
                    "source_table": table,
                    "source_column": "x_ref_id",
                    "target_table": parent_table,
                    "target_column": parent_column,
                }
            )
        else:
            column = KINDS[kind]()
        columns.insert(at + offset, column)
    return grown


# ---------------------------------------------------------
# A column does not move when its neighbours do
# ---------------------------------------------------------
@pytest.fixture(scope="module")
def coffee() -> Inputs:
    return Inputs(COFFEE)


@pytest.fixture(scope="module")
def coffee_rows(coffee) -> dict[str, pd.DataFrame]:
    return coffee.generate()


@pytest.mark.parametrize("position", list(POSITIONS))
@pytest.mark.parametrize("kind", [*KINDS, "fk"])
def test_a_new_column_moves_none_of_the_coffee_webshops_columns(
    coffee, coffee_rows, kind, position
):
    grown = insert(coffee, "orders", position, [kind])
    assert moved(coffee_rows, grown.generate()) == []


def test_the_new_column_is_drawn_the_same_wherever_it_sits(coffee):
    drawn = {
        position: insert(coffee, "orders", position, ["text", "int", "enum"]).generate()["orders"]
        for position in POSITIONS
    }
    for name in ("x_note", "x_num", "x_kind"):
        assert drawn["first"][name].equals(drawn["middle"][name])
        assert drawn["first"][name].equals(drawn["last"][name])


def test_a_column_nothing_depends_on_can_go(coffee, coffee_rows):
    shrunk = coffee.copy()
    columns = shrunk.tables["orders"].columns
    # `channel` is read by nothing: no rule, no `when`, no derivation, no key.
    columns.remove(next(column for column in columns if column.name == "channel"))
    after = shrunk.generate()
    assert moved(coffee_rows, after, skip={("orders", "channel")}) == []


def test_two_columns_can_swap_places(coffee, coffee_rows):
    swapped = coffee.copy()
    columns = swapped.tables["orders"].columns
    names = [column.name for column in columns]
    one, two = names.index("channel"), names.index("total_amount")
    columns[one], columns[two] = columns[two], columns[one]
    assert moved(coffee_rows, swapped.generate()) == []


@pytest.mark.parametrize("position", list(POSITIONS))
def test_a_table_added_to_the_model_moves_no_other_table(coffee, coffee_rows, position):
    grown = coffee.copy()
    extra = TableDef(
        name="x_extra",
        columns=[
            ColumnDef("id", "int", {"pk"}),
            ColumnDef("x_full_name", "varchar"),
            ColumnDef("x_num", "int"),
        ],
    )
    tables = dict(grown.tables)
    if position == "first":
        grown.tables = {"x_extra": extra, **tables}
    elif position == "last":
        grown.tables = {**tables, "x_extra": extra}
    else:
        items = list(tables.items())
        items.insert(len(items) // 2, ("x_extra", extra))
        grown.tables = dict(items)
    after = grown.generate()
    assert moved(coffee_rows, after) == []


# ---------------------------------------------------------
# Every example model, every table
# ---------------------------------------------------------
# Real dependencies a new column cannot be told apart from. None so far.
SEMANTIC: dict[tuple[str, str, str], str] = {}


def _cases():
    for path in MODELS:
        inputs = Inputs(path)
        for table in inputs.tables:
            for position in POSITIONS:
                yield pytest.param(path, table, position, id=f"{path.name}-{table}-{position}")


@pytest.mark.parametrize(("path", "table", "position"), list(_cases()))
def test_new_columns_move_no_column_of_any_example(path, table, position):
    inputs = Inputs(path)
    before = inputs.generate()
    grown = insert(inputs, table, position, ["text", "int", "date", "enum", "person", "fk"])
    changed = moved(before, grown.generate())
    unexplained = [name for name in changed if (path.stem, table, name) not in SEMANTIC]
    assert unexplained == []


# ---------------------------------------------------------
# A fixture list with the usual parts
# ---------------------------------------------------------
SPORTS = """
model2data: 0.9.0
name: fixture_list
enums:
  fixture_status: [scheduled, final]
tables:
  teams:
    columns:
      id: {type: bigint, pk: true}
      name: {type: company_name, not_null: true}
  fixtures:
    rules:
      - {home_team_id: {ne: {column: away_team_id}}}
    columns:
      id: {type: bigint, pk: true}
      home_team_id: {type: bigint, not_null: true, references: teams.id}
      away_team_id: {type: bigint, not_null: true, references: teams.id}
      kickoff_at:
        type: timestamp
        not_null: true
        generate:
          min: "2026-01-10"
          max: "2026-06-30"
          days: {sat: 3, sun: 2}
          times: {"15:00": 2, "17:30": 1}
      status:
        type: fixture_status
        not_null: true
        generate:
          derive:
            cases:
              - if: {kickoff_at: {lte: {run: as_of}}}
                then: final
            else: scheduled
      home_score:
        type: int
        generate: {distribution: {kind: poisson, mean: 1.5}, max: 9, when: {status: [final]}}
      away_score:
        type: int
        generate: {distribution: {kind: poisson, mean: 1.2}, max: 9, when: {status: [final]}}
      winner_team_id:
        type: bigint
        references: teams.id
        generate:
          when: {status: [final]}
          derive:
            cases:
              - if: {home_score: {gt: {column: away_score}}}
                then: {column: home_team_id}
              - if: {away_score: {gt: {column: home_score}}}
                then: {column: away_team_id}
      result_posted_at:
        type: timestamp
        generate: {after: kickoff_at, when: {status: [final]}}
run:
  seed: 5
  as_of: "2026-03-15"
  rows_per_table: {teams: 12, fixtures: 80}
"""


def _sports() -> Inputs:
    inputs = Inputs.__new__(Inputs)
    model = load(SPORTS)
    engine = to_engine(model)
    inputs.tables, inputs.refs = engine.tables, engine.refs
    inputs.seed, inputs.locale, inputs.as_of = 5, None, AS_OF
    inputs.rows_per_table = {"teams": 12, "fixtures": 80}
    return inputs


def test_a_venue_added_to_the_fixture_list_moves_nothing_that_was_there():
    inputs = _sports()
    before = inputs.generate()
    assert before["fixtures"]["home_score"].notna().any()  # some games are played

    grown = inputs.copy()
    grown.tables["venues"] = TableDef(
        name="venues",
        columns=[ColumnDef("id", "bigint", {"pk"}), ColumnDef("name", "varchar", {"not null"})],
    )
    grown.tables["teams"].columns.insert(1, ColumnDef("venue_id", "bigint"))
    fixtures = grown.tables["fixtures"].columns
    fixtures.insert(
        [column.name for column in fixtures].index("kickoff_at"),
        ColumnDef("venue_id", "bigint"),
    )
    for table in ("teams", "fixtures"):
        grown.refs.append(
            {
                "source_table": table,
                "source_column": "venue_id",
                "target_table": "venues",
                "target_column": "id",
            }
        )
    after = grown.generate()
    assert moved(before, after) == []
    assert after["fixtures"]["venue_id"].notna().any()


# ---------------------------------------------------------
# Rules and keys keep to themselves
# ---------------------------------------------------------
def test_a_rule_on_other_columns_moves_no_other_rules_draws(coffee):
    ruled = coffee.copy()
    ruled.tables["orders"].rules = [{"total_amount": {"gt": 150}}]
    before = ruled.generate()
    assert (before["orders"]["total_amount"] > 150).all()

    more = ruled.copy()
    more.tables["orders"].rules.append({"channel": {"in": ["web", "mobile_app"]}})
    after = more.generate()
    assert set(after["orders"]["channel"]) <= {"web", "mobile_app"}
    # the new rule redraws `channel`; everything else, the first rule's redraws included, stays.
    assert moved(before, after) == ["orders.channel"]


def test_a_rule_does_not_move_columns_it_does_not_read(coffee, coffee_rows):
    ruled = coffee.copy()
    ruled.tables["orders"].rules = [{"total_amount": {"gt": 150}}]
    assert moved(coffee_rows, ruled.generate()) == ["orders.total_amount"]


def test_a_composite_key_on_other_columns_moves_no_other_column(coffee, coffee_rows):
    keyed = coffee.copy()
    # twenty combinations for forty rows: the key has to redraw its columns
    keyed.tables["orders"].composite_keys.append(
        {"type": "unique", "columns": ["channel", "status"]}
    )
    after = keyed.generate()
    assert set(moved(coffee_rows, after)) <= {"orders.channel", "orders.status"}

    twice = keyed.copy()
    twice.tables["orders"].composite_keys.append(
        {"type": "unique", "columns": ["order_date", "total_amount"]}
    )
    again = twice.generate()
    # a second key redraws its own columns and leaves the first key's alone
    assert moved(after, again) == []


# ---------------------------------------------------------
# Seeds
# ---------------------------------------------------------
def test_the_same_seed_gives_the_same_rows(coffee, coffee_rows):
    assert moved(coffee_rows, coffee.generate()) == []
    assert moved(coffee_rows, coffee.generate(seed=8)) != []


def test_a_table_seed_re_rolls_that_table_and_what_depends_on_it(coffee, coffee_rows):
    rolled = coffee.generate(table_seeds={"customers": 1})
    below = descendants(coffee.refs, "customers")
    for table in set(coffee.tables) - {"customers"} - below:
        assert moved({table: coffee_rows[table]}, rolled) == [], table
    assert not coffee_rows["customers"]["email"].equals(rolled["customers"]["email"])
    # a child's own columns do not follow its parent's re-roll: only its keys, and the dates
    # that are ordered after the parent's
    for column in ("channel", "status", "total_amount"):
        assert coffee_rows["orders"][column].equals(rolled["orders"][column])


# ---------------------------------------------------------
# The days after the first
# ---------------------------------------------------------
DAILY = ROOT / "examples" / "ecommerce_daily.model2data.yml"


@pytest.mark.parametrize("kind", ["text", "enum", "person", "date"])
def test_a_new_column_moves_no_column_on_the_days_after_the_first(kind):
    engine = to_engine(load(DAILY))
    before = generate_days(engine, 4, base_rows=60, seed=3, as_of=AS_OF)

    grown = copy.deepcopy(engine)
    columns = grown.tables["orders"].columns
    columns.insert(len(columns) // 2, KINDS[kind]())
    after = generate_days(grown, 4, base_rows=60, seed=3, as_of=AS_OF)

    assert sum(len(day.tables["orders"].inserted) for day in before) > 60
    for old, new in zip(before, after, strict=True):
        assert (
            moved(
                {name: table.state for name, table in old.tables.items()},
                {name: table.state for name, table in new.tables.items()},
            )
            == []
        ), old.day
