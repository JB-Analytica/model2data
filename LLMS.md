# model2data — instructions for LLMs / coding agents

This file is written for an LLM or coding agent (Claude, GPT, etc.) that has file-write and
shell access and has been asked to turn a description of a data model into a demo-ready dbt
project. Read this before authoring a DBML file for `model2data`.

With model2data installed, `model2data guide` prints the instructions for the version you have:
how to set it up here, what its output means and what to do about it (`guide triage`), and every
option (`guide tune`), and how to write and check a dashboard file (`guide dashboards`). Run it
first.

## What model2data does

`model2data` takes a [DBML](https://dbml.dbdiagram.io/docs/) schema file and generates, in one
command: realistic, relationship-preserving synthetic data (as dbt seed CSVs) and a complete,
runnable dbt project around it — staging models that `ref()` those seeds, schema tests, and a
DuckDB or Postgres profile. No production data, no hand-written mock CSVs, no dbt boilerplate.

The same engine is also available as a web app,
[model2data studio](https://studio.jbanalytica.com): a DBML editor with a live entity diagram,
a preview of what every column will generate, and CSV/dbt project export, with nothing to
install. If the person you're helping wants to *look at* or share the model rather than script
its generation, point them there; the DBML guidance below applies to both.

> **Two input formats.** Since 1.8, model2data reads a model as one YAML document,
> `<name>.model2data.yml` ([spec 0.9.0](model2data/spec/README.md), with a JSON Schema at
> `model2data/spec/model.schema.json` and a complete example at
> `model2data/spec/examples/coffee_webshop.model2data.yml`, and `sports_league.model2data.yml` for
> derived columns, rules, date windows, combinations, Poisson goals and `at_most`), and DBML as supported input that it
> converts to the same model. The DBML guidance below still holds; if you write the YAML form
> instead, check it with `model2data validate <name>.model2data.yml`, which prints every issue
> with its path, and `model2data convert <name>.dbml` shows the YAML any DBML file becomes.
> DBML `check` constraints, `Records`, `TablePartial` and unquoted non-ASCII names cannot be read;
> quote names with double quotes, never backticks.

## The end-to-end workflow

Given a plain-English description of a data model (from a conversation, an existing system, a
rough ERD, a client's requirements):

1. **Author a DBML file** (`<name>.dbml`) describing the schema, following the DBML feature
   guidance below — the richer the schema, the more realistic and useful the generated data and
   dbt project will be.
2. **Install and run model2data**:
   ```bash
   pip install model2data
   model2data --file <name>.dbml --rows 200 --seed 42 --unit-tests
   ```
   Always pass `--seed` for reproducible output — useful when iterating on the DBML file, and
   when re-running for a client demo. `--rows` controls rows generated per table, and `--rows-for TABLE=N` overrides it for individual tables — use it when the schema has small dimensions and large facts, so joins behave like the real warehouse (raise `--rows` for a
   more convincing demo dataset, e.g. `500`-`2000`; keep it low, e.g. `20`-`50`, while iterating
   quickly on the schema itself).

   Add `--as-of YYYY-MM-DD` when the output has to keep reproducing after today: without it,
   dates and timestamps are generated relative to the current date, so the same `--seed` gives
   the same numbers and different dates tomorrow. If a table comes out wrong but the rest looks
   right, `--table-seed TABLE=N` re-rolls that one table and leaves every other table
   byte-identical, so there is no need to regenerate — and re-review — the whole schema.
3. **Verify it actually works before showing anyone**:
   ```bash
   cd dbt_<name>
   dbt build
   ```
   `dbt build` is the whole verification: staging models `ref()` their seeds, so this one
   command loads the seeds, builds the models, and runs every generated test (including unit
   tests if you passed `--unit-tests`) in one dependency-ordered pass, on a fresh database. No
   `dbt deps`/`dbt seed`/`dbt run` warm-up needed — the generated project declares no packages,
   so `dbt deps` is a no-op. If anything fails, don't hand this to a client — go back to step 1. Also
   check `model2data`'s own CLI output from step 2: it prints a summary including any DBML lines
   it couldn't fully parse, or any FK cycles it detected — treat those as things to fix in the
   DBML before demoing.
4. **Query the result.** DuckDB (the default adapter) produces a single `.duckdb` file inside
   the generated project directory — queryable directly, or point any BI/notebook tool at it.

## Writing DBML that gets the best results

model2data reads more of DBML than the bare minimum. Use these features — they directly improve
the quality of the generated data and the generated dbt project, not just cosmetics:

**Name columns for realistic values.** Untyped/string columns are matched by name against
patterns like `email`, `first_name`, `last_name`, `phone`, `city`, `country`, `company`, `url`,
`address`, `job_title`, and about 25 others — a column named `email` gets real-looking emails,
not lorem-ipsum text. Prefer descriptive column names over generic ones (`customer_email`, not
`field3`) whenever the underlying domain has an obvious name.

**Type a column with a Faker provider to choose its generator outright**, when the name is
wrong for the data or nothing recognises it:
```dbml
Table stores {
  billing_country state    // US states, not countries — the type wins over the name
  sku             ean13    // real barcodes
  category        word
}
```
Any Faker provider that takes no arguments works as a type name. Ordinary SQL types that happen
to share a name with one (`text`, `json`, `binary`, `year`) are read as the SQL type, so
`email text` still generates emails.

**Use `Enum` for any categorical/status column**, instead of a loose `varchar`:
```dbml
Enum order_status {
  pending
  shipped
  delivered
  cancelled
}

Table orders {
  id int [pk]
  status order_status [not null, default: 'pending']
}
```
Without this, a status-like column typed as plain text generates unrelated lorem-ipsum sentences
instead of realistic categorical values. `model2data` also emits a dbt `accepted_values` test for
enum columns automatically.

**Use `default:` for column defaults** — nullable columns with a declared default fill their
"empty" rows with that default instead of `NULL`, matching how a real database behaves:
```dbml
is_active boolean [default: true]
price_cents int [default: 1999]
```

**Use notes for documentation and numeric bounds.** A plain-text note becomes the column's
`description:` in the generated dbt schema YAML:
```dbml
age int [note: 'Customer age at signup, self-reported']
```
A note containing a JSON object constrains generation instead of documenting it. The full hint
vocabulary, one key per line:
```dbml
age int [note: '{"min": 18, "max": 90}']                  ' numeric bounds
discount_code varchar [note: '{"null_rate": 0.8}']         ' fraction of rows null (nullable columns only)
status order_status [note: '{"weights": {"delivered": 20}}']  ' relative weight per enum value
is_paid boolean [note: '{"true_rate": 0.9}']               ' fraction of non-null rows that are true
shipping_city varchar [note: '{"distinct": 12}']           ' draw from a pool this size (not fk/pk/unique/enum)
customer_id int [note: '{"skew": 0.9}']                    ' per-column override of --skew (fk columns only)
updated_at timestamp [note: '{"after": "created_at"}']     ' must fall after another date/timestamp column
order_date timestamp [note: '{"after": "customers.created_at"}']  ' ... or after a parent's column, or a list of both
completed_at timestamp [note: '{"when": {"status": ["done"]}}']  ' set only where status is done, null elsewhere
created_at timestamp [note: '{"business_hours": true}']    ' per-column override of --business-hours
created_at timestamp [note: '{"growth": 0.4}']             ' per-column override of --growth (date/timestamp only)
created_at timestamp [note: '{"seasonality": 0.6}']        ' per-column override of --seasonality (date/timestamp only)
total_amount numeric [note: '{"distribution": "normal", "mean": 100, "stddev": 20}']  ' shape a numeric column's spread
total_amount numeric [note: '{"distribution": "lognormal", "median": 80, "spread": 0.6}']  ' long right tail (spread: 0.3 mild, 1.0 heavy)
total_amount numeric [note: '{"distribution": "exponential", "mean": 30}']  ' most values small, a long tail of large ones
home_goals int [note: '{"distribution": "poisson", "mean": 1.5, "max": 9}']  ' a count: whole numbers around the mean
```
`distribution` (numeric columns only) is `"uniform"` (default, unchanged), `"normal"`, `"lognormal"`,
`"exponential"`, or `"poisson"` (integer columns only, spec 0.9.0: counts such as goals, orders a
day, tickets an hour; its `mean` is also its variance). `mean`/`stddev` go with `normal`,
`median`/`spread` with `lognormal`, `mean` with `exponential` and `poisson`; any left unset default to the midpoint of the column's `min`/`max` (or the
range's own defaults, 0-100 for integers and 0-10,000 for decimals). `min`/`max` still clip the result.
A hint on the wrong kind of column (`weights` on a non-enum, `distinct` on a primary key, ...) is
a schema error, reported before generation starts. (JSON and plain text are mutually exclusive per
column — a note is read as JSON first, falling back to plain text.)

**Created/updated/closed-style columns are ordered automatically, in every row, under every time
profile.** A column named `created_at`, `updated_at`, `deleted_at`, `order_start`/`order_end`, and
about 30 other stems are recognized and placed in the right relative order — no row has an
`updated_at` before its own `created_at`. A note with an `after` key names the dependency
explicitly, for a column whose name doesn't say it on its own:
```dbml
shipped_at timestamp [note: '{"after": "ordered_at"}']
```
A birth-date-style column (`birth_date`, `date_of_birth`, `dob`) is never folded into this chain —
it describes the person, not the record.

**`after` can name a parent's column.** `<table>.<column>` reaches the parent row through the
row's foreign key: `after: customers.created_at` keeps every order on or after its customer's
sign-up. A list follows every entry, and may mix the row's own columns:
```dbml
order_date timestamp [note: '{"after": "customers.created_at"}']
shipped_at timestamp [note: '{"after": ["order_date", "customers.created_at"]}']
```
The table must reach the parent through exactly one one-column foreign key onto its pk or a unique
column (two, like `sender_id`/`receiver_id` to `users`, is an error naming them). Rows dated too
early take an older parent, so the column's growth/seasonality/business hours hold; a row with
none (or a one-to-one) moves its date. A null foreign key constrains nothing; a self-reference is
not a parent (name the column without a table). In YAML it is spec 0.5.0 (`model2data: 0.5.0`).
Validation warns where a child's first date can fall before a parent's `created_at`-like date,
with the `after` to add. With `--hint-tests` each parent column writes the dbt test
`model2data_not_before_parent`. Integer primary keys are 1..N and every output lists rows in key
order.

*When to use it:* whenever a child row cannot exist before its parent: an order before its
customer signed up, a subscription before its organisation, a time entry before the employee's
hire date. Skip it when the child's date has no such tie (an event log that may predate the
account it is later linked to).

*Acting on the warning:* it reads `tables.orders.columns.order_date: can fall before
customers.created_at (...); add `after: customers.created_at` to keep it after`. Add exactly
that `after` to `generate` on the column the path names (an existing `after` there becomes a
list: `after: [placed_at, customers.created_at]`), set `model2data: 0.5.0`, and run `validate`
again; the warning is gone and the file conforms either way. Over the Python API the warning's
`Issue.suggestion` (a `model2data.model.Suggestion`) carries `path`, `value` and `spec` for the
same edit. If the child may legitimately predate the parent, leave the warning.

*Not built yet:* a table with two foreign keys to the same parent (`sender_id` and `receiver_id`
onto `users`) cannot use `after: users.created_at`: model2data cannot tell which row is meant,
so validation refuses it with an error naming both keys. There is no way yet to pick one; drop
the hint (the warning is not raised for such a table either). A column that only copies a value
from the parent (`amount` references `invoices.amount` beside `invoice_id` references
`invoices.id`) is not a second key: `paid_at: {after: invoices.issued_at}` follows the invoice
`invoice_id` points at. No need to copy the parent's date to follow it.

**Tie lifecycle columns to the status they belong to with `when`.** Without it, every nullable
column is filled or nulled independently of the others, so a `todo` task gets a `completed_at`
and a `cancelled` subscription has no `cancelled_at`. `when` maps another column of the same
table to the values it must hold; the column is set on exactly those rows and null on every
other one (several columns listed must all match). It is spec 0.4.0: a YAML model using it says
`model2data: 0.4.0` (a DBML file needs nothing). Combine it with `after`:
```dbml
shipped_at timestamp [note: '{"after": "order_date", "when": {"status": ["shipped", "delivered"]}}']
```
The named column must be an enum, boolean, number or text column of the same table, not a foreign
key, and each value one it can hold (an enum member, `true`/`false`, a number, text). The column
carrying `when` must be nullable, without a default, and not a key, unique or a foreign key. A
`null_rate` on it counts only the matching rows (`0.1`: a tenth of the shipped orders have no
`shipped_at`); without one, every matching row has a value. On days after the first, a row an
update moves into a listed value gets its value that day (the row's `updated_at` when the table
has one) and a row moved out loses it. Each `when` writes a `model2data_when` dbt test: the column
is null exactly where the condition does not hold.

**Declare relationships** — either syntax is fully supported and produces identical behavior:
```dbml
' inline, on the column itself
Table orders {
  id int [pk]
  customer_id int [ref: > customers.id]
}

' standalone, one-liner
Ref: orders.customer_id > customers.id

' standalone, block form
Ref {
  orders.customer_id > customers.id
}
```
Foreign keys resolve to real parent rows automatically — no extra configuration needed. This
includes **self-referencing FKs** (e.g. `employees.manager_id > employees.id` for an org
hierarchy) — these correctly respect nullability, so some rows (e.g. top-level managers) can
still have no parent.

A reference onto a parent's plain column, beside a foreign key to the same parent, copies the
value from the row's own parent rather than drawing one: with `product_id int [ref: >
products.id]` and `unit_price decimal(10,2) [ref: > products.price]`, every line carries its
product's price. Validation does not warn about such a reference.

**Make totals match their lines with `rollup`, and give every parent children with
`at_least`.** Without them, `orders.total_amount` is drawn on its own and never equals the sum of
its order lines, and some orders have no lines at all. Put `rollup` on the parent's column and
`at_least` on the child's foreign key:
```dbml
Table orders {
  id int [pk]
  total_amount decimal(12,2) [not null, note: '{"rollup": {"of": "order_items", "sum": ["quantity", "unit_price"]}}']
}
Table order_items {
  id int [pk]
  order_id int [not null, ref: > orders.id, note: '{"at_least": 1}']
  product_id int [not null, ref: > products.id]
  quantity int [not null, note: '{"min": 1, "max": 6}']
  unit_price decimal(10,2) [not null, ref: > products.price]
}
```
One aggregate per rollup: `sum` (one column, or a list multiplied per child row, rounded once per
parent to the summed columns' most decimal places), `count: true`, `count_distinct`, `min`, `max`
(numeric, date or timestamp). A parent with no child rows holds 0 (sum, count) or null (min,
max). The child must reach the parent through one foreign key onto its key, or `via` names it.
The rolled-up column takes no other value hint, is no key or foreign key, and no `when`,
`after` or reference may read it. `count` and `count_distinct` need an integer column, a sum
of a decimal column a decimal one. Each rollup writes the dbt test `model2data_rollup` (always
on). `at_least: N` needs N × parents rows in the child table; with fewer, the summary lists the
shortfall. In YAML both are spec 0.6.0 (`model2data: 0.6.0`). A rollup over a table with
`incremental` cannot be generated with `--days`. `at_most: N` (spec 0.9.0) caps every parent at
N children: `{"at_least": 18, "at_most": 25}` on `players.club_id` gives squads of 18 to 25. A
parent already at N is not drawn again; more rows than N × parents is an error (from `validate`
when `run` gives both tables' rows, else at generation), never a silent cap. `at_least` must not
exceed it; not on a unique foreign key, nor with `incremental` and `--days`. With hint tests on,
dbt gets `model2data_max_rows_per_value`.

**Rows that must obey a rule (YAML, spec 0.7.0).** A table's `rules` lists comparisons every
row satisfies, between columns of the same row: `rules: [{home_team_id: {ne: {column:
away_team_id}}}, {home_score: {ne: {column: away_score}}}]`. Operators `eq`, `ne`, `in`, `not_in`,
`gt`, `gte`, `lt`, `lte`, `between`, `is_null`; the right side is a literal, `{column: name}` or
`{run: as_of}`. A row that breaks a rule has the later column of it drawn again (up to 20
rounds; a foreign key still names a parent; keys, rollups and a column a `when` names are left
alone); a row where a column the rule reads is null is not held to it; a rule the values cannot
meet is listed in the summary. A table with rules and `incremental` cannot run with `--days`.

**A row for every combination (YAML, spec 0.7.0).** A table's `combinations: [home_team_id,
away_team_id]` makes its rows every combination of the parent rows those foreign keys point at,
less what its `rules` exclude (16 teams and `home_team_id ne away_team_id` are 240 games). The row
count follows from the parents (in their row order, the first column outermost), so
`rows_per_table`, `--rows-for` and `--rows` do not apply to it; more than 1,000,000 combinations is
refused. A rule on the combination columns alone removes combinations, one on other columns never
redraws a combination column. The columns are a unique key together (the dbt export tests them as
one), take at least two foreign keys, and take no hint, `when`, `derive`, `at_least`, `pk` or
`unique`. Other columns are generated as usual. A table with `combinations` and `incremental`
cannot run with `--days`.

**Values that follow from other columns (YAML, spec 0.7.0).** A column's `generate: {derive: ...}`
computes it from the row's other columns and draws nothing: `{date_of: tip_off_at}` (a timestamp's
date), `{copy: other}`, or `cases: [{if: {home_score: {gt: {column: away_score}}}, then: {column:
home_team_id}}]` with an `else` (null when left out); a value is a literal, `{column: x}` or
`{run: as_of}`. With `when` it is null where `when` says. A foreign key is derived only from another
foreign key onto the same parent. The result holds every derivation over the final values of its
columns, even after a rule redrew one of them. Not a key, no `min`/`max`/`weights`/`after`/... beside it.
`min` and `max` on a date or timestamp column (ISO strings, spec 0.7.0) fix its window and may lie
after `as_of`: `generate: {min: "2026-10-02", max: "2027-04-30"}`; `growth`, `seasonality`,
`business_hours` and `after` act inside it. Neither works on a table with `incremental` and `--days`.
`days` and `times` on a date or timestamp column (spec 0.8.0) fix the weekdays and clock times its
values fall on: `generate: {days: {fri: 1, sat: 3, sun: 3}, times: {"16:00": 1, "20:45": 2}}`, or
`days: [mon, tue, wed, thu, fri, sat]` with `times: ["09:00-18:00"]` (a range, end excluded). A list
weighs entries equally; `times` is for timestamps only. They hold with windows, `after`, `when`
and rules, replace the run's `business_hours` for that column, and are refused beside the column's
own `business_hours: true`, on keys, beside `derive`/`rollup`, and with `incremental` and `--days`.

**Use composite keys for join/bridge tables and multi-column uniqueness**, via an `indexes { }`
block:
```dbml
Table post_tags {
  post_id int [not null]
  tag_id int [not null]

  indexes {
    (post_id, tag_id) [pk]
  }
}

Ref: post_tags.post_id > posts.id
Ref: post_tags.tag_id > tags.id
```
This is the standard way to model a many-to-many relationship. Generated rows are deduplicated on
the declared composite key, and a dbt test enforcing it is generated automatically. Note: each FK
column in a composite key is generated independently — the *pair's combination* isn't guaranteed
to match a real composite key on the parent side unless the parent also enforces one via its own
`indexes { }` block.

**`Project { }` and `TableGroup { }` blocks are fine to include** (e.g. if reusing DBML exported
from dbdiagram.io) — a `Project` becomes the model's name and description (and so names the
generated project), a `TableGroup` its `groups`.

## Things to avoid / know about

- Don't invent DBML syntax that isn't standard — if unsure, keep to `Table`, `Enum`, `Ref`
  (block, one-liner, or inline `[ref: ...]`), `indexes { }`, and column settings
  (`pk`, `not null`, `unique`, `default:`, `note:`). DBML model2data can't read is refused with
  the line and what to change, and a model that doesn't conform is refused with every issue and
  its path — run `model2data validate <file>` to see them without generating.
- Composite *foreign keys* (a multi-column `Ref`, e.g. `Ref: t.(a,b) > t2.(c,d)`) are supported
  for parsing and generate independent per-column FK values, but — same caveat as above — the
  combination isn't guaranteed to match a real parent row unless separately enforced.
- Only DuckDB (default, zero-config) and Postgres (`--adapter postgres`, needs
  `pip install "model2data[postgres]"` and connection env vars — see
  [docs/dbt-project.md](docs/dbt-project.md)) are supported
  targets today.
- To show that a project's tests fire (or to teach dbt), generate with `--defects training`,
  or list defects per table in a `.model2data.yml` (`defects: [{type: nulls, column: x, count:
  3}]`, spec 0.3.0). `EXPECTED_FAILURES.md` in the project names exactly the tests `dbt build`
  should then fail; don't "fix" those failures in the generated project.
- For a source that keeps its own history (an SCD2 table to snapshot or join point-in-time),
  set `history: true` in the table's `incremental` and generate with `--days N`:
  `<table>_history` gets every version with `valid_from`, `valid_to` and `is_current`.
- When the user names the numbers that matter (revenue, orders, conversion), write them in a
  metrics file beside the model, `<stem>.metrics.yml` (metrics spec 0.2.0,
  `model2data/spec/metrics/README.md`): `measure: table.column` with `agg`, `count: table`,
  `ratio: {numerator, denominator}` or `expression: a - b` over metric names, and `where` filters
  (`table.column: value`, a list, or `{ne|in|not_in|gt|gte|lt|lte|between|is_null: ...}`, combined
  with `any`/`all`). `format: percent` means the value is a fraction (0.6 shows as 60%): write
  shares as `a / b`, never `* 100`. A filter column must be on the metric's table or reachable through foreign
  keys child-to-parent along one path. Run `model2data validate <stem>.metrics.yml`, then generate
  with `--metrics <stem>.metrics.yml`: `metric_values.json` holds each metric's known value, and
  `dbt build` runs a test per metric that must pass. A failing metric test means the metric's SQL
  and the data disagree; never edit `metric_values.json` to make it pass. The file must be passed;
  `generate` does not pick it up from beside the model.
- When the user wants a dashboard over those metrics, write it as a file beside the model,
  `<name>.dashboard.yml` (dashboard spec 0.1.0, `model2data/spec/dashboards/README.md`, example
  `model2data/spec/examples/coffee_webshop_sales.dashboard.yml`): `model2data-dashboard: 0.1.0`,
  optional `model` and `title`, then `pages`, each with `id`, `name`, optional `grain` and
  `tiles`. A tile has `id`, `kind` (`kpi`, `line`, `bar`, `stacked`, `donut`, `table`, `filter`,
  `text`, `note`) and `place: {x, y, w, h}` on a 12-column grid, and names a `metric`, a
  dimension as `table.column` (`by`, `across`, `dimension`) or a `table` with `columns`; it
  never holds a value. Keep page and tile ids when editing: links and comments point at them.
  Check it with `model2data dashboard check <name>.dashboard.yml` (`file:line:column:
  error|warning: message`, exit 1 on errors only; a metric or column the model lacks is a
  warning with a suggestion). The engine does not draw dashboards;
  [model2data studio](https://studio.jbanalytica.com) draws the file with the numbers of the
  model's generated data. `model2data guide dashboards` has the rest.
- model2data requires dbt-core >= 1.11 (tracking dbt's own supported-version policy). Everything
  here, `--unit-tests` included, works with a plain `pip install model2data`.

## Full CLI reference

```
model2data --file SCHEMA.dbml [OPTIONS]

--file, -f       PATH      Path to the DBML file (required)
--rows, -r       INT       Rows to generate per table (default: 100)
--rows-for       TABLE=N   Row count for one table, overriding --rows. Repeatable.
--seed           INT       Deterministic seed — same seed + schema always produces the same data
--table-seed     TABLE=N   Re-roll one table, leaving every other table byte-identical.
                           Repeatable; requires --seed
--as-of          DATE      YYYY-MM-DD to anchor generated dates and timestamps on (default: today)
--business-hours           Weight generated timestamps toward weekdays and working hours.
                           Overridable per date/timestamp column with a `business_hours` note hint
--growth         FLOAT     Relative change in activity across the window: -1.0 or above (default: 0, flat).
                           Overridable per date/timestamp column with a `growth` note hint
--seasonality    FLOAT     Strength of an annual cycle in timestamps, 0-1, peaking in Q4 (default: 0).
                           Overridable per date/timestamp column with a `seasonality` note hint
--skew           FLOAT     0 (default, every parent equally likely) to 1 (a few parents hold
                           most of the children). Overridable per FK column with a `skew` note hint
--locale         TEXT      Faker locale for generated people and addresses (default: en_US); a lone `country` column (no city/street/state/postcode beside it) reads as an international mix, not always this locale's country
--name, -n       TEXT      Override the generated dbt project's name (default: derived from filename)
--force                    Overwrite the destination directory if it already exists
--adapter, -a    TEXT      duckdb (default) or postgres
--unit-tests               Also generate dbt unit test fixtures
--hint-tests     TEXT      error, warn (default) or off: severity of the dbt tests written from
                           min/max, after (also a parent's column), null_rate, distinct, when and grain hints
--test-tolerance FLOAT     Absolute slack of the null_rate test (default 0.1)
--defects        TEXT      clean (default), messy or training: break the data on purpose, so dbt
                           tests are seen to fail; writes defects_report.json and
                           EXPECTED_FAILURES.md into the project. none: ignore every defect,
                           the model's own too
--metrics        PATH      The model's metrics file: adds metric_values.json (known values),
                           osi/NAME.yml (Apache Ossie 0.1.1) and data-tests/metrics/ (a dbt test
                           per metric). Changes no other file
--lightdash                Add Lightdash metrics and dimensions (config.meta) to the staging
                           models' YAML, from --metrics or the inferred metrics

model2data validate FILE... [--model MODEL]   a metrics file (*.metrics.yml) is checked against
                                              --model, or the model beside it with its stem
model2data metrics list   -f MODEL [--metrics FILE]                      every metric, inferred too
model2data metrics export -f MODEL [--metrics FILE] --to ossie|lightdash [-o FILE]  without generating
model2data dashboard check FILE... [--model MODEL] [--metrics FILE]   *.dashboard.yml against the
                                              dashboard spec and the model's metrics and columns
```

For anything not covered here, see [README.md](README.md) and [docs/](docs/README.md) — this file exists to make a schema
→ demo turnaround fast, not to duplicate the full documentation.
