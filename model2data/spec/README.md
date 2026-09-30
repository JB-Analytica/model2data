# The model2data model — spec 0.2.0

A model2data model is one document: the tables of a data model, their columns and keys, the
relationships between them, the enums a column can be typed as, and how every column's values
are generated. It is written as YAML, in a file named `<name>.model2data.yml`, or as the same
document in JSON. [`model.schema.json`](model.schema.json) is its JSON Schema.

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

Everything in the document is data of its own type: a hint is a YAML mapping, a weight a number,
a description prose. Nothing is encoded inside a string.

The [model2data](https://github.com/JB-Analytica/model2data) engine reads this document and
nothing else. DBML, dbt YAML and other formats are read by converting them into it.

## What is normative

This README and `model.schema.json`, including every `description` in the schema. A document
**conforms** when, read under the YAML profile below, it validates against the schema and meets
every check under [Checks beyond the schema](#checks-beyond-the-schema). Examples are
informative.

A reader reports what it finds as issues, each with the path of the value and a **severity**:
an **error** is a way the document does not conform, and a **warning** is something the reader
points out in a document that still conforms (see [Warnings](#warnings)). Only errors make a
document non-conforming; a reader reads a document with warnings as it reads any other. A
validating tool shows both, and fails only on errors.

## Versioning

The spec is versioned on its own, apart from the engine and the studio, with
[semantic versioning](https://semver.org). The version is in the schema's `$id`:

```
https://www.jbanalytica.com/model2data/spec/0.2.0/model.schema.json
```

A patch release changes wording only. A minor release adds something optional. A major release
makes a valid document invalid or changes what one means; until 1.0.0 a minor release may too,
and says so. A document names the version it is written against in `model2data:`, and a reader
refuses a major (before 1.0.0: minor) version it does not implement rather than guess.

0.2.0 replaces 0.1.0, which carried hints as JSON inside DBML notes. See
[From 0.1](#from-01).

## The YAML profile

A reader of a `.model2data.yml` file applies these rules. Each exists because the YAML default
would silently change what the document says.

1. **The YAML 1.2 core schema.** `yes`, `no`, `on`, `off`, `y` and `n` are strings, not booleans
   (only `true` and `false` are), and `2026-01-01` is a string, not a date. Under YAML 1.1 an
   enum member `no` became `false` and a country code `NO` might too.
2. **No duplicate keys.** Two columns of one name are an error, not the last one winning.
3. **No anchors, aliases, merge keys (`<<`) or tags.** A model is read by people and edited by
   tools; a value defined in one place and used in another is neither.
4. **One document per file,** UTF-8.

A JSON document needs none of these: it is read as JSON.

Quoting follows YAML. The ones a model meets: a `color` (`"#ea580c"`, since `#` starts a
comment), and a description containing `: ` (`"Catalogue: one row per product"`).

## The document

The schema defines every member; this section says what the members mean together.

### Names

A **name** (a table, a column, an enum, a group) is 1–200 characters and contains no `.`, no `/`
or `\`, and no control character. Anything else is allowed, spaces and non-ASCII letters
included. A **table key** is a table's name, or `schema.name` for a table outside the default
schema; enums are keyed the same way. A **column path** is `<table key>.<column>`: the last `.`
separates the column.

Names are case-sensitive. Where a name reaches a filesystem or a dbt identifier (a seed, a
model), a consumer normalises it to `[a-z0-9_]`; two table keys that normalise to the same
identifier are an error for that consumer, not for the document.

### Tables and columns

`tables` and each table's `columns` are mappings keyed by name. **Order is significant**: the
columns of a table are generated and exported in document order, and consumers keep tables in
document order wherever they list them. Every mainstream YAML and JSON parser preserves it.

A column is a mapping with at least a `type`, or just its type as a string: `country: country`
means `country: {type: country}`.

A column's **type** is an SQL type, the name of an enum in the model, or the name of a generator.
The generators, and how a name or a type picks one, are the engine's (see its README); this spec
fixes only that an enum's name as a type means that enum, and that a type is matched
case-insensitively.

### Keys

- `pk: true` on a column: it is the primary key, and is not null and unique. When more than one
  column of a table says `pk: true`, they are **one composite primary key**, exactly as if listed
  under `keys` — not several keys.
- `unique: true` on a column: no two rows share its value.
- `keys` on a table: keys over several columns, `{pk: [a, b]}` or `{unique: [a, b]}`. Every
  member of a `pk` key is not null. A table has at most one primary key, whichever way it is
  written.

### References

A foreign key of one column is written on it: `references: customers.id`, or
`references: {to: users.id, one_to_one: true}`. The column is the **child**; the referenced
column, the **parent**, should be its table's primary key, a member of a one-column `pk` key, or
unique. Every child value is a value of the parent, or null when the child is nullable.

A parent column that is not a key is allowed, with a [warning](#warnings): real schemas have
them (a load log naming a schema version by its hash, which several versions may share). Such a
reference is generated like any foreign key -- every non-null child value is drawn from the
values the parent column actually holds, so each one exists in the parent -- but it does not
imply one parent row per value, as a reference onto a key does.

A foreign key over several columns is written on its table under `foreign_keys`, pairing
`columns` with `to_columns` in order. A many-to-many relationship, which no table holds the key
of, is written under the top-level `relationships`; it is drawn and documented but generates
nothing — model a join table to generate one.

### Generation hints

`generate` on a column holds its hints. The schema defines each one: its type, range and
meaning. A hint's `x-model2data-applies-to` names the **kinds** of column it may sit on:

| Kind | A column is this kind when |
|------|-----------------------------|
| `numeric` | its base type (its `type` lower-cased, cut at the first `(`, trimmed) is an integer type — contains `int` — or a decimal type — contains `decimal`, `numeric`, `float`, `double` or `real`, or is `money` or `number` |
| `boolean` | the base type contains `bool` |
| `temporal` | the base type contains `date` or `timestamp`; `time` alone is not temporal |
| `enum` | its `type` is the key of an enum in the model |
| `foreign-key` | it has `references`, or is a child column of a `foreign_keys` entry |
| `nullable` | it is not `pk`, not in a `pk` key, and not `not_null` |
| `non-key` | it is none of: a foreign key, `pk`, in a key, `unique`, an enum |

A column typed with an enum is of kind `enum` and never `numeric`, `boolean` or `temporal`,
whatever the enum's name contains (`maintenance_type` contains `int`).

The run's defaults for four hints are under `run.shape`; a column's own `generate` wins.

### Modelling

`role` and `grain` on a table and `measure` on a column steer what a consumer derives from the
model: a star schema, a Data Vault, a semantic layer, tests. The generator does not read them;
they sit beside `generate`, not in it, because they are about the model rather than its data.

- `grain` lists the columns that together identify one row: what one row of a fact *is*. A
  consumer may test it as a unique combination and use it as the primary entity of a semantic
  model.
- `measure` is `true` (a measure, aggregated by `sum`), `false` (not a measure), or how it
  aggregates: `sum`, `average`, `min`, `max`, `median`, `count`, `count_distinct`. The last two
  apply to any column, the rest to numeric ones.

```yaml
orders:
  role: fact
  grain: [order_id, line_number]
  columns:
    total: {type: numeric, measure: sum}
    unit_price: {type: numeric, measure: average}
    customer_id: {type: bigint, references: customers.id, measure: count_distinct}
```

### Days after the first

A run generates one day's state of the model: the rows as they stand on `as_of`. A table with
`incremental` also moves forward, one day at a time, when a reader asks for the next day
(`model2data generate --next`):

```yaml
orders:
  incremental:
    new_per_day: 40          # rows inserted each day, their dates falling on it
    update_rate: 0.05        # the share of existing rows that change each day
    changes: [status]        # which columns an update may change
    updated_at: updated_at   # set to the day a row was inserted or last changed
  columns:
    status:
      type: order_status
      generate:
        transitions: {pending: [paid, cancelled], paid: [shipped], shipped: [delivered]}
```

- An update changes the columns in `changes`, or, without it, the columns that have
  `transitions`. A column with `transitions` moves from its current member to one of the members
  listed for it, and stays when none are; any other changed column is drawn again by its type and
  hints.
- Keys never change, and a foreign key keeps pointing at a row that exists.
- Day *n* is fixed by the seed, `as_of` and *n*: generating days 1 to *n* again gives the same
  bytes, and day *n* never depends on anything but the days before it.
- A table without `incremental` does not change after the first day.

How the days are delivered (a file per day, a change log with an operation per row, the state as
of the last day) is the reader's choice; the rows each day holds are not.

### Run settings

`run` saves generation settings with the model: the row counts, the seed, the `as_of` day, the
locale, the shape defaults. A seed and a day name one dataset — the same `run`, model and engine
version produce the same bytes, on any machine, on any later day. A reader's own options (the
CLI's flags, the studio's settings) override the file.

### Extensions

A key beginning `x-` is an extension, at the top level, on a table, on a column, or in
`generate`. It carries no meaning in this spec; a consumer ignores the ones it does not know
and keeps them when it rewrites the document. Every other key not defined by the schema makes
the document invalid, so a typo like `nul_rate` is an error rather than a hint that silently
does nothing.

## Checks beyond the schema

A JSON Schema sees one value at a time. A conforming document also meets these, and a reader
reports each failure with the path of the value (`tables.orders.columns.status.generate.weights`):

1. Every `references`, `foreign_keys`, `relationships`, `groups.*.tables`, `run.rows_per_table`
   and `run.table_seeds` entry names a table (and column) of the model.
2. `columns` and `to_columns` of a foreign key have the same length.
3. A `keys` entry names columns of its own table.
4. Every hint sits on a column of a kind it applies to.
5. Every key of `weights` is a member of the column's enum.
6. `after` names another temporal column of the same table, and the `after` hints of a table do
   not form a cycle.
7. On an integer column, `min` and `max` are whole numbers, and `min` does not exceed `max` once
   a bound left out takes its default (0 and 100).
8. `null_rate` is only on a nullable column (it would otherwise have no rows to null).
9. `run.table_seeds` only with `run.seed`.
10. `grain`, `incremental.changes` and `incremental.updated_at` name columns of their own table,
    and `updated_at` is a temporal column.
11. Every key and every target of `transitions` is a member of the column's enum.
12. A `measure` aggregating by `sum`, `average`, `min`, `max` or `median` is on a numeric column.

### Warnings

A reader reports these as warnings, with the path of the value; a document that has them
conforms.

1. A referenced parent column is not a primary key, a one-column key, or unique; or the
   `to_columns` of a foreign key are not together the parent's primary key, one of its `keys`, or
   a unique column. The reference is generated as [References](#references) describes.

## From 0.1

A 0.1 model was DBML, with hints as a JSON object inside a column or table note. It converts to
0.2.0 without loss, and the engine and the studio do it when they read one:

| 0.1 (DBML) | 0.2.0 |
|------------|-------|
| `phone text [note: '{"null_rate": 0.4}']` | `phone: {type: text, generate: {null_rate: 0.4}}` |
| `total numeric [note: '{"distribution": "lognormal", "median": 120, "spread": 0.7}']` | `generate: {distribution: {kind: lognormal, median: 120, spread: 0.7}}` |
| `amount numeric [note: '{"measure": true}']` | `measure: true` |
| `Note: '{"role": "fact"}'` on a table | `role: fact` |
| `name word [note: 'The blend printed on the bag']` | `description: The blend printed on the bag` |
| `Table orders [headercolor: #ea580c]` | `color: "#ea580c"` |
| `Ref: orders.customer_id > customers.id` | `references: customers.id` on `orders.customer_id` |
| `Ref: profiles.user_id - users.id` | `references: {to: users.id, one_to_one: true}` |
| `Ref: posts.id <> tags.id` | `relationships: [{many_to_many: [posts.id, tags.id]}]` |
| `indexes { (a, b) [pk] }` | `keys: [{pk: [a, b]}]` |
| `status text [default: 'active']` | `default: active` |
| `created_at timestamp [default: \`now()\`]` | `x-default-expression: now()` |
| `TableGroup sales { ... }` | `groups: {sales: {tables: [...]}}` |

A note is read as hints when the whole note parses as a JSON object; any other note is a
description. A DBML table alias (`as C`) resolves to the table it names. A DBML name containing a
`.` has no 0.2.0 equivalent, and converting it is an error naming the table.
