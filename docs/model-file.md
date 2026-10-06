# The model file

A model is one YAML document, `<name>.model2data.yml` — or the same document as JSON. Its
format is [spec 0.4.0](../model2data/spec/README.md), with a JSON Schema
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
