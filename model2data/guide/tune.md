# model2data reference

## Commands

| Command | Does | Exit |
|---|---|---|
| `model2data --file M` | same as `model2data generate --file M` | see below |
| `model2data validate M...` | check models against spec 0.7.0, `*.metrics.yml` files against the metrics spec and their model (`--model M`, else the model beside it with the same stem), and `*.dashboard.yml` files as `dashboard check` does | 0 conforms (warnings allowed), 1 any error |
| `model2data convert M` | print M as `.model2data.yml`; `-o FILE` writes it, `--force` overwrites | 0 ok, 1 unreadable model or output exists |
| `model2data metrics list -f M [--metrics F]` | every metric: the file's, then the inferred ones | 0, 1 on a model or metrics error |
| `model2data metrics export -f M [--metrics F] --to ossie\|lightdash` | the model and its metrics as Apache Ossie 0.1.1, or as Lightdash meta in one dbt properties file for the staging models; `-o FILE` writes it, `--force` overwrites | 0, 1 on an error or an existing output, 2 on another `--to` |
| `model2data dashboard check D... [--model M] [--metrics F]` | check `*.dashboard.yml` files against the dashboard spec and the names of their model and metrics; prints `file:line:column: error\|warning: message` | 0 (warnings allowed), 1 any error |
| `model2data guide [TOPIC]` | this page; topics `setup`, `triage`, `tune`, `dashboards` | 0 |

`generate` exits 0 on success; 1 on a model error, an existing `dbt_<name>/` without
`--force`, or a bad `--adapter` / `--hint-tests` value; 2 on a value the option parser
rejects (`--rows 5`, an unknown table in `--rows-for`, a bad `--defects` or `--days-format`).

`validate` also takes `--glob '**/*.model2data.yml'` (repeatable, no shell globstar
needed), `--format github` (pull-request annotations) and `--require-files` (exit 1 when
nothing matched).

## generate options

Every option overrides the model's `run` setting of the same meaning.

| Option | `run` key | Default |
|---|---|---|
| `--rows N` (min 10) | `rows` | 100 |
| `--rows-for TABLE=N` (repeatable, may go below 10) | `rows_per_table` | |
| `--seed N` | `seed` | random |
| `--table-seed TABLE=N` (needs a seed) | `table_seeds` | |
| `--as-of YYYY-MM-DD` | `as_of` | today |
| `--locale en_GB` | `locale` | en_US |
| `--business-hours`, `--growth X`, `--seasonality 0..1`, `--skew 0..1` | `shape.*` | flat, even |
| `--defects clean\|messy\|training\|none` | `defects` | clean |
| `--days N`, `--next` (= `--days 1`), `--days-format batches\|changelog\|final` | | |
| `--adapter duckdb\|postgres` | | duckdb |
| `--hint-tests error\|warn\|off`, `--test-tolerance X` | | warn, 0.1 |
| `--unit-tests` | | off |
| `--name NAME` (project becomes `dbt_NAME`) | `name` (top level) | the model's `name`, else the file stem |
| `--force` (replace `dbt_<name>/`) | | off |
| `--metrics FILE` (the model's `*.metrics.yml`; never picked up on its own) | | none |

## Generator types

A column's `type` may name a generator instead of an SQL type: `{type: company}`. These
always work: `name`, `first_name`, `last_name`, `user_name`, `email`, `phone_number`,
`company`, `job`, `catch_phrase`, `address`, `street_address`, `city`, `state`, `country`,
`postcode`, `url`, `domain_name`, `ipv4`, `color_name`, `currency_code`, `iban`, `ean13`,
`license_plate`, `uuid4`, `word`, `sentence`. Any other Faker provider name that takes no
arguments works too. A plain `varchar` gets realistic text only when its name contains a
known pattern (`email`, `phone`, `city`, `company`, `first_name`, ...); there is no generic
`name` pattern.

A column without `not_null` (or `pk`) is nullable, and gets nulls in up to a fifth of
its rows unless it sets `generate: {null_rate: X}`; that includes enum and generator
columns. Add `not_null` to every column that must always have a value.

## Model keys

- Column: `type`, `pk`, `unique`, `not_null`, `increment`, `default`, `description`,
  `references`, `measure`, `generate`.
- `generate`: `min`, `max`, `distribution`, `null_rate`, `weights`, `true_rate`,
  `distinct`, `skew`, `after`, `business_hours`, `growth`, `seasonality`, `transitions`,
  `when`, `rollup`, `at_least`, `derive`.
- Table: `columns`, `description`, `color`, `role`, `grain`, `keys`, `foreign_keys`,
  `incremental`, `rules`, `defects`.
- Top level: `model2data` (spec version), `name`, `description`, `enums`, `tables`,
  `relationships`, `groups`, `run`.

Each key in use (this model validates):

```yaml
model2data: 0.7.0
name: saas
enums:
  plan_tier: [free, pro, enterprise]
  sub_status: [trial, active, cancelled]
tables:
  accounts:
    columns:
      id: {type: bigint, pk: true}
      company: company
      tier: {type: plan_tier, generate: {weights: {free: 5, pro: 3, enterprise: 1}}}
      is_verified: {type: boolean, generate: {true_rate: 0.8}}
      region: {type: city, generate: {distinct: 4}}
      created_at: {type: timestamp, not_null: true}
      billed_total:
        type: numeric(12,2)
        not_null: true
        generate: {rollup: {of: invoices, sum: amount}}
  invoices:
    columns:
      id: {type: bigint, pk: true}
      account_id: {type: bigint, not_null: true, references: accounts.id, generate: {at_least: 1}}
      amount: {type: "numeric(10,2)", not_null: true, generate: {min: 10, max: 900}}
  subscriptions:
    incremental: {new_per_day: 5, update_rate: 0.1, changes: [status], updated_at: updated_at}
    keys: [{unique: [account_id, started_at]}]
    columns:
      id: {type: bigint, pk: true}
      account_id: {type: bigint, not_null: true, references: accounts.id, generate: {skew: 0.7}}
      status:
        type: sub_status
        generate: {transitions: {trial: [active, cancelled], active: [cancelled]}}
      seats: {type: integer, generate: {min: 1, max: 500, distribution: {kind: lognormal, median: 10}}}
      started_at:
        type: timestamp
        not_null: true
        generate: {business_hours: true, growth: 0.5, after: accounts.created_at}
      cancelled_at: {type: timestamp, generate: {after: started_at, when: {status: [cancelled]}}}
      updated_at: {type: timestamp, generate: {after: started_at}}
    defects:
      - {type: nulls, column: account_id, count: 2}
run:
  rows: 100
  rows_per_table: {accounts: 20}
  seed: 7
  table_seeds: {subscriptions: 2}
  as_of: 2026-01-01
  shape: {seasonality: 0.3}
```

`transitions` act only on days after the first: it needs `incremental` and `--days`.

`when` (added in spec version 0.4.0, which the model then names) ties a column to another column's value: `cancelled_at` above holds a timestamp on
every `cancelled` row and is null on every other one, so no `active` subscription has a
`cancelled_at`. Name another column of the table (an enum, boolean, number or text column)
and list the values it must hold; several columns must all match. Use it for every
lifecycle column: `completed_at` with `{status: [done]}`, `shipped_at` with
`{status: [shipped, delivered]}`, `resolved_at`, `end_date`. The column must be nullable,
without a `default`, and not a key, unique or a foreign key. A `null_rate` beside it counts
only the matching rows. On later days a row whose status moves into a listed value gets a
value that day (its `updated_at` when the table has one), and one moving out loses it. It
writes the dbt test `model2data_when`.

`after` also names a parent's column as `<table>.<column>`: `started_at` above is on or after
the `created_at` of the account its `account_id` points at (added in spec version 0.5.0, so
the model says at least `model2data: 0.5.0`). The table must reach that parent through exactly one one-column foreign
key onto its key; a list (`after: [started_at, accounts.created_at]`) follows each entry. A
row dated before its parent takes another parent created by then, so the column keeps its
growth and seasonality; with none (or a one-to-one) its date moves. A null foreign key
constrains nothing. Use it whenever a child cannot exist before its parent (an order before its
customer). Two foreign keys to one parent (`sender_id`, `receiver_id`) are an error: choosing
between them is not built yet. `validate` warns where a child's first date can fall before its parent's
`created_at`-like date and names the `after` to add. It writes the dbt test
`model2data_not_before_parent`. Integer primary keys number the rows 1..N, and every output
lists the rows in key order.

`rollup` (added in spec version 0.6.0, which the model then names) makes a parent's column the
aggregate of its child rows, so totals reconcile: `billed_total` above is the sum of the
account's invoice `amount`s. One aggregate: `sum: column`, `sum: [quantity, unit_price]`
(multiplied per child row, then added; rounded once per parent to the summed columns' most
decimal places), `count: true`, `count_distinct: column`, `min: column`, `max: column` (numeric,
date or timestamp). A parent with no children holds 0 (sum, count) or null (min, max). The child
must reach the parent through one foreign key onto its key, or `via: <child fk column>` names it.
The column takes no other value hint (`min`, `max`, `distribution`, `null_rate`, ...), is no key
or foreign key, and nothing may read it while tables generate (a `when`, an `after`, a reference
onto it). A count needs an integer column, a sum of a decimal column a decimal one. It writes
the dbt test `model2data_rollup`, always at severity error. A rollup over a table with
`incremental` cannot run with `--days`.

`at_least: N` on a foreign key gives every parent at least N child rows (`account_id` above:
no account without an invoice). Pair it with a rollup so no total is 0 by accident. With fewer
rows than N × parents the summary lists the shortfall; raise the table's rows. Not on a unique
foreign key.

`rules` on a table (spec 0.7.0, which the model then names) lists comparisons every row
satisfies, between columns of the same row: `rules: [{home_team_id: {ne: {column: away_team_id}}},
{home_score: {ne: {column: away_score}}}]`. Operators: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`,
`lte`, `between`, `is_null`; the right-hand side is a literal, `{column: other_column}` or
`{run: as_of}` (the run's anchor); order operators compare numbers, dates and timestamps only.
After a table is drawn, a row that breaks a rule has the later of its columns drawn again (a foreign
key still names a parent), up to 20 rounds; a rule a column's value space cannot meet is listed
in the summary. A row where a column the rule reads is null is not held to it. Keys, rollups and
a column a `when` names are not drawn again. A rule across a foreign key (`via`) is not supported
yet, and a table with rules and `incremental` cannot run with `--days`.

`min` and `max` on a date or timestamp column (spec 0.7.0) are ISO strings that fix the window its
values fall in, and may lie after the run's `as_of`: `tip_off_at: {type: timestamp, generate: {min:
"2026-10-02", max: "2027-04-30"}}` schedules a season past today. A date alone as a timestamp's
`max` is the end of that day; with one bound alone the other keeps its default (up to `as_of`).
`growth`, `seasonality` and `business_hours` shape the draw inside the window, and `after` still
holds. A table with a window and `incremental` cannot run with `--days`.

`derive` (spec 0.7.0) makes a column a consequence of others of its row, drawing nothing:
`game_date: {type: date, generate: {derive: {date_of: tip_off_at}}}`, `copy: other_column`, or
`cases`, the first `{if: <comparison>, then: <value>}` that holds on the row giving the value and
`else` the value when none does (null when left out); a value is a literal, `{column: x}` or
`{run: as_of}`, as in `status: {type: game_status, generate: {derive: {cases: [{if: {tip_off_at:
{lte: {run: as_of}}}, then: final}], else: scheduled}}}`. With `when`, `when` says where it is
null. A derived foreign key must come from another foreign key onto the same parent
(`winner_team_id` from `home_team_id` or `away_team_id`). It is no key and takes no other value
hint, nothing reads it with `after`, and derivations do not read each other in a loop. The table
holds every derived value over the final values of the columns it reads, every `when` over the
final values of what it names, and every rule that can be met; a table with `derive` and
`incremental` cannot run with `--days`.

A reference onto a parent's plain column next to a foreign key to the same parent
(`unit_price: {references: products.price}` beside `product_id: {references: products.id}`)
copies the value from the row's own product.

The full spec and JSON Schema ship in the package: `model2data/spec/README.md`,
`model2data/spec/model.schema.json`.

## Output

`dbt_<name>/` in the current directory: `seeds/raw/<table>.csv`, `models/staging/`,
`profiles.yml`, a copy of the model, and with defects `defects_report.json` and
`EXPECTED_FAILURES.md`. Same model, `run`, options and engine version give the same bytes.

With `--days N` the seeds hold the state after the last day, and `--days-format` adds:

- `batches` (default): `dbt_<name>/days/<table>/day_000.csv` is the whole table on the
  first day; each later `day_NNN.csv` holds that day's inserted rows, then its updated
  rows with their new values. Loading the days in order is an upsert on the key.
- `changelog`: `dbt_<name>/changelog/<table>.csv`, every row version with `_day` and `_op`.
- `final`: nothing beyond the seeds.

Only tables with `incremental` change after the first day.

With `--metrics FILE`, and nothing else changed: `metric_values.json` (each metric's known
value over the seeds, overall and by month), `data-tests/metrics/metric_<name>.sql` (a dbt
test per metric that fails unless the warehouse gets that value) and `osi/<name>.yml`
(Apache Ossie 0.1.1). With `--lightdash` (with or without `--metrics`), the staging models'
`stg_<table>.yml` also carry Lightdash metrics, dimensions and joins under `config.meta`; what
Lightdash cannot say is listed in the summary.

## Metrics file

`<stem>.metrics.yml` beside the model, version 0.1.0 of its own spec
(`model2data/spec/metrics/README.md`, `metrics.schema.json` in the package). Top level:
`model2data-metrics` (0.1.0), `model` (the model's `name`), `metrics`, `dimensions`, `infer`.
A metric (name `[a-z][a-z0-9_]*`) has exactly one of `measure` (`table.column`, aggregated
by `agg`: `sum`, `average`, `min`, `max`, `median`, `count`, `count_distinct`, or else by
the column's own `measure`), `count` (a table: its rows), `ratio` (`{numerator, denominator}`,
two metric names) or `expression` (metric names, numbers, `+ - * /`, parentheses), and may
have `label`, `description`, `ai_context`, `format` (`number`, `currency`, `percent`: a fraction, 0.6 shows as 60%, never `* 100`), and,
with `measure` or `count`, `time` (a date or timestamp column) and `where`. `where` maps
`table.column` to a value (equals), a list (one of) or operators `eq`, `ne`, `in`,
`not_in`, `gt`, `gte`, `lt`, `lte`, `between: [a, b]`, `is_null: true`; `any: [...]` and
`all: [...]` combine filters. A null fails every condition but `is_null: true`. A filter
column is on the metric's table or reached through `references` child to parent along
exactly one path. Order operators only on numbers, dates and timestamps. Every `measure`
column also yields a metric `<table>_<column>` and a fact `<table>_count`, unless
`infer: false`; a metric of the same name in the file replaces it. `dimensions` maps a
column to `false` (never a dimension), `true`, or `{label, description}`.

next: `model2data guide triage`
