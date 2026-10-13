# The model2data model — spec 0.5.0

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

What the model's numbers mean -- revenue, orders, average order value -- is written in a second,
optional document beside it, `<name>.metrics.yml`, defined by the [metrics
spec](metrics/README.md) and versioned on its own. The generator never reads it.

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
https://www.jbanalytica.com/model2data/spec/0.5.0/model.schema.json
```

A patch release changes wording only. A minor release adds something optional. A major release
makes a valid document invalid or changes what one means; until 1.0.0 a minor release may too,
and says so. A document names the version it is written against in `model2data:`, and a reader
refuses a major (before 1.0.0: minor) version it does not implement rather than guess.

0.3.0 adds [defects](#defects) and a table's [history](#history), and nothing else: a 0.2
document is a 0.3.0 document that uses neither, and a reader of 0.3.0 reads it as it always read.
Such a document keeps saying `model2data: 0.2.0` and pointing at the 0.2.0 schema; a document
that uses either says `model2data: 0.3.0`, and either in a document that says 0.2 is an error. A
writer writes the version the document was read with, and 0.3.0 once it uses either.

0.4.0 adds a column's [`when`](#generation-hints), and nothing else: a 0.2 or 0.3 document is a
0.4.0 document that does not use it, and a reader of 0.4.0 reads it as it always read. Such a
document keeps saying the version it says and pointing at that version's schema; a document that
uses `when` says `model2data: 0.4.0`, and `when` in a document that says 0.2 or 0.3 is an error. A
writer writes the version the document was read with, and 0.4.0 once it uses `when`.

0.5.0 adds two forms of a column's [`after`](#references): a parent's column,
`<table>.<column>`, and a list. A 0.2, 0.3 or 0.4 document is a 0.5.0 document that uses
neither, and reads as it always read. A document that uses either says `model2data: 0.5.0`,
and either in a document that says an earlier version is an error. A writer writes the
version the document was read with, and 0.5.0 once it uses either. 0.5.0 also says, of the
generated data of every document, as until 1.0.0 a minor release may, that an integer primary
key numbers the rows 1 to n, in order (see [Keys](#keys)).

0.2.0 replaced 0.1.0, which carried hints as JSON inside DBML notes. See
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

A primary key of one integer column numbers a table's rows: the first day's n rows hold 1 to
n, and a reader lists them in that order (spec 0.5.0). With a `min`/`max` or `distribution`,
or as a foreign key (a child keyed by its parent), the values are drawn and the rows listed
in ascending key order. Any other key leaves the values and the row order to the engine.

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

A date can be kept on or after a date of the parent row it points at: `after:
customers.created_at` on `orders.order_date` places no order before its customer signed up
(spec 0.5.0). `after` names a column of the same row, a parent's column as `<table>.<column>`
(a column path: the last `.` separates the column), or a list of them, all of which the value
is not before. A parent's column is read on the row the child's foreign key points at, so:

- the named table is reached through exactly one foreign key of one column, onto the parent's
  primary key, a one-column key, or a unique column. A table with two foreign keys to the parent
  (`sender_id` and `receiver_id` onto `users`) cannot say which row it means, and it is an
  error naming them; so is a parent reached only through a foreign key of several columns, or
  through a reference onto a column that is no key;
- the named column is a date or timestamp; a date compared with a timestamp compares by day;
- a row whose foreign key is null is not constrained;
- the named table is not the column's own: a column of the same row is named without a table,
  and a parent row in the same table (a self-reference) is not followed;
- two tables' `after`s must not name each other's columns (a cycle), and the parent must not be
  generated after the child (a cycle of foreign keys broken at the very foreign key followed).

How the engine keeps the child's own shape while doing so is the engine's (see
`model2data.generate.parents`).

A foreign key over several columns is written on its table under `foreign_keys`, pairing
`columns` with `to_columns` in order. A many-to-many relationship, which no table holds the key
of, is written under the top-level `relationships`; it is drawn and documented but generates
nothing — model a join table to generate one.

### Generation hints

`generate` on a column holds its hints. The schema defines each one: its type, range and
meaning. A hint's `x-model2data-applies-to` names the **kinds** of column it may sit on:

| Kind | A column is this kind when |
|------|-----------------------------|
| `numeric` | its base type (its `type` lower-cased, cut at the first `(`, trimmed) is an integer type — contains `int`, or is a PostgreSQL serial type (`serial`, `serial2`, `serial4`, `serial8`, `smallserial`, `bigserial`) — or a decimal type — contains `decimal`, `numeric`, `float`, `double` or `real`, or is `money` or `number` |
| `boolean` | the base type contains `bool` |
| `temporal` | the base type contains `date` or `timestamp`; `time` alone is not temporal |
| `enum` | its `type` is the key of an enum in the model |
| `foreign-key` | it has `references`, or is a child column of a `foreign_keys` entry |
| `nullable` | it is not `pk`, not in a `pk` key, and not `not_null` |
| `non-key` | it is none of: a foreign key, `pk`, in a key, `unique`, an enum |

A column typed with an enum is of kind `enum` and never `numeric`, `boolean` or `temporal`,
whatever the enum's name contains (`maintenance_type` contains `int`).

The run's defaults for four hints are under `run.shape`; a column's own `generate` wins.

`when` (spec 0.4.0) makes a column depend on another column's value: the column holds a value on the rows
where the named columns hold one of the listed values, and is null on every other row.

```yaml
tasks:
  columns:
    status: {type: task_status, not_null: true}
    created_at: {type: timestamp, not_null: true}
    completed_at:
      type: timestamp
      generate: {after: created_at, when: {status: [done]}}
```

Several columns listed must all match. A row whose named column is null does not match. On the
matching rows the column is generated as it would be without `when`, and none of them is null
unless the column has a `null_rate`, which then counts only those rows. How the values a model
without `when` generates are kept is the engine's (see `model2data.generate.when`).

### Modelling

`role` and `grain` on a table and `measure` on a column steer what a consumer derives from the
model: a star schema, a Data Vault, a semantic layer, tests. The generator does not read them;
they sit beside `generate`, not in it, because they are about the model rather than its data.

- `grain` lists the columns that together identify one row: what one row of a fact *is*. A
  consumer may test it as a unique combination and use it as the primary entity of a semantic
  model.
- `measure` is `true` (a measure, aggregated by `sum`), `false` (not a measure), or how it
  aggregates: `sum`, `average`, `min`, `max`, `median`, `count`, `count_distinct`. The last two
  apply to any column, the rest to numeric ones. Each measure also implies a metric, and a
  [metrics file](metrics/README.md) adds named, filtered, derived ones.

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
- A new row of a column with `transitions` starts in an initial state: a member no transition
  leads into (a new order is `pending`, never already `delivered`), drawn by `weights` among
  them. When every member can be reached from another, every member is a start. Day 0 is the
  state rows have reached by `as_of`, so it holds every member.
- A day's new rows continue the existing ones: an integer key continues after the largest value
  held, any other unique value differs from every value held, and a foreign key points at a row
  that exists by then, one inserted the same day included (parents are inserted before
  children), and a new row's `after` of a parent's column holds on that parent. A foreign key that
  must be unique takes each parent once, so a table whose parents
  run out inserts fewer rows that day. Every date and timestamp column of a new row falls on the
  day (a timestamp at any time of it, weighted toward working hours when the run shape has
  `business_hours`), a nullable one staying null where an ordinary draw would be, and a column
  that follows another (`after`, or a created/updated/closed stage) is not before it.
- `update_rate` is applied to the rows that existed before the day's insertions: the day updates
  `update_rate` times their number, rounded half up. A row with nowhere to move is
  counted all the same, and gets its `updated_at`. A temporal column in `changes` is set to a
  time on the day.
- `updated_at`, when given, is set on a new row to the latest time of the row's own temporal
  columns, and on an updated row to a time on the day no earlier than any temporal column the
  update changed.
- A column with `when` holds a value on every row that matches, new rows included, and on no
  other. An update that brings a row to match sets the column on the day (a date column to the
  day, a timestamp to the row's `updated_at` when the table has one); one that takes a row out
  of matching makes it null; a row that matched and still does keeps its value.
- Keys never change (a key column, one that is `pk` or `unique` or in a `keys` entry, is not
  listed in `changes`), and a foreign key keeps pointing at a row that exists.
- Day *n* is fixed by the seed, `as_of` and *n*: generating days 1 to *n* again gives the same
  bytes, and day *n* never depends on anything but the days before it.
- A table without `incremental` does not change after the first day.

How the days are delivered (a file per day, a change log with an operation per row, the state as
of the last day) is the reader's choice; the rows each day holds are not.

### History

`history: true` in a table's `incremental` (spec 0.3.0) keeps every version of every row, as a
source that keeps its own history (a slowly changing dimension of type 2) does. A reader writes a
second table, `<table>_history`: the table's columns, then

- `valid_from`: when the version began: its `updated_at` when the table has one and it is set,
  else the start of the day the version was delivered (the first day for the rows it holds);
- `valid_to`: when the next version of the key began, null for the current one;
- `is_current`: true for the last version of each key, false for the others.

One row per version: the first day's, then one for each day that inserted or updated the row. A
version never begins before the one it follows (one that would begins a second after it), so each
key has exactly one current version and no two of its versions overlap. The table needs a primary
key, has none of the three columns itself, and the model has no table named `<table>_history`.
The history is the clean record of the days: [defects](#defects) of the table do not reach it,
and only `overlapping_history` breaks it.

### Run settings

`run` saves generation settings with the model: the row counts, the seed, the `as_of` day, the
locale, the shape defaults. A seed and a day name one dataset — the same `run`, model and engine
version produce the same bytes, on any machine, on any later day. A reader's own options (the
CLI's flags, the studio's settings) override the file.

### Defects

A run can break its data on purpose, so a dbt project's tests are seen to fire: a learner runs
`dbt build`, sees exactly the tests that should fail, and fixes them. A table lists its defects
under `defects`, one per entry:

```yaml
orders:
  columns:
    id: {type: bigint, pk: true}
    customer_id: {type: bigint, not_null: true, references: customers.id}
    order_date: {type: timestamp, not_null: true}
  defects:
    - {type: duplicate_keys, count: 3}
    - {type: orphan_foreign_keys, column: customer_id, share: 0.02}
    - {type: nulls, column: order_date, count: 5}
```

| `type` | Breaks | `column` | The test it fails |
|--------|--------|----------|-------------------|
| `duplicate_keys` | rows that repeat another row's key | the primary key (all of a composite one), or a unique column | `unique` (a composite key's uniqueness test) |
| `orphan_foreign_keys` | rows whose foreign key names no parent row | required: a column with `references`, or in `foreign_keys`; not a boolean | `relationships` |
| `nulls` | nulls in a column that is never null | required: `not_null`, or the table's one-column primary key | `not_null` |
| `invalid_values` | values outside the members | required: a column typed with an enum, in no key | `accepted_values` |
| `late_arriving` | rows inserted on a later day whose event time is before the previous load's cutoff | the event-time column: `incremental.updated_at`, else the table's first date or timestamp column | none: an incremental model skips them |
| `late_updates` | updates on a later day whose `incremental.updated_at` is the version's they replace | none: `incremental.updated_at` | none: a timestamp snapshot misses them |
| `messy_text` | leading or trailing whitespace and inconsistent case | required: a text column (not a time) that is no key, foreign key or enum | none: cleaned in staging |
| `overlapping_history` | versions in `<table>_history` still valid after the next version of their key began | none | the history's no-overlap test |

- Each entry has a `count` (rows) or a `share` of the table's rows (rounded half up, and at
  least one row when the share is above 0), never both. `count: 0` switches a defect off.
- A table lists a `type` once per `column` (a left-out column standing for the type's default):
  nulls in two columns are two entries.
- The late kinds and `overlapping_history` need a run of several days (`incremental` and
  `model2data generate --days`). A run of one day applies none of them, and says so.
- `run.defects` names a **preset**, which a reader expands into defects on every table it suits:
  `clean` (none, the default), `messy` (a small share of every defect a table can take) or
  `training` (one defect for each kind of standard test, so each fails once where the model has
  one to break; the late kinds and overlapping history too on a run of several days). A table's
  own entries override it: an entry of the same `type` and `column` replaces the preset's, any
  other is added, and `defects: []` keeps the table clean. `none` is no defect at all, the
  tables' own ignored too: the clean data of a model that lists defects. A reader's own option
  (`--defects`) overrides `run.defects`.

What a defect does to which rows, and how a run reports it, is the engine's (see
`model2data.defects`); this spec fixes what a defect is asked to break. A run with defects
reports every one it applied: the rows it broke, and the tests of the generated project that now
fail. Defects are applied after the clean data is generated, from a random stream of their own,
so the same seed, model and defects give the same bytes, and the clean data is what it is
without them.

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
6. Each entry of an `after` names another temporal column of the same table, or a temporal
   column of a parent table reached as [References](#references) says; the `after` hints of a
   table do not form a cycle, and two tables' `after`s do not name each other's columns.
7. On an integer column, `min` and `max` are whole numbers, and `min` does not exceed `max` once
   a bound left out takes its default (0 and 100).
8. `null_rate` is only on a nullable column (it would otherwise have no rows to null).
9. `run.table_seeds` only with `run.seed`.
10. `grain`, `incremental.changes` and `incremental.updated_at` name columns of their own table,
    `updated_at` is a temporal column, and `changes` names no column of a key.
11. Every key and every target of `transitions` is a member of the column's enum.
12. A `measure` aggregating by `sum`, `average`, `min`, `max` or `median` is on a numeric column.
13. `defects`, `run.defects` and `incremental.history` only in a document of spec 0.3.0 or
    later, and `when` only in a document of spec 0.4.0 or later.
14. Every defect has exactly one of `count` and `share`, names a column of its table, and gives a
    `type` and `column` no other entry of the table gives. `duplicate_keys` names a primary key
    or a unique column, or its table has a primary key; when `run` gives the table's rows, its
    `count` is fewer than them. `orphan_foreign_keys`, `nulls`, `invalid_values` and
    `messy_text` name their column, and it is of the kind the [Defects](#defects) table says.
    `late_arriving` is on a table with `incremental.new_per_day` and a date or timestamp column
    (a named one is one); `late_updates` names no column and is on a table with
    `incremental.update_rate` and `incremental.updated_at`; `overlapping_history` names no
    column and is on a table with `incremental.history`.
15. A table with `incremental.history` has a primary key, no column named `valid_from`,
    `valid_to` or `is_current`, and no table of the model is named `<table>_history`, or
    normalises to the same dbt name.
16. `when` is on a nullable column with no `default` that is not in a key, `unique`, a foreign
    key or its table's `incremental.updated_at`. Each column it names is another column of the
    same table that has no `when`, is not a foreign key and is not temporal; each value listed
    for it is a member of its enum, `true` or `false` for a boolean, a number for a numeric
    column (a whole one for an integer column), or text for any other.

### Warnings

A reader reports these as warnings, with the path of the value; a document that has them
conforms.

1. A referenced parent column is not a primary key, a one-column key, or unique; or the
   `to_columns` of a foreign key are not together the parent's primary key, one of its `keys`, or
   a unique column. The reference is generated as [References](#references) describes.
2. A `grain` contains no key of its table (its primary key, a `keys` entry, or a `unique`
   column). The generator does not read `grain`, so generated rows may repeat it; declaring it
   as a key too makes them unique.
3. A table reaches a parent through one one-column foreign key onto a key, the parent has a
   date or timestamp that reads as its creation (by the engine's stage words, as `created_at`,
   or else its first that names no later stage), the table's own first such date has no `after`
   naming that parent, and no other column's `after` names it: the child's date can fall
   before its parent's. The warning names the `after` to add. A tool may offer it as a fix: the
   engine's warning carries the path and value to set (and the spec version the document then
   needs).

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
