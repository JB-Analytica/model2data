# The model file

A model is one YAML document, `<name>.model2data.yml` — or the same document as JSON. Its
format is [spec 0.10.0](../model2data/spec/README.md), with a JSON Schema
([`model.schema.json`](../model2data/spec/model.schema.json)) your editor can autocomplete and check
against:

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/0.2.0/model.schema.json
model2data: 0.2.0
name: coffee_webshop

enums:
  order_status: [pending, paid, shipped, delivered, cancelled]

tables:
  customers:
    description: One row per registered customer account
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, unique: true, not_null: true}
      phone:
        type: phone_number
        generate: {null_rate: 0.4}

  orders:
    columns:
      id: {type: bigint, pk: true}
      customer_id:
        type: bigint
        not_null: true
        references: customers.id
        generate: {skew: 0.8}
      total_amount:
        type: numeric
        not_null: true
        generate:
          min: 10
          max: 5000
          distribution: {kind: lognormal, median: 120, spread: 0.7}
      status:
        type: order_status
        generate:
          weights: {delivered: 20, cancelled: 1}

run:
  seed: 1
  as_of: 2026-01-01
```

A column's `type` is an SQL type, an enum of the model, or a generator (`email`, `first_name`,
`ean13`, ...). `generate` holds its hints, each a typed value the schema documents. `run` saves
the generation settings with the model; every CLI option overrides the one it names. The
[reference example](../model2data/spec/examples/coffee_webshop.model2data.yml) is a complete model, and the
[sports league](../model2data/spec/examples/sports_league.model2data.yml) shows what 0.7.0, 0.8.0 and 0.9.0 add
(derived columns, rules, date windows, combinations, kick-off days and times, Poisson goals,
squads of 18 to 25). Every schema in [`examples/`](../examples/) comes as one.

Check a model without generating anything — every issue is printed with its path in the document:

```bash
model2data validate examples/ecommerce.model2data.yml
```

A warning is not an error, and some name the fix. `can fall before customers.created_at ... add
`after: customers.created_at`` means a child's date can precede its parent's creation; the
`after` it names (see [When things happen](time-shapes.md)) is the edit, and spec 0.5.0 the
version to write. Integer primary keys number the rows 1..N, and every output lists them in that
order.

A reference onto a parent's column that is no key warns that nothing makes its values one row
each, unless the table also has a foreign key to that parent's key: then the value is copied from
the row's own parent (`unit_price: {references: products.price}` beside `product_id: {references:
products.id}` gives every line its product's price), and nothing is said. With two foreign keys
onto that parent, `via` (spec 0.10.0) names the one the copy follows: `venue_id: {references: {to:
teams.venue_id, via: home_team_id}}`. Without it the model is refused, naming every choice.

## Totals that match their lines

A parent's total drawn on its own never equals the sum of its child rows. `rollup` (spec 0.6.0)
computes it from them instead, and `at_least` on the child's foreign key gives every parent rows
to add up:

```yaml
model2data: 0.6.0
tables:
  orders:
    columns:
      id: {type: bigint, pk: true}
      total_amount:
        type: numeric(12,2)
        not_null: true
        generate: {rollup: {of: order_items, sum: [quantity, unit_price]}}
  order_items:
    columns:
      id: {type: bigint, pk: true}
      order_id: {type: bigint, not_null: true, references: orders.id, generate: {at_least: 1}}
      product_id: {type: bigint, not_null: true, references: products.id}
      quantity: {type: integer, not_null: true, generate: {min: 1, max: 6}}
      unit_price: {type: "numeric(10,2)", not_null: true, references: products.price}
```

- **One aggregate per rollup:** `sum: column`; `sum: [a, b]`, the columns multiplied on each child
  row and added (rounded once per parent, to the summed columns' most decimal places); `count:
  true`; `count_distinct: column`; `min: column` or `max: column` (numeric, date or timestamp).
  A parent with no child rows holds 0 for a sum or count, null for min or max.
- **Which rows:** the child table must reach the parent through exactly one foreign key onto its
  key; with two (`billing_order_id`, `shipping_order_id`), `via: <column>` picks one. A rollup
  may read another rollup's column (a customer's lifetime value as the sum of its orders' totals).
- **What it may not be:** a key, unique or foreign key, a column with another value hint (`min`,
  `max`, `distribution`, `null_rate`, ...), or a column anything reads while tables generate (a
  `when`, an `after`, a reference). `count` needs an integer column; a sum of a decimal column a
  decimal one. `validate` names each of these.
- **Nothing else moves:** rollups are computed once every table exists, and draw nothing, so
  every other column is what it is without them. With `--days`, a rollup over a table with
  `incremental` is refused: later days would not keep it.
- **`at_least: N`** places N × parents rows first, each parent N times at random positions,
  and draws the rest as usual. Too few rows is not an error: the summary lists `order_items: 100
  rows cover 100 of 250 orders; at_least 1 on order_id needs 250`. A row so placed keeps its
  parent; an `after` on the parent's date moves the row's date instead.
- **`at_most: N`** (spec version 0.9.0) caps every parent at N rows: a value is drawn as usual, and a
  parent already holding N is drawn again among the parents with room. `{at_least: 18, at_most:
  25}` on `players.club_id` gives every club a squad of 18 to 25. More rows than N × parents is
  an error, from `validate` when `run` gives both tables' rows, from the generator otherwise
  (`--rows` included): rows are never dropped. A rule draws such a column again among the
  parents with room; `after` and a composite key's repair leave its rows' parents alone. Not with
  `incremental` under `--days`. The dbt project tests it with `model2data_max_rows_per_value`
  when hint tests are on.

The generated dbt project tests every rollup with `model2data_rollup` (see
[The generated dbt project](dbt-project.md)).

## Rows that must obey a rule

Columns are drawn one by one, so a game can have the same team at home and away, or end level
in a sport with no draws. `rules` (spec 0.7.0) on a table lists comparisons every row
satisfies:

```yaml
model2data: 0.7.0
tables:
  games:
    rules:
      - {home_team_id: {ne: {column: away_team_id}}}   # a team never plays itself
      - {home_score: {ne: {column: away_score}}}       # no draws
      - {played_at: {lte: {run: as_of}}}               # nothing is dated in the future
    columns:
      id: {type: bigint, pk: true}
      home_team_id: {type: bigint, not_null: true, references: teams.id}
      away_team_id: {type: bigint, not_null: true, references: teams.id}
      home_score: {type: int, generate: {min: 0, max: 5}}
      away_score: {type: int, generate: {min: 0, max: 5}}
      played_at: timestamp
```

- **The comparison:** `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `between` and
  `is_null`, with a literal, `{column: other_column}` (that column in the same row) or
  `{run: as_of}` (the run's anchor) on the right. Order operators compare numbers, dates and
  timestamps; a number is not compared with a date or an enum with text. A metric's filter
  (`where`, in the metrics file) takes `{column: ...}` the same way (metrics spec 0.2.0).
- **How a row is fixed:** a row that breaks a rule has the column that comes later in the table
  drawn again (`away_team_id`, `away_score`), up to 20 rounds. A foreign key still names a parent
  that exists, a column with `when` keeps its nulls, and only the rows that broke a rule change:
  a table without rules generates the same bytes as before, and so does every other table.
- **Nulls:** a row where a column the rule reads is null is not held to it, as with an SQL
  check constraint.
- **Not forced:** a key, a rollup, a foreign key with `at_least` and a column a `when` names are
  never drawn again (a foreign key with `at_most` is, among the parents with room); a rule the values cannot satisfy (two columns that can each hold one value
  and must differ) is listed in the summary with its number of rows, not an error. `validate`
  names a rule that can never hold (`gt` between columns whose `min` and `max` leave no room).
- **Days:** with `--days`, a table with rules and `incremental` is refused: the rules hold for
  the first day's rows only. A rule across a foreign key (`via`) is not supported yet.

## A row for every combination

Random draws and a unique key cannot promise that every team plays every other team at home and
away. `combinations` (spec 0.7.0) on a table makes its rows one for every combination of the
parent rows its foreign keys point at, less the combinations its rules exclude:

```yaml
model2data: 0.7.0
tables:
  games:
    combinations: [home_team_id, away_team_id]
    rules:
      - {home_team_id: {ne: {column: away_team_id}}}
    columns:
      id: {type: bigint, pk: true}
      home_team_id: {type: bigint, not_null: true, references: teams.id}
      away_team_id: {type: bigint, not_null: true, references: teams.id}
```

With 16 teams that is 16 x 16 - 16 = 240 games. It is as general as it is sporty: every product
in every store, every employee for every review cycle, every pair of currencies.

- **The rows:** the product of the parents' primary-key values, in the parents' row order with
  the first listed column outermost. Two or more columns, onto the same parent or different ones.
- **The row count is not yours to set:** it follows from the parents, so `run.rows_per_table` and
  `--rows-for` naming the table are errors, and `run.rows` does not apply to it. More than
  1,000,000 combinations is refused, with the parents' sizes.
- **Rules:** a rule that reads only the combination columns is applied to the combinations,
  before anything else is generated, and removes the ones that fail it. A rule that reads other
  columns works as above, and never draws a combination column again.
- **The other columns** are generated as usual for that row count: `derive`, `when`, date windows
  and rules all work on them, as in the league above.
- **A unique key:** the listed columns are a key together, so the dbt export tests them as one,
  like any composite unique key. They are never null and take no hint, `when`, `derive`,
  `at_least`, `pk` or `unique` of their own.
- **Order and cycles:** the parents are generated first, so a combination over the table itself
  or over a table that depends on it is an error. Days: with `--days`, a table with
  `combinations` and `incremental` is refused.

## Dates that lie ahead, and values that follow from others

By default every date a model draws falls before the run's `as_of`, so a schedule has nothing to
stand on. `min` and `max` on a date or timestamp column (spec 0.7.0) set its window, and `derive`
makes a column the consequence of others:

```yaml
model2data: 0.7.0
enums:
  game_status: [final, scheduled]
tables:
  games:
    rules:
      - {home_team_id: {ne: {column: away_team_id}}}
      - {home_score: {ne: {column: away_score}}}
    columns:
      id: {type: bigint, pk: true}
      home_team_id: {type: bigint, not_null: true, references: teams.id}
      away_team_id: {type: bigint, not_null: true, references: teams.id}
      tip_off_at:
        type: timestamp
        not_null: true
        generate: {min: "2026-10-02", max: "2027-04-30"}
      game_date:
        type: date
        generate: {derive: {date_of: tip_off_at}}
      status:
        type: game_status
        generate:
          derive:
            cases:
              - {if: {tip_off_at: {lte: {run: as_of}}}, then: final}
            else: scheduled
      home_score: {type: int, generate: {min: 55, max: 125, when: {status: [final]}}}
      away_score: {type: int, generate: {min: 55, max: 125, when: {status: [final]}}}
      winner_team_id:
        type: bigint
        references: teams.id
        generate:
          when: {status: [final]}
          derive:
            cases:
              - {if: {home_score: {gt: {column: away_score}}}, then: {column: home_team_id}}
            else: {column: away_team_id}
```

Games before the run's day are `final` with scores and a winner; later ones are `scheduled` and
have none.

- **Windows:** `min` and `max` are ISO 8601 strings and may lie after `as_of`. A date alone as a
  timestamp's `max` is the end of that day. With one bound alone the other keeps its default
  (a year for a timestamp, two for a date, up to `as_of`). `growth`, `seasonality` and
  `business_hours` shape the draw inside the window, and `after` still holds; `validate` reports
  a `max` before the first day of the column it follows.
- **`derive`:** `cases` (the first `if` that holds gives the `then`, else the `else`, null when
  there is none), `copy: column` or `date_of: column`. An `if` is the comparison of a rule, so
  `{column: ...}` and `{run: as_of}` work; a value is a literal, `{column: x}` or `{run: as_of}`.
  A derived column draws nothing, is no key and takes no other value hint; a derived foreign
  key must come from a foreign key onto the same parent.
- **The order:** every column that is not derived is drawn, then the derived columns and the
  `when`s that read or are read by them are computed in dependency order, then the rules are met
  (a rule draws again the columns it reads that are not derived, and the derived columns follow
  after each round), and the derived columns are computed once more. What a table holds is
  therefore final: every derived value is its derivation over the final values of its columns,
  every `when` follows the final values it names, and every rule holds where it can.
- **Days:** a table with `incremental` that has a derived column or a window cannot be
  generated with `--days`: the first day's rows only are covered. Nothing may follow a derived
  column with `after`, and `validate` reports a derivation loop.

## Weekdays and clock times

A timestamp is drawn evenly across the 24 hours (or in office hours with `business_hours`), so a
fixture can kick off at 03:00 on a Tuesday. `days` and `times` (spec 0.8.0) say when a column's
values fall:

```yaml
model2data: 0.8.0
tables:
  fixtures:
    columns:
      id: {type: bigint, pk: true}
      kickoff_at:
        type: timestamp
        not_null: true
        generate:
          min: "2026-08-15"
          max: "2027-05-15"
          days: {fri: 1, sat: 3, sun: 3}                # weighted weekdays
          times: {"16:00": 1, "18:30": 1, "20:45": 2}   # weighted exact clock times
  orders:
    columns:
      id: {type: bigint, pk: true}
      placed_at:
        type: timestamp
        generate:
          days: [mon, tue, wed, thu, fri, sat]          # open six days a week
          times: ["09:00-18:00"]                        # a range: even within it
```

- **`days`:** a list of weekdays (`mon` to `sun`, equal weights) or a mapping of weekday to a
  positive weight. Other weekdays are never drawn; `growth` and `seasonality` still weigh the
  listed ones. On date and timestamp columns.
- **`times`:** a list or a mapping of entry to weight; an entry is an exact time (`"20:45"`,
  `"20:45:30"`), which the value takes exactly, or a range (`"09:00-18:00"`, start included, end
  excluded, `"24:00"` at the latest, never past midnight). On timestamp columns.
- **With other hints:** they hold in the run's window and a column's own `min`/`max`, after what
  a column follows (`after`), on rows a `when` fills, and on values a rule draws again. `days`
  replaces the weekend thinning of the run's `business_hours` for the column and `times` its
  working hours; beside the column's own `business_hours: true` either is an error, and so is
  either on a key, unique or foreign-key column, beside `derive` or `rollup`, or on a table with
  `incremental` generated with `--days`. `validate` reports a window with no allowed moment.
- **dbt:** `days` writes a `model2data_weekdays` test and `times` a `model2data_times_of_day` test.

## DBML is supported input

`--file` also takes a `.dbml` file, which model2data converts to the same model before generating
(parsed with [pydbml](https://github.com/Vanderhoof/PyDBML)). Hints written as a JSON note on a
column (`[note: '{"min": 1, "max": 5}']`) become its `generate`; any other note is its
description. `convert` writes the model a DBML file converts to, for you to keep:

```bash
model2data convert examples/ecommerce.dbml -o ecommerce.model2data.yml
```

The spec's [From 0.1](../model2data/spec/README.md#from-01) section lists how each DBML feature
converts. A few newer or rarer DBML features — `check` constraints, `Records`, `TablePartial`,
unquoted non-ASCII names — can't be read yet, and are refused with the line and what to change.
