# The model file

A model is one YAML document, `<name>.model2data.yml` — or the same document as JSON. Its
format is [spec 0.6.0](../model2data/spec/README.md), with a JSON Schema
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
[reference example](../model2data/spec/examples/coffee_webshop.model2data.yml) is a complete model,
and every schema in [`examples/`](../examples/) comes as one.

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
products.id}` gives every line its product's price), and nothing is said.

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

The generated dbt project tests every rollup with `model2data_rollup` (see
[The generated dbt project](dbt-project.md)).

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
