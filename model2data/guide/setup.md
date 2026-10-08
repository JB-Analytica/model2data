# Set up model2data

No model in this directory yet. model2data turns one model file into seed CSVs and a
runnable dbt project. You write the model; it writes everything else.

## Install

1. `python -m pip install model2data` in the project's virtualenv. It brings `dbt-core`
   and `dbt-duckdb`, so `model2data` and `dbt` are both on PATH afterwards.
   In a uv project: `uv add --dev model2data`, then prefix every command below with `uv run`.
2. Check: `model2data --help` lists `generate`, `validate`, `convert`, `guide`.

## Write the model

Create `<name>.model2data.yml` in this directory. Start from this and replace the tables:

```yaml
model2data: 0.5.0
name: shop

enums:
  order_status: [pending, paid, shipped, cancelled]

tables:
  customers:
    columns:
      id: {type: bigint, pk: true}
      email: {type: email, unique: true, not_null: true}
      country: country
  orders:
    columns:
      id: {type: bigint, pk: true}
      customer_id: {type: bigint, not_null: true, references: customers.id}
      status: order_status
      placed_at: {type: timestamp, not_null: true}
      total_amount: {type: numeric, generate: {min: 5, max: 500}}

run:
  rows: 100
  seed: 42
  as_of: 2026-01-01
```

- Name columns for what they hold (`customer_email`, not `field3`): names and types pick
  realistic values. A type may also be a generator name: `email`, `country`, `company`
  (the list: `model2data guide tune`).
- Mark every key: `pk`, `unique`, `not_null`, `references`. Each one becomes a dbt test.
- Put `not_null` on every column that must always have a value: a column without it gets
  nulls in up to a fifth of its rows, enums and generator types included.
- A lookup table with one row per value (priorities, plan tiers): an enum column with
  `unique: true`, and that table's rows set to the number of members:
  `run: {rows_per_table: {priorities: 3}}`. It may go below the 10-row minimum.
- Keep `run.seed` and `run.as_of`: without them the output changes every run and every day.
- Already have DBML? `model2data convert <name>.dbml -o <name>.model2data.yml` and edit that.

## First run

```bash
model2data validate <name>.model2data.yml
model2data --file <name>.model2data.yml
cd dbt_<name> && dbt build
```

If `validate` warns that a date `can fall before customers.created_at`, add the `after` it
names (`model2data guide triage`). Done looks like: `✅ <name>.model2data.yml conforms to spec 0.5.0.`, a summary with
`Columns using generic fallback text: 0` and no ⚠️ lines, and `dbt build` ending in
`ERROR=0`. Anything else: `model2data guide triage`.

## Record the convention

Add to `CLAUDE.md` (or the README): "Edit `<name>.model2data.yml`, never `dbt_<name>/`;
regenerate with `model2data --file <name>.model2data.yml --force`. Run `model2data guide`
first." In CI, `model2data validate --glob '**/*.model2data.yml' --format github`.

Every option and model key: `model2data guide tune`.

next: `model2data validate <name>.model2data.yml`
