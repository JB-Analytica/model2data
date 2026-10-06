# Generating a project

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

## The determinism promise

The same model, seed and `--as-of` — with the same options (`--rows`, `--rows-for`,
`--table-seed`, `--locale`, `--defects`, `--days`, …) — produce the same files, byte for byte:

- on any machine and operating system, and on every supported Python version;
- with any Faker or pandas version model2data accepts;
- with the same model2data version, whenever you run it.

A new release may change what a seed produces, to fix a bug or make the data more realistic.
When it does, the changelog says which models are affected and why. Pin the version
(`model2data==1.11.0`) when fixtures must stay the same across upgrades.

`tests/test_determinism.py` holds the file hashes of a spread of models, presets, multi-day runs
and locales, and runs on every pull request — against the newest Faker and pandas too — so a
change to a single output byte fails the build unless it is deliberate and in the changelog.

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

Next: [run the generated dbt project](dbt-project.md), or shape the data with
[days after the first](days.md), [when things happen](time-shapes.md),
[how the data is spread](distributions.md) and [defects on purpose](defects.md).
