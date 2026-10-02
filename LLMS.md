# model2data — instructions for LLMs / coding agents

This file is written for an LLM or coding agent (Claude, GPT, etc.) that has file-write and
shell access and has been asked to turn a description of a data model into a demo-ready dbt
project. Read this before authoring a DBML file for `model2data`.

With model2data installed, `model2data guide` prints the instructions for the version you have:
how to set it up here, what its output means and what to do about it (`guide triage`), and every
option (`guide tune`). Run it first.

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
> `<name>.model2data.yml` ([spec 0.4.0](model2data/spec/README.md), with a JSON Schema at
> `model2data/spec/model.schema.json` and a complete example at
> `model2data/spec/examples/coffee_webshop.model2data.yml`), and DBML as supported input that it
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
completed_at timestamp [note: '{"when": {"status": ["done"]}}']  ' set only where status is done, null elsewhere
created_at timestamp [note: '{"business_hours": true}']    ' per-column override of --business-hours
created_at timestamp [note: '{"growth": 0.4}']             ' per-column override of --growth (date/timestamp only)
created_at timestamp [note: '{"seasonality": 0.6}']        ' per-column override of --seasonality (date/timestamp only)
total_amount numeric [note: '{"distribution": "normal", "mean": 100, "stddev": 20}']  ' shape a numeric column's spread
total_amount numeric [note: '{"distribution": "lognormal", "median": 80, "spread": 0.6}']  ' long right tail (spread: 0.3 mild, 1.0 heavy)
total_amount numeric [note: '{"distribution": "exponential", "mean": 30}']  ' most values small, a long tail of large ones
```
`distribution` (numeric columns only) is `"uniform"` (default, unchanged), `"normal"`, `"lognormal"`,
or `"exponential"`. `mean`/`stddev` go with `normal`, `median`/`spread` with `lognormal`, `mean` with
`exponential` again; any left unset default to the midpoint of the column's `min`/`max` (or the
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
  `pip install "model2data[postgres]"` and connection env vars — see README.md) are supported
  targets today.
- To show that a project's tests fire (or to teach dbt), generate with `--defects training`,
  or list defects per table in a `.model2data.yml` (`defects: [{type: nulls, column: x, count:
  3}]`, spec 0.3.0). `EXPECTED_FAILURES.md` in the project names exactly the tests `dbt build`
  should then fail; don't "fix" those failures in the generated project.
- For a source that keeps its own history (an SCD2 table to snapshot or join point-in-time),
  set `history: true` in the table's `incremental` and generate with `--days N`:
  `<table>_history` gets every version with `valid_from`, `valid_to` and `is_current`.
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
                           min/max, after, null_rate, distinct, when and grain hints
--test-tolerance FLOAT     Absolute slack of the null_rate test (default 0.1)
--defects        TEXT      clean (default), messy or training: break the data on purpose, so dbt
                           tests are seen to fail; writes defects_report.json and
                           EXPECTED_FAILURES.md into the project. none: ignore every defect,
                           the model's own too
```

For anything not covered here, see [README.md](README.md) — this file exists to make a schema
→ demo turnaround fast, not to duplicate the full documentation.
