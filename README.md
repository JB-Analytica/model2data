# model2data

[![PyPI](https://img.shields.io/pypi/v/model2data)](https://pypi.org/project/model2data/)
[![CI](https://github.com/JB-Analytica/model2data/actions/workflows/ci.yml/badge.svg)](https://github.com/JB-Analytica/model2data/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/JB-Analytica/model2data/branch/main/graph/badge.svg)](https://codecov.io/gh/JB-Analytica/model2data)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Built and maintained by [JB Analytica](https://www.jbanalytica.com/) — data platform architecture and analytics engineering.

**Turn a data model into a running analytics stack in one command.**

Give `model2data` a data model — a `.model2data.yml` file, or a [DBML](https://dbml.dbdiagram.io/docs/)
schema hand-written or exported from an existing database — and it generates realistic,
relationship-preserving synthetic data
*and* a complete, runnable dbt project around it: seeds, staging models, tests, and a
DuckDB or Postgres profile. No sample data to hunt down, no dbt boilerplate to hand-write, no
production data to risk exposing.

Driving model2data from a coding agent? Have it start here; the instructions ship with the
installed version and pick a topic from the current directory:

```bash
uvx model2data@latest guide
```

A working analytics stack — real (synthetic) data, tested dbt models, queryable in
DuckDB — from a schema file, in seconds:

![model2data generating a project and running it with dbt](https://raw.githubusercontent.com/JB-Analytica/model2data/main/assets/demo.gif)

```bash
pip install model2data
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42
cd dbt_ecommerce && dbt build
```

> **Prefer a browser?** [model2data studio](https://studio.jbanalytica.com) is the same
> engine as a web app: write DBML, watch the entity diagram redraw as you type, see what every
> column will generate before you generate it, then export CSVs or a runnable dbt project.
> Nothing to install, free to start.

---

## Why this exists

Analytics engineers hit the same wall constantly: you need realistic data to build or test a
pipeline, but production data is off-limits (privacy, access, scale), and hand-rolling mock CSVs
is tedious and doesn't scale past two tables. `model2data` closes that gap — from a schema
definition to a seeded, tested dbt project you can actually run, with no database or production
access required.

- **Privacy-safe.** Nothing but a schema definition goes in; nothing but synthetic data comes out.
- **Realistic, not random.** Column names are matched against ~35 common patterns — `email`,
  `first_name`, `city`, `phone`, `company`, ... — so a column called `email` gets real-looking
  emails, not `Lorem ipsum` text. Type a column with any Faker provider (`billing_country state`,
  `sku ean13`) to pick its generator outright when the name is wrong for the data.
- **Relationship-preserving.** Foreign keys resolve to real parent rows; tables are generated in
  dependency order.
- **Deterministic, byte for byte.** The same model, `--seed` and `--as-of` (and the same options)
  give the same files, byte for byte, on any machine and in every 1.x release — safe to commit
  fixtures, safe to diff across CI runs and upgrades. See [The determinism promise](#the-determinism-promise).
- **Re-rollable one table at a time.** `--table-seed orders=7` regenerates a single table and
  leaves every other table byte-identical, so you can keep the four tables that look right.
- **A real dbt project, not just CSVs.** Seeds, staging models that `ref()` them, schema tests,
  and a ready-to-use profile — the thing you'd otherwise spend an afternoon scaffolding by hand.
  A single `dbt build` loads, transforms, and tests the whole thing.

## Who is model2data for?

- **Analytics engineers** — generate realistic datasets and a working dbt project without
  waiting on production access.
- **Data engineers** — produce deterministic test data from an existing schema for pipeline and
  migration testing.
- **Software & data teams** — prototype integrations and analytics workflows without exposing
  production data.
- **Consultants & architects** — spin up realistic environments for demos, workshops, and
  architecture validation in minutes, not hours.

## How it works

```mermaid
flowchart LR
    subgraph input [" "]
        A["📄 model\n.model2data.yml or DBML"]
    end

    subgraph m2d ["model2data"]
        direction LR
        B["Parse\ntables, columns,\nrelationships"] --> C["Generate\nFaker + name-aware\ninference, FK-aware"]
        C --> D["Scaffold\nseeds · staging models\ntests · profile"]
    end

    subgraph output ["Generated dbt project"]
        direction TB
        E["seeds/*.csv"]
        F["models/staging/*.sql + *.yml"]
        G["profiles.yml\n(DuckDB or Postgres)"]
    end

    A --> B
    D --> E
    D --> F
    D --> G
    E & F & G --> H["dbt build"]
    H --> I[("Analytics-ready\ndataset")]

    classDef m2dStyle fill:#0A3866,stroke:#2196F0,color:#F6F8FB
    classDef outStyle fill:#182333,stroke:#A8C9EE,color:#F6F8FB
    classDef endStyle fill:#FA9306,stroke:#FA9306,color:#182333
    class B,C,D m2dStyle
    class E,F,G outStyle
    class H,I endStyle
```

1. **Read.** Reads the model — its tables, columns, keys, references and generation hints — from
   a `.model2data.yml` document, checking it against [the spec](model2data/spec/README.md), or
   from DBML, which it converts to the same model first.
2. **Generate.** Produces synthetic values per column — typed generation for known SQL types
   (int, date, timestamp, ...), then a Faker provider named as the type (`sku ean13`), then
   name-aware inference for everything else (`email`, `phone`, `city`, ...), foreign keys
   resolved against already-generated parent rows.
3. **Scaffold.** Writes a complete dbt project around that data: CSV seeds, staging models that
   `ref()` those seeds, `not_null`/`unique`/`relationships` tests, `accepted_values` tests for
   enum-typed columns, singular SQL tests for composite primary/unique keys, table and column
   `description:` fields from the model, and a profile for DuckDB (zero-config, file-based) or
   Postgres.

---

## The model file

A model is one YAML document, `<name>.model2data.yml` — or the same document as JSON. Its
format is [spec 0.3.0](model2data/spec/README.md), with a JSON Schema
([`model.schema.json`](model2data/spec/model.schema.json)) your editor can autocomplete and check
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
[reference example](model2data/spec/examples/coffee_webshop.model2data.yml) is a complete model,
and every schema in [`examples/`](examples/) comes as one.

Check a model without generating anything — every issue is printed with its path in the document:

```bash
model2data validate examples/ecommerce.model2data.yml
```

### DBML is supported input

`--file` also takes a `.dbml` file, which model2data converts to the same model before generating
(parsed with [pydbml](https://github.com/Vanderhoof/PyDBML)). Hints written as a JSON note on a
column (`[note: '{"min": 1, "max": 5}']`) become its `generate`; any other note is its
description. `convert` writes the model a DBML file converts to, for you to keep:

```bash
model2data convert examples/ecommerce.dbml -o ecommerce.model2data.yml
```

The spec's [From 0.1](model2data/spec/README.md#from-01) section lists how each DBML feature
converts. A few newer or rarer DBML features — `check` constraints, `Records`, `TablePartial`,
unquoted non-ASCII names — can't be read yet, and are refused with the line and what to change.

---

## Validate models in CI

Teams that keep `*.model2data.yml` models in git can fail a pull request that breaks one.
`model2data validate` takes several files and `--glob` patterns, prefixes each issue with its
file, exits 1 if any model has an error, and with `--format github` prints annotations that show
inline on the pull request's Files tab. `--require-files` fails when nothing matched.

**GitHub Actions**

```yaml
name: model2data
on:
  pull_request:
    paths: ["**/*.model2data.yml"]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: JB-Analytica/model2data@v1
```

Inputs: `files` (glob, default `**/*.model2data.yml`), `version` (default: the release the tag
points at, e.g. `1.10.4`; a specifier such as `>=1.10,<2` also works), `python-version` (3.12).

**pre-commit**

```yaml
repos:
  - repo: https://github.com/JB-Analytica/model2data
    rev: v1.10.4
    hooks:
      - id: model2data-validate
```

**GitLab CI**

```yaml
model2data:
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes: ["**/*.model2data.yml"]
  script:
    - pip install model2data==1.10.4
    - model2data validate --glob "**/*.model2data.yml"
```

## model2data studio — the same engine, in the browser

[**model2data studio**](https://studio.jbanalytica.com) puts everything on this page behind a
web UI. It is built by JB Analytica on top of this library, it's the fastest way to try
model2data, and it's the better fit while a schema is still being designed:

- **Type DBML, see the diagram.** Syntax highlighting, autocomplete and live error checking; the
  entity diagram redraws as you type. Click a column to trace what actually joins to it.
- **See what you'll get before you generate.** Every column shows an example of the value it will
  produce, and columns nothing recognises are marked — so placeholder data is visible rather than
  silent.
- **Generate and export.** Per-table row counts, then CSVs or a complete dbt project: the same
  seeds, staging models, tests and DuckDB profile this CLI produces, reproducing the exact rows
  you previewed.
- **Share the model.** A share link that also embeds as a chrome-free diagram in a Notion,
  Confluence or wiki page.

Free to start, nothing to install: [studio.jbanalytica.com](https://studio.jbanalytica.com)
([plans and pricing](https://www.jbanalytica.com/model2data/pricing/)).
The CLI stays the right tool for scripting, CI and fixtures you commit; the studio is where a
model gets designed and shown.

---

## Installation

```bash
pip install model2data
```

---

## Quick start

We bundle several example schemas in `examples/` — this walkthrough uses the e-commerce one
(`examples/ecommerce.model2data.yml`: customers, products, orders, order items, and reviews).

Generate a project with synthetic data:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42
```

This creates a `dbt_ecommerce/` folder with your data and dbt setup.

Real schemas are rarely uniform. `--rows-for` sizes individual tables, so a handful of customers
can sit behind a large orders table the way they would in the warehouse you're modelling:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 \
  --rows-for customers=50 --rows-for order_items=5000
```

`--seed` reproduces a run's numbers, but dates and timestamps are generated relative to the
current date, so the same seed drifts once the day turns over. `--as-of` pins the date they're
anchored on, and the whole dataset reproduces on any later day — which is what makes a generated
fixture safe to commit:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --as-of 2026-01-31
```

### The determinism promise

The same model, seed and `--as-of` — with the same options (`--rows`, `--rows-for`,
`--table-seed`, `--locale`, `--defects`, `--days`, …) — produce the same files, byte for byte:

- on any machine and operating system, and on every supported Python version;
- with any Faker or pandas version model2data accepts;
- in every 1.x release. A release may make generation faster or add options, but never changes
  what an existing model, seed and set of options produce. A fix that cannot keep that waits for
  a new major version, and the changelog says so.

`tests/test_determinism.py` holds the file hashes of a spread of models, presets, multi-day runs
and locales, and runs on every pull request — against the newest Faker and pandas too — so a
change to a single output byte fails the build rather than reaching a release.

If one table comes out wrong and the rest looks right, `--table-seed` re-rolls just that table.
Every other table's seed CSV stays byte-identical, and children of the re-rolled table still
reference rows that exist, so there's nothing to re-check but the table you asked to change:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --table-seed orders=7
```

`--locale` picks the country every generated person and address comes from (`en_US` by default);
it's a per-run setting, so a table can't end up holding one Belgian and one American address. A
`country` column that sits beside a `city`/`street`/`state`/`postcode` column always agrees with
that place; a `country` column with none of those beside it isn't describing anyone's address, so
it reads as an international mix instead, with the locale's own country the most common:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --locale nl_BE
```

### Generate the next days

A table with `incremental` in the model moves on day by day: each day adds `new_per_day` rows
(their dates and timestamps falling on that day) and updates `update_rate` of the rows it already
holds, and an enum column with `transitions` moves from a state to one of the states allowed to
follow it. See `examples/ecommerce_daily.model2data.yml` and "Days after the first" in the
[spec](model2data/spec/README.md).

```bash
model2data --file examples/ecommerce_daily.model2data.yml --seed 42 --as-of 2026-01-31 --days 7
model2data --file examples/ecommerce_daily.model2data.yml --seed 42 --as-of 2026-01-31 --next
```

`--days N` generates day 0, the run you would get without it, and N days after it (`--next` is
`--days 1`). The dbt seeds hold the state after the last day. `--days-format` picks what else is
written beside the project, outside `seeds/` so dbt does not load it: `batches` (the default),
`days/<table>/day_000.csv` whole and then one file per day with the rows inserted and updated that
day; `changelog`, `changelog/<table>.csv` with a `_day` and an `_op` (`insert` or `update`) column
on every row; or `final`, nothing more. A day depends only on the seed, `--as-of`, the day and the
model, so day *n* never changes when you generate more days or add an unrelated table. From Python:

```python
from model2data.generate.days import generate_days
from model2data.model import load

days = generate_days(load("examples/ecommerce_daily.model2data.yml"), 7, seed=42)
days[3].tables["orders"].inserted   # rows day 3 added
days[3].tables["orders"].updated    # rows day 3 changed, with their new values
days[3].tables["orders"].state      # the table after day 3
```

### Shape when things happen

By default, every date and timestamp is drawn uniformly across its window. `--business-hours`,
`--growth`, and `--seasonality` shape that instead — weekdays and working hours, a trend across
the window, and an annual cycle peaking in Q4:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 \
  --business-hours --growth 0.5 --seasonality 0.3
```

Within a row, created/updated/deleted-style columns are ordered automatically — `updated_at` never
lands before its own `created_at` — under any profile, uniform included. A column whose name
doesn't say what it depends on can say so explicitly with an `after` hint:

```yaml
shipped_at:
  type: timestamp
  generate: {after: ordered_at}
```

The flags above (or `run.shape` in the model) shape every date and timestamp column the same
way, run-wide. `business_hours`, `growth`, and `seasonality` column hints override that for one
column at a time — the whole point being a run can be uniform everywhere except the one column
that needs shaping, or shaped everywhere except the one column that shouldn't be:

```yaml
orders:
  columns:
    id: {type: int, pk: true}
    created_at:
      type: timestamp
      generate: {business_hours: true, growth: 0.4}
    refunded_at:
      type: timestamp
      generate: {growth: 0}
```

Here `created_at` gets business hours and growth even on an otherwise-uniform run, while
`refunded_at` stays flat even under `--growth 0.5` — each hint only replaces the fields it names,
so a partial hint like `{growth: 0}` leaves that column's `business_hours`/`seasonality` at
whatever the run-level flags set.

### Shape how the data is spread

By default every parent row is equally likely to be picked for a child row, and every column
gets the same generic null rate, value spread, and true/false split. `--skew` changes the first
part: `0.0` is that uniform default, `1.0` means a handful of parents hold most of the children —
"a fifth of the customers place most of the orders":

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --skew 0.8
```

Column hints shape the rest, per column:

```yaml
orders:
  columns:
    id: {type: int, pk: true}
    customer_id:
      type: int
      references: customers.id
      generate: {skew: 0.9}
    status:
      type: order_status
      generate:
        weights: {delivered: 20, cancelled: 2}
    is_paid:
      type: boolean
      generate: {true_rate: 0.9}
    discount_code:
      type: varchar
      generate: {null_rate: 0.8}
    shipping_city:
      type: varchar
      generate: {distinct: 12}
```

`skew` on a foreign key overrides `--skew` for just that column. `weights` biases an enum column
toward the values named (unnamed values still appear, at weight 1). `true_rate` is the fraction of
non-null rows a boolean column comes back `true`. `null_rate` replaces the column's default null
fraction outright. `distinct` draws the column's values from a fixed-size pool instead of a fresh
value per row — a `shipping_city` most warehouses only ever see a handful of.

### Shape a number's distribution

`min`/`max` alone only ever drew uniformly between them. A `distribution` hint on an integer or
decimal column picks a different shape instead:

```yaml
total_amount:
  type: numeric
  generate:
    min: 5
    distribution: {kind: lognormal, median: 80, spread: 0.6}
```

`normal` takes `mean` (the centre) and `stddev` (the spread); `lognormal` takes `median` (the
typical value) and `spread` (how heavy the tail is — 0.3 is mild, 1.0 is heavy); `exponential`
takes `mean` (the average). Any left unset default to the midpoint of the column's effective
`min`/`max` (or `stddev` = range / 6, `spread` = 0.5). `min`/`max` still clip the result — a
`normal` centred near an edge redraws a bounded number of times before clamping, so it never
loops forever and never crosses the bound. Leaving `distribution` out, or setting it to
`uniform`, is exactly today's behaviour.

Run dbt to load, transform, and test the data:

```bash
cd dbt_ecommerce
dbt build
```

Staging models `ref()` their seeds, so a single `dbt build` loads the seeds, builds the models,
and runs every generated test in one dependency-ordered pass — no separate `dbt seed`/`dbt run`
needed, even on a brand-new database. (The individual `dbt deps`, `dbt seed`, and `dbt run`
commands still work if you'd rather drive the steps yourself; the generated project declares no
packages, so `dbt deps` is a no-op.)

Your analytics-ready dataset is now in DuckDB!

To target Postgres instead, install the extra and pass `--adapter postgres`:

```bash
pip install "model2data[postgres]"
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --adapter postgres
```

Connection details are read from environment variables (`MODEL2DATA_PG_HOST`, `MODEL2DATA_PG_PORT`, `MODEL2DATA_PG_USER`, `MODEL2DATA_PG_PASSWORD`, `MODEL2DATA_PG_DATABASE`), defaulting to `localhost:5432` with a `postgres`/`postgres` user for local development.

After generation, the CLI prints a short summary — tables and rows generated, relationships found in the model, and any columns that fell back to generic placeholder text because neither their type nor name could be matched.

Pass `--unit-tests` to also generate deterministic dbt unit test fixtures (`models/staging/ut_stg_<table>.yml`) from the actually-generated seed rows:

```bash
model2data --file examples/ecommerce.model2data.yml --rows 200 --seed 42 --unit-tests
```

This targets dbt-core's native unit testing feature, which works out of the box with the base
install — see [dbt-core versions](#dbt-core-versions) below.

Your model's hints also write **data tests** for the dbt project, so the model that generates the
fixtures guards the real pipeline too. Every hint that states a constraint becomes a generic test
on the staging model, shipped as a self-contained macro in `macros/model2data_hint_tests.sql` (no
dbt package, so `dbt build` still works offline):

| In the model | dbt test | Tolerance |
|---|---|---|
| `generate: {min, max}` on a numeric column | `model2data_between` | none |
| `generate: {after: other}` | `model2data_not_before` (where both are not null) | none |
| `generate: {null_rate}` | `model2data_max_null_share`: nulls at most `null_rate` + tolerance | `--test-tolerance`, default 0.1 |
| `generate: {distinct: n}` | `model2data_max_distinct`: at most `n` distinct values | none |
| `grain` on a table | `model2data_unique_combination` | none |
| an enum-typed column | `accepted_values` (always written) | none |

`true_rate`, `weights`, `skew`, `distribution` and the temporal shape hints (`business_hours`,
`growth`, `seasonality`) describe a statistical shape rather than a constraint a row can break, so
they write no test. `--hint-tests {error,warn,off}` sets these tests' severity (default `warn`:
they describe intent and should not break a first `dbt build` on real data); the structural tests
(`not_null`, `unique`, `relationships`) are unaffected. A model with no such hints generates the
same YAML as before. To call the mapping from Python, `model2data.dbt.hint_tests.hint_tests_for(model)`
returns the tests as data.

### Break the data on purpose

A dbt test you have never seen fail is a test you hope works. `--defects` puts deliberate,
counted defects in the generated data, so `dbt build` fails exactly the tests it should — for
teaching dbt, or for proving a project's tests fire:

```bash
model2data --file examples/ecommerce.model2data.yml --seed 42 --defects training
cd dbt_ecommerce && dbt build    # fails unique, not_null, relationships, accepted_values once each
```

`training` breaks each kind of standard dbt test once; `messy` puts a small share of every defect
on every table; `clean` (the default) is the data as it always was, byte for byte; `none` also
ignores the defects a model's tables list, for the clean data of a training model. A table can
also list its own defects in the model (spec 0.3.0):

```yaml
orders:
  defects:
    - {type: duplicate_keys, count: 3}                               # fails unique
    - {type: orphan_foreign_keys, column: customer_id, share: 0.02}  # fails relationships
    - {type: nulls, column: order_date, count: 5}                    # fails not_null
```

The types are `duplicate_keys`, `orphan_foreign_keys`, `nulls`, `invalid_values` (fails
`accepted_values`), `messy_text` (whitespace and casing to clean in staging), and, with `--days`,
`late_arriving` (rows an incremental model filtering on `updated_at > max(updated_at)` skips) and
`late_updates` (versions a `strategy: timestamp` snapshot misses) and `overlapping_history` (see
below). Every run with defects writes
`defects_report.json` (each defect, the rows it broke, the tests it breaks) and
`EXPECTED_FAILURES.md` into the project, so a test that does not fire is visible. Defects are applied
after the clean data, from their own random stream: the same seed gives the same bytes, and a
defect on one table moves nothing in another. From Python, `model2data.defects.planned_defects`
says what a run will break, `model2data.output.finish_run` applies it (and builds the histories)
after `generate_days`, and `write_defects_report` / `write_expected_failures` write the two files.

### A source that keeps its history

`history: true` in a table's `incremental` (spec 0.3.0) also writes `<table>_history`: every
version of every row over the generated days, with `valid_from`, `valid_to` (null for the current
version) and `is_current` — an SCD type 2 source to build snapshots and point-in-time joins
against. The project tests it with two package-free tests, one current row per key and no
overlapping versions; the `overlapping_history` defect breaks the second.

```yaml
orders:
  incremental: {new_per_day: 20, update_rate: 0.1, updated_at: updated_at, history: true}
```

---

## Generated dbt project structure

The generated dbt project includes:

```
dbt_{project_name}/
├── seeds/
│   └── raw/
│       ├── __seed_config.yml  # seed descriptions + column-type overrides
│       ├── table1.csv
│       └── table2.csv
├── models/
│   └── staging/
│       ├── stg_table1.sql
│       ├── stg_table1.yml
│       ├── ut_stg_table1.yml  # only with --unit-tests
│       └── ...
├── data-tests/
│   └── unique_combination_stg_table1_col_a_col_b.sql  # only for composite pk/unique keys
├── macros/
│   ├── generate_schema_name.sql
│   └── model2data_hint_tests.sql  # only when the model's hints write tests
├── dbt_project.yml
├── profiles.yml  # DuckDB or Postgres config, depending on --adapter
└── {project_name}_profile.duckdb  # DuckDB adapter only
```

- **Seeds**: CSV files with generated synthetic data, plus `__seed_config.yml` — each seed's
  `description:` (the table's `description` in the model) and the column-type overrides that keep
  all-digit text columns (barcodes, zero-padded postcodes, ...) from being loaded as integers.
- **Staging Models**: Basic dbt models that `ref()` their seed. Using `ref()` rather than
  declaring the seeds as dbt `sources` is what gives each model a real DAG edge to the seed
  behind it, so one `dbt build` orders seeds before models on a fresh database.
- **Tests**: A YAML per staging model with column tests (`not_null`, `unique`, `relationships`,
  and `accepted_values` for enum-typed columns), plus the hint tests above. A column's `description` in the model becomes
  its `description:` field.
- **Composite key tests**: Composite primary/unique keys (`keys`, or several `pk: true` columns) get
  a singular SQL test under `data-tests/`, dbt's configured `test-paths`.
- **Profiles**: Pre-configured for DuckDB (file-based) or Postgres (via env vars), with schema handling.
- **Unit tests** (opt-in via `--unit-tests`): `models/staging/ut_stg_<table>.yml` fixtures built
  from real generated rows, co-located with each staging model so dbt (which only parses unit
  tests from `model-paths`) picks them up.

---

## Using model2data with an LLM

If you want to go from a plain-English description of a data model straight to a running,
demo-ready dbt project, [LLMS.md](LLMS.md) is written for an LLM/agent to read: it covers the
DBML feature set model2data understands (enums, notes, defaults, composite keys, both
relationship syntaxes, self-references), which it converts to a spec 0.2.0 model and the exact command sequence to run. Point an
LLM-backed coding assistant at it and describe your data model — it can author the DBML and run
model2data for you.

---

## dbt-core versions

model2data requires **dbt-core >= 1.11**, tracking [dbt's own version support
policy](https://docs.getdbt.com/docs/dbt-versions): dbt Labs supports each minor release for one
year, and 1.11 is the oldest that still is. Generated projects build cleanly — no deprecation
warnings — on every supported dbt-core version, and CI proves it on each push by running a real
`dbt build` against both the stated floor and the newest release.

If you're pinned to an older dbt-core, use model2data 0.5.x, which supported down to 1.8.5.

---

## Design decisions / non-goals

- **DuckDB Default**: Chosen for its zero-config, file-based nature, making it easy to get started without database setup. Postgres is supported via `--adapter postgres`; other adapters can be configured manually.
- **dbt Integration**: Leverages dbt's transformation capabilities for a familiar workflow in analytics engineering.
- **Synthetic Data**: Uses deterministic generation for reproducibility; not intended for production use or as a replacement for real data.
- **Non-goals**: This is not a data migration tool, ETL pipeline, or real-time data generator. It focuses on static, synthetic datasets for testing and prototyping.

---

## Limitations

- Synthetic data generation is heuristic-based (typed generation, name-aware inference, enum/default awareness) and may not perfectly mimic real-world distributions or edge cases.
- DuckDB and Postgres are supported today; other databases require manual profile adjustments.
- No support for incremental models or advanced dbt features in generated projects.
- Composite foreign keys (across a bridge/join table) are generated as independent single-column FKs — each column's values are individually valid, but the *combination* isn't guaranteed to match a real parent composite key unless that key is separately enforced as a key of the child (`keys`).
- A model that doesn't conform to the spec — or DBML that can't be read, or converts to such a model — is refused with every issue and where it is, rather than generated from in part. A reference onto a column that is not a key is allowed with a warning: its values are drawn from the ones the parent column holds.

---

## Project status

As of `1.0.0`, model2data is stable and feature-complete for its intended use case: turning a
data model into realistic synthetic data and a runnable dbt project, reliably. It is actively
maintained — CI runs a real `dbt build` against both the oldest supported dbt-core and the newest
release on every push, so compatibility is proven rather than assumed. New capabilities are not
the focus; correctness, dbt-core compatibility and community contributions are.

Ideas that came up during development but were deliberately left out of scope, in case anyone
wants to pick them up as a contribution:

- Additional database adapters (e.g. Snowflake, BigQuery).
- A rule-based semantic layer scaffold (`semantic_models.yml`/basic metrics) derived from the
  parsed schema shape.
- Example mart-layer models on top of staging (the generated `dbt_project.yml` carries a
  ready-to-uncomment `marts` schema/materialization config for this).

See [CONTRIBUTING.md](CONTRIBUTING.md) if you'd like to work on any of these.

---

## Contributing

We welcome contributions!

- Open issues for bugs or feature requests.
- Submit PRs to add new example models, custom data generators, or improvements.
- Ensure all new features include tests if possible.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines, and [DEVELOPMENT.md](DEVELOPMENT.md) for the local dev setup and release process.

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) to understand our community standards.

---

## License

MIT License. See LICENSE for details.

---

<p align="center">
  <a href="https://www.jbanalytica.com">
    <img src="https://raw.githubusercontent.com/JB-Analytica/model2data/main/assets/jba-icon-dark-bg.svg" alt="JB Analytica" height="40">
  </a>
  <br>
  Built and maintained by <a href="https://www.jbanalytica.com"><strong>JB Analytica</strong></a> —
  Data & Analytics Engineering · Data Platform Architecture · Modern BI.
  <br>
  Try <a href="https://studio.jbanalytica.com"><strong>model2data studio</strong></a> — model2data in the browser, nothing to install.
</p>
