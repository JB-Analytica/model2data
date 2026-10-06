# model2data metrics — spec 0.1.0

A metrics file says what a model2data model's numbers mean: revenue, orders, average order value,
each written once, next to the model they are computed from. It is a second, optional document,
`<stem>.metrics.yml` beside `<stem>.model2data.yml`, versioned on its own.
[`metrics.schema.json`](metrics.schema.json) is its JSON Schema. The model it is for is defined by
the [model spec](../README.md).

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/metrics/0.1.0/metrics.schema.json
model2data-metrics: 0.1.0
model: coffee_webshop          # the model's `name`

metrics:
  revenue:
    label: Revenue
    description: Order value of orders that were not cancelled.
    measure: orders.total_amount        # aggregated by the column's own `measure`, or by `agg`
    where:
      orders.status: [paid, shipped, delivered]
    time: orders.order_date
    format: currency
  orders:
    measure: orders.id
    agg: count_distinct
  order_lines:
    count: order_items                  # the rows of a table
  average_order_value:
    ratio: {numerator: revenue, denominator: orders}
    format: currency
  web_revenue:
    measure: orders.total_amount
    where:
      orders.status: [paid, shipped, delivered]
      orders.channel: web
  web_share:
    expression: web_revenue / revenue    # a fraction: format shows 0.6 as 60%
    format: percent

dimensions:
  products.name: {label: Product}
  customers.email: false

infer: true
```

A complete example, which conforms against the example model beside it, is
[`../examples/coffee_webshop.metrics.yml`](../examples/coffee_webshop.metrics.yml).

The model and its generated data do not depend on the metrics file: a generator never reads it,
and a model generates the same rows with or without one. What a metrics file adds is meaning
(names, labels, formats, filters) and, for a consumer that has the data, numbers.

## What is normative

This README and `metrics.schema.json`, including every `description` in the schema. A document
**conforms** when, read under the model spec's [YAML profile](../README.md#the-yaml-profile), it
validates against the schema and meets every check under [Checks beyond the
schema](#checks-beyond-the-schema), and, read **against its model**, every check under [Checks
against the model](#checks-against-the-model). Examples are informative.

A reader reports issues as the model spec does: each with the document path of the value
(`metrics.revenue.where.orders.status`) and a severity, an **error** where the document does not
conform and a **warning** for something it points out in a document that does.

## Versioning

The metrics spec is versioned on its own, apart from the model spec, the engine and the studio,
with [semantic versioning](https://semver.org). The version is in the schema's `$id`:

```
https://www.jbanalytica.com/model2data/spec/metrics/0.1.0/metrics.schema.json
```

A document names the version it is written against in `model2data-metrics:`. A patch release
changes wording only; a minor release adds something optional, and, before 1.0.0, may also make a
valid document invalid or change what one means, and says so. A reader refuses a minor version it
does not implement (from 1.0.0, a major one) rather than guess.

## The YAML profile, and extensions

A metrics file is read under the model spec's YAML profile: the YAML 1.2 core schema (`no` is a
string, `2026-01-01` is a string), no duplicate keys, no anchors, aliases, merge keys or tags,
one UTF-8 document. JSON is read as JSON.

A key beginning `x-` is an extension, allowed at the top level, on a metric, in a `ratio`, in a
`where` (a key with no `.` in it), in a condition's mapping of operators and on a dimension. It
carries no meaning here; a consumer ignores the ones it does not know and keeps them when it
rewrites the document. Every other unknown key makes the document invalid.

## The document

### `model`

The `name` of the model the metrics are for. A model without a `name` goes by the stem of its
file (`coffee_webshop` for `coffee_webshop.model2data.yml`). Read against a model of another
name, the document is in error, not quietly applied to the wrong model.

### Metrics

`metrics` maps a **metric name** to a metric. A name is a lower-case letter, then lower-case
letters, digits and underscores (`^[a-z][a-z0-9_]*$`): it is used unquoted in SQL, in file names
and in other semantic layers. Order is significant: consumers list metrics in document order.

A metric's **kind** comes from exactly one of four keys. None, or more than one, is an error.

| Key | Kind | The metric is |
|-----|------|---------------|
| `measure: table.column` | simple | the column aggregated: by `agg`, or without it by the column's own `measure` in the model (`true` is `sum`) |
| `count: table` | row count | the number of rows of the table |
| `ratio: {numerator: a, denominator: b}` | ratio | metric `a`'s total divided by metric `b`'s |
| `expression: a - b` | derived | arithmetic over other metrics and numbers |

The other keys:

- `agg` (simple only): one of the model spec's seven aggregations, `sum`, `average`, `min`,
  `max`, `median`, `count`, `count_distinct`. `count` (the non-null values) and `count_distinct`
  apply to a column of any kind; the rest to a numeric one, by the model spec's [kind
  rules](../README.md#generation-hints) (an enum is never numeric). Without `agg`, the column's
  `measure` decides; a column with no `measure` (or `measure: false`) and no `agg` is an error.
- `label`: the metric's name for people. `description`: what it is, in prose. `ai_context`:
  prose for an AI tool, on when to use it and what it is also called.
- `format`: how a value is shown, `number`, `currency` or `percent`. It changes no value.
  `percent` expects a **fraction**: a value of 0.6 is shown as 60%, as Lightdash, Looker, Power
  BI and Cube format percentages. So a share is written `web_revenue / revenue`, never
  `* 100`. A consumer or exporter passes `percent` on as a percent format and does not multiply
  the value, and a known value of a `percent` metric is the fraction.
- `time` (simple and row count only): the date or timestamp column the metric is dated by. See
  [Time](#time).
- `where` (simple and row count only): a filter. See [Filters](#filters). A ratio or a derived
  metric is filtered, and dated, through the metrics it is computed from.

**A ratio** is a ratio of totals: the numerator's value over the whole of what is being looked at,
divided by the denominator's, never an average of row-level ratios. It is null when the
denominator is zero or null.

**A derived metric's** `expression` is the one place this spec encodes something in a string, so
its grammar is as small as one can be:

```
expression := term (("+" | "-") term)*
term       := factor (("*" | "/") factor)*
factor     := ("+" | "-") factor | NUMBER | NAME | "(" expression ")"
NAME       := [a-z][a-z0-9_]*          a metric
NUMBER     := [0-9]+ ("." [0-9]+)?
```

Whitespace between tokens is ignored; `*` and `/` bind tighter than `+` and `-`, and operators of
one precedence associate to the left. An expression uses at least one metric. It is evaluated over
its metrics' values in decimal arithmetic: any null makes the result null, and so does a division
by zero. A reader parses it; it never evaluates it as code.

A ratio or an expression may use any metric: one written in the file, or one [inferred from the
model](#inferred-metrics). Metrics that depend on each other in a cycle are an error.

### Filters

`where` maps a **column path** (`table.column`, or `schema.table.column`: the last `.` separates
the column) to a **condition**, and every entry must hold. Two keys combine filters:

- `any: [filter, ...]`: one of the filters holds;
- `all: [filter, ...]`: every one holds (needed inside `any`).

A column path always contains a `.`, so it never collides with `any`, `all` or an extension.

A condition is

- a value: the column equals it (`orders.channel: web`);
- a list of values: the column is one of them (`orders.status: [paid, shipped]`);
- a mapping of operators, every one of which must hold:

| Operator | Holds when the column |
|----------|------------------------|
| `eq: v` | equals `v` |
| `ne: v` | differs from `v` |
| `in: [v, ...]` | equals one of them |
| `not_in: [v, ...]` | equals none of them |
| `gt`, `gte`, `lt`, `lte: v` | is greater than, at least, less than, at most `v` |
| `between: [a, b]` | is at least `a` and at most `b` |
| `is_null: true` / `false` | is null / is not null |

**Nulls** behave as in SQL: a null fails every condition except `is_null: true`. So `ne: cancelled`
and `not_in: [cancelled]` leave out the rows with no status as well as the cancelled ones; write
`any: [{orders.status: {ne: cancelled}}, {orders.status: {is_null: true}}]` to keep them.

A **value** is checked against its column, by the model spec's kinds:

| The column is | A value is |
|---------------|------------|
| an enum | one of its members (an integer member may be written as a number) |
| boolean | `true` or `false` |
| numeric | a number; a whole one for an integer column |
| a date | an ISO 8601 date string, `"2026-01-01"` |
| a timestamp | an ISO 8601 date or timestamp string, `"2026-01-01 08:30:00"`, with no time zone |
| anything else | text (quote one that YAML would read as a number) |

The order operators (`gt`, `gte`, `lt`, `lte`, `between`) apply to numeric, date and timestamp
columns only. On an enum, a boolean or text they are an error: an enum's order is not its
members', and text order depends on the warehouse's collation, so a filter on it would not mean
one thing. List the values with `in` instead.

Not in 0.1.0: `not`, text matching (`like`, prefixes), dates relative to today.

### Reaching other tables

A metric aggregates the rows of one table: a simple metric's column's, a row count's table. A
filter column, and its `time`, are on that table or on one it **reaches**: by following foreign
keys from child to parent (`references` and `foreign_keys`, the many side to the one side),
one or more steps. Each step joins a row to at most one parent row, so the rows being aggregated
are never multiplied. A step from parent to child is never taken.

A column is reachable when **exactly one** such path leads to its table. When two do (`orders`
with both `billed_to` and `shipped_to` referencing `customers`), which one the filter means is
unclear, and it is an error naming both paths. A column no path leads to is an error too.

A row whose foreign key is null, or names no parent row, reaches no parent: its parent's columns
are null, and fail every condition but `is_null: true`.

### Time

A simple metric's or a row count's `time` names the date or timestamp column it is dated by: its
value per month is computed over the rows whose time falls in that month. The column is on the
metric's table or one it reaches, as a filter column is. Without `time`:

1. the metric's table's first date or timestamp column, in document order;
2. else, nearest first and in document order of the foreign keys, the first date or timestamp
   column of a table it reaches along exactly one path (`order_items` is dated by
   `orders.order_date`, through `order_id`).

A metric with neither is valid, and a reader warns: it has a total but no value per month.

### Dimensions

The **dimensions** of a model are the columns to group and filter metrics by. By default they are
every date and timestamp column (a time dimension), and every enum and boolean column that is not
a primary key, in a key, `unique`, a foreign key, or a `measure` (a categorical dimension).

`dimensions` changes that, by column path:

- `false`: never offered as a dimension (`customers.email: false`);
- `true`: offered, as a time dimension when the column is a date or timestamp, else categorical;
- a mapping: offered, with a `label` and a `description`.

### Inferred metrics

With `infer: true` (the default), a model implies metrics on its own, beside the file's:

- one **simple metric for each column with a `measure`**, aggregated by it, named
  `<table>_<column>`, with `_<aggregation>` appended when it is not `sum`
  (`orders_total_amount`, `products_price_average`);
- one **row count for each fact**, named `<table>_count`: a table whose `role` is `fact`, or which
  has no `role` and at least one `measure` column.

A name is the text lower-cased, with every run of other characters made one `_`, trimmed of `_`;
it starts with a letter (else `c_` is put in front) and has at least two characters (else `_x` is
appended), and a name already taken gets `_2`, `_3`, in document order of tables and columns. An
inferred metric's label is the table and the column in words (`Orders total amount`), with the
aggregation in brackets when it is not `sum`.

A metric of the same name in the file **replaces** the inferred one. Consumers list the file's
metrics first, in document order, then the inferred ones it did not replace, in model order.
`infer: false` turns inference off: the metrics are only the ones written here.

A model with no metrics file has the inferred metrics, wherever a consumer exposes metrics.

These are the names model2data studio's semantic-layer export gave its metrics, so a metric keeps
its name when the studio moves onto this spec.

## Checks beyond the schema

A conforming document also meets these, read on its own:

1. `model2data-metrics` is a version this reader implements (0.1.x).
2. Each metric has exactly one of `measure`, `count`, `ratio` and `expression`; `agg` only with
   `measure`; `time` and `where` only with `measure` or `count`.
3. Every `expression` parses by the grammar above and uses at least one metric.
4. No metric depends on itself, directly or through other metrics.

## Checks against the model

Read against its model, a conforming document also meets these:

1. `model` is the model's name.
2. Every column path and table key names a column or table of the model (`measure`, `count`,
   `time`, every column of a `where`, every key of `dimensions`).
3. A simple metric's aggregation fits its column (see `agg`); without `agg`, the column has a
   `measure`.
4. Every metric a `ratio` or an `expression` names exists, in the file or inferred.
5. `time` is a date or timestamp column.
6. Every filter column and `time` is reachable from the metric's table along exactly one path.
7. Every filter value fits its column, and no order operator is on an enum, boolean or text
   column.

### Warnings

1. A simple metric or row count of the file has no time: no `time`, and no date or timestamp
   column on its table or a table it reaches.
2. A filter can never match, as far as the model's generation hints cheaply tell:
   `is_null: true` on a column that is never null (`pk` or `not_null`), a comparison or value
   outside a numeric column's `min` and `max`, or a `between` whose low end is above its high end.

## What a reader computes

This spec fixes what a metric means. How a consumer computes and publishes metrics is its own
business; the [model2data engine](https://github.com/JB-Analytica/model2data) computes each one's
known value over a generated run (`metric_values.json`), writes a dbt test per metric that checks
the warehouse gets the same number, and exports Apache Ossie 0.1.1 as tagged `osi-0.1.1-rc1` in
apache/ossie (no final 0.1.1 tag exists yet). Its README says how.
