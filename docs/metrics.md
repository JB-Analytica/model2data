# Metrics with known values

What the numbers mean -- revenue, orders, average order value -- goes in a second, optional file
beside the model, `<stem>.metrics.yml`, defined by the [metrics spec 0.1.0](../model2data/spec/metrics/README.md)
([schema](../model2data/spec/metrics/metrics.schema.json)):

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/metrics/0.1.0/metrics.schema.json
model2data-metrics: 0.1.0
model: coffee_webshop

metrics:
  revenue:
    label: Revenue
    measure: orders.total_amount        # summed: the column's `measure` is true
    where:
      orders.status: [paid, shipped, delivered]
    time: orders.order_date
    format: currency
  orders:
    measure: orders.id
    agg: count_distinct
  average_order_value:
    ratio: {numerator: revenue, denominator: orders}
  coffee_units_sold:
    measure: order_items.quantity       # dated by orders.order_date, through order_id
    where:
      orders.status: {ne: cancelled}
      products.category: [beans, ground, capsules]
      any:
        - orders.channel: web
        - order_items.quantity: {gte: 3}
```

(the whole file, which conforms against the example model, is
[`model2data/spec/examples/coffee_webshop.metrics.yml`](../model2data/spec/examples/coffee_webshop.metrics.yml)).
A metric is a column aggregated (`measure`, with `agg` or the column's own `measure`), a row count
(`count`), a `ratio` of two metrics' totals, or arithmetic over metrics (`expression`). A filter
can reach any table the metric's table reaches through its foreign keys, many-to-one, along exactly
one path; nulls behave as in SQL. Every `measure` column of the model also yields a metric on its
own (`orders_total_amount`), and a fact a row count (`orders_count`); `infer: false` turns that
off. `format` changes no value: `percent` expects a fraction (0.6 shows as 60%), so a share is
`web_revenue / revenue`, never `* 100`. The generator never reads the file: the data is the same
with or without it.

```bash
model2data validate coffee_webshop.metrics.yml          # against coffee_webshop.model2data.yml beside it
model2data --file coffee_webshop.model2data.yml --metrics coffee_webshop.metrics.yml --rows 200 --seed 42 --as-of 2026-01-31
cd dbt_coffee_webshop && dbt build                      # one test per metric, each checking its known value
```

With `--metrics`, the run also writes, and changes nothing else:

- **`metric_values.json`** -- each metric's **known value** over the generated data, overall and
  per calendar month of its time column: with the command above, revenue is 28344.22, orders 200,
  average order value 141.7211. A run knows its data, so it knows its numbers; a semantic layer or
  dashboard built on the data can be checked against them. Counts, and sums, minimums and maximums of
  integer columns, are exact; everything else is rounded to 6 decimal places, half to even. Same seed, same bytes.
- **`data-tests/metrics/metric_<name>.sql`** -- a singular dbt test per metric that computes it
  over the project's staging models and fails unless it gets the known value (within
  `0.000001 + 0.000000001 × |value|`), so `dbt build` proves the metric logic on the warehouse.
- **`osi/<project>.yml`** -- the model and its metrics as an [Apache Ossie](https://github.com/apache/ossie)
  (Open Semantic Interchange) 0.1.1 semantic model: tables as datasets over the staging models
  (`staging.stg_<table>`), foreign keys as relationships, date columns as time dimensions, metrics
  as ANSI SQL expressions. What Ossie 0.1.1 has no field for (a metric's label, format and time,
  enum members) is carried in `custom_extensions` and listed at the top of the file.

`model2data metrics list -f MODEL [--metrics FILE]` shows every metric, and `model2data metrics
export -f MODEL [--metrics FILE] --to ossie [-o FILE]` writes the Ossie file without generating.
Without `--metrics`, both use the model's inferred metrics. A sibling metrics file is not picked up
by `generate` on its own: a file appearing beside a model must not change what an unchanged
command writes. From Python, `model2data.metrics` loads and checks a file (`load`, `validate`),
resolves it with the model into one semantic representation (`resolve`), and computes
(`known_values`) and exports (`to_ossie`) from it.

The e-commerce example has a metrics file too, [`examples/ecommerce.metrics.yml`](../examples/ecommerce.metrics.yml)
(revenue, orders, average order value, units sold); the [README](../README.md#before-and-after)
shows what a run with it writes.
