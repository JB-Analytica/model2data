# model2data dashboards — spec 0.1.0

A dashboard file lays out a dashboard over a model2data model: pages of tiles on a grid, each tile
showing a metric, a breakdown of one, a filter, a table's rows or some text. It is a third,
optional document beside the [model](../README.md) and its [metrics file](../metrics/README.md),
`<name>.dashboard.yml`, versioned on its own. [`dashboard.schema.json`](dashboard.schema.json) is
its JSON Schema.

A dashboard file **names** things and never holds a value: a metric by its name, a dimension as
`table.column`, a table by its key. It names no data source, no query and no number. Whatever
draws it computes every value from the model's data, so the same file shows the numbers of any
run of the model, and a number on a dashboard cannot disagree with the metric it names.
[model2data studio](https://studio.jbanalytica.com) draws a dashboard file with the numbers of the
model's generated data.

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/dashboards/0.1.0/dashboard.schema.json
model2data-dashboard: 0.1.0
model: coffee_webshop                   # the model's `name`; optional
title: Sales overview                   # optional
pages:
  - id: overview
    name: Overview
    grain: month                        # optional: day | week | month | quarter | year
    tiles:
      - id: revenue
        kind: kpi
        title: Revenue
        metric: revenue
        compare: previous
        place: {x: 0, y: 0, w: 3, h: 2}
      - id: revenue-by-country
        kind: bar
        metric: revenue
        by: customers.country
        place: {x: 3, y: 0, w: 9, h: 5}
```

A complete example, which checks clean against the example model and metrics file beside it, is
[`../examples/coffee_webshop_sales.dashboard.yml`](../examples/coffee_webshop_sales.dashboard.yml).

## What is normative

This README and `dashboard.schema.json`, including every `description` in the schema. A document
**conforms** when, read under the model spec's [YAML profile](../README.md#the-yaml-profile), it
validates against the schema and meets every check under [Checks beyond the
schema](#checks-beyond-the-schema). Examples are informative.

A reader reports issues each with a **severity**: an **error** where the document does not
conform, and a **warning** for something it points out in a document that does (see
[Warnings](#warnings)). A reader that reads the document from text gives each issue the 1-based
line and column of the value at fault; one that reads parsed data gives its document path
(`pages.0.tiles.2.metric`).

## Versioning

The dashboard spec is versioned on its own, apart from the model spec, the metrics spec and the
engine, with [semantic versioning](https://semver.org). The version is in the schema's `$id`:

```
https://www.jbanalytica.com/model2data/spec/dashboards/0.1.0/dashboard.schema.json
```

A document names the version it is written against in `model2data-dashboard:`. A patch release
changes wording only; a minor release adds something optional, and, before 1.0.0, may also make a
valid document invalid or change what one means, and says so. A reader refuses a minor version it
does not implement (from 1.0.0, a major one) rather than guess.

## The file and its name

A dashboard is one YAML document, UTF-8, of at most 200 KB, read under the model spec's YAML
profile: the YAML 1.2 core schema, no duplicate keys, no anchors, aliases, merge keys or tags.

Its file is `<name>.dashboard.yml` (or `.yaml`), and its **name** is the file name without that
suffix: the dashboard's identity, where the `title` is only what people see. A name is 1 to 64
lowercase letters, digits, `-` or `_`, starting with a letter or digit
(`^[a-z0-9][a-z0-9_-]{0,63}$`), so it holds no `.`. Which model a dashboard is for is said by its
`model` key, not by its file name: `coffee_webshop_sales.dashboard.yml` says
`model: coffee_webshop`.

Unknown keys are errors, anywhere in the file, so a misspelt key is caught rather than ignored.
There are no extension keys in 0.1.0.

## The document

### Top level

| Key | | |
|-----|-|-|
| `model2data-dashboard` | required | The spec version, `0.1.0`. |
| `model` | optional | The `name` of the model the dashboard is for (a model without `name` goes by its file's stem). |
| `title` | optional | The dashboard's title for people. |
| `pages` | optional | The pages, in the order they are shown. None is a dashboard with no pages. |

### Pages

| Key | | |
|-----|-|-|
| `id` | required | The page's id. See [Ids](#ids). |
| `name` | required | The page's name for people. |
| `grain` | optional | What the page's tiles over time are on: one of the [grains](#grains). `month` when left out. |
| `tiles` | optional | The page's tiles. |

### Tiles

Every tile has an `id`, a `kind` and a `place`; `title`, a heading shown on the tile, is optional
on every kind. The other fields are read by some kinds, and left out of the others:

| Field | Read by | Holds |
|-------|---------|-------|
| `id` | every kind, required | The tile's id. See [Ids](#ids). |
| `kind` | every kind, required | One of the kinds below. |
| `title` | every kind | A heading on the tile. |
| `metric` | `kpi`, `line`, `bar`, `stacked`, `donut` | A metric, by name: one of the metrics file's, or one the model implies. |
| `comparison` | `kpi` | Text shown under the number. It is not computed. |
| `compare` | `kpi` | `previous`: the last complete period against the one before it. |
| `grain` | `line`, `stacked`, `kpi` with `compare` | A grain of the tile's own, over the page's. |
| `by` | `bar`, `donut`, `line`, `stacked` | A dimension, `table.column`. |
| `across` | `stacked` | A dimension, `table.column`, whose members are the bars. |
| `dimension` | `filter` | The dimension it filters on, `table.column`. |
| `table` | `table` | A table of the model, by its key. |
| `columns` | `table` | A list of that table's column names; all of them when left out. |
| `rows` | `table` | How many rows to show, 1 to 50; 10 when left out. |
| `text` | `text`, `note` | The text, at most 2000 characters. Several lines as a `|` block. |
| `place` | every kind, required | Where the tile sits: `{x, y, w, h}`. See [The grid](#the-grid). |

A field a kind does not read is allowed and ignored, so a tile keeps its fields when its kind is
changed.

### Tile kinds

| Kind | Shows |
|------|-------|
| `kpi` | One number: `metric`, over everything the page's filters leave. `compare: previous` adds the last complete period against the one before it, on the tile's `grain`, else the page's. `comparison` is text shown under the number instead; it is not computed. |
| `line` | `metric` over time, one point per period of `grain` (else the page's). With `by`, one line per member of that dimension. |
| `bar` | `metric` broken down by `by`: one bar per member. |
| `stacked` | `metric` over time on `grain`, each bar stacked by the members of `by`. With `across`, the bars are the members of that dimension instead of periods. |
| `donut` | `metric`'s share per member of `by`. |
| `table` | Rows of one table: `table`, its `columns` and at most `rows` rows. |
| `filter` | A filter for its page on `dimension`. A date or timestamp column gives a date range, any other column a pick of one member. It narrows every tile on its page. |
| `text` | Text on the dashboard itself, such as a heading or an explanation: `text`. |
| `note` | A note to whoever reviews the dashboard, drawn apart from the rest: `title` and `text`. It is not part of the finished dashboard. |

A dimension is any column of the model, written `table.column` (the last `.` separates the
column, so `schema.table.column` names a table with a schema). Which columns the metrics file
offers as dimensions does not restrict a dashboard.

### Grains

`day`, `week` (ISO weeks, Monday first), `month`, `quarter`, `year`: the periods the engine
computes a metric's values per (`model2data.metrics.by_period`). A page's `grain` is what its
`line` and `stacked` tiles, and its `kpi` tiles with `compare`, are on; a tile's own `grain`
overrides it.

### The grid

A page is a grid **12 columns wide**, with rows running down the page. A tile's `place` is four
whole numbers:

| | | Range |
|-|-|-------|
| `x` | the column it starts in | 0 to 11 |
| `y` | the row it starts on | 0 to 1000 |
| `w` | its width, in columns | 1 to 12 |
| `h` | its height, in rows | 1 to 100 |

Keep `x + w` at most 12: a tile that runs past the right edge is a warning. Tiles should not
overlap. A row is short, about 40 pixels where model2data studio draws it: a row of KPIs is
`h: 2`, a chart `h: 5` to `7`. Write `place` in flow style on one line, `place: {x: 0, y: 0, w: 3, h: 2}`; the canonical writer does.

### Ids

Every page has an `id`, unique among the file's pages, and every tile an `id`, unique among
**all** the file's tiles, on every page. Page ids and tile ids are separate: a page and a tile may
share one. An id is any text up to 200 characters; lower-case words joined by `-`
(`revenue-by-country`) read best.

Ids are what everything outside the file holds on to: a link to a page, a comment on a tile, a
tool's edit of one tile. A tool that edits a dashboard keeps every id it does not delete, and
never renumbers or derives them from position or title, so moving, resizing or retitling a tile
leaves what points at it pointing at it.

### Text and limits

Every name, title, id, `comparison`, metric, dimension, table and column name is text of at most
200 characters; a `text` at most 2000. A value written as a number where text is expected is read
as its text (`title: 2024` is the title "2024"). An optional field written as null (`grain:` with
nothing after it) is read as left out.

| | At most |
|-|---------|
| pages in a dashboard | 20 |
| tiles on a page | 60 |
| `columns` of a table tile | 50 |
| `rows` of a table tile | 50 |
| the file | 200 KB |

## Checks beyond the schema

A conforming document also meets these:

1. `model2data-dashboard` is a version this reader implements (0.1.x).
2. Page ids are unique among the pages; tile ids are unique among all the tiles of the file.
3. The file is at most 200 KB.

## Warnings

Read against a model and its metrics, a reader warns, and the document still conforms, when

1. a tile's `metric` is not a metric of the model: neither in its metrics file nor implied by it
   (see [Inferred metrics](../metrics/README.md#inferred-metrics));
2. a `by`, `across` or `dimension` is not `table.column`, names a table the model does not have,
   or names a column its table does not have;
3. a table tile's `table` is not a table of the model, or one of its `columns` is not a column of
   that table;
4. `model` is not the name of the model it is read against.

Read with or without a model, a reader warns when a tile's `x + w` is more than 12.

A warning names the closest name the model has, when one is close (`did you mean 'revenue'?`). A
name is a warning rather than an error because a dashboard is often written before its metric
is, or kept while a model changes: a renderer shows the tile, saying the name is unknown.

## What a reader does

This spec fixes what a dashboard file says. The
[model2data engine](https://github.com/JB-Analytica/model2data) reads, checks and writes it
(`model2data dashboard check`, `model2data.dashboards`), and computes metric values
(`model2data.metrics`), but does not draw dashboards. Its canonical writer puts keys in the order
of the tables above (a tile's: `id`, `kind`, `title`, the content fields, `place`), `place` and
`columns` in flow style, and a `text` of several lines as a `|` block. Comments are a person's:
a tool that wants to keep them edits the text rather than writing the document anew.
