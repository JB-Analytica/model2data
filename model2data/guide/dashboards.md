# Write a dashboard file

A dashboard is a YAML file, `<name>.dashboard.yml`, beside the model (`<stem>.model2data.yml`)
and its metrics file (`<stem>.metrics.yml`). It names metrics, `table.column` dimensions and
tables; it never holds a number, a query or a connection. model2data checks it offline;
model2data studio (https://studio.jbanalytica.com) draws it with the numbers of the model's
generated data.

The name (the file name without `.dashboard.yml`) is 1 to 64 lowercase letters, digits, `-` or
`_`, starting with a letter or digit: no dots. Set `model:` to the model's name so the check
finds it.

## 1. See what you can name

```bash
model2data metrics list -f <stem>.model2data.yml --metrics <stem>.metrics.yml
```

Each metric name is a `metric:`. A dimension is any column of the model as `table.column`; a
table is its key in the model.

## 2. Write it

```yaml
# yaml-language-server: $schema=https://www.jbanalytica.com/model2data/spec/dashboards/0.1.0/dashboard.schema.json
model2data-dashboard: 0.1.0
model: coffee_webshop
title: Sales overview
pages:
  - id: overview
    name: Overview
    grain: month
    tiles:
      - id: revenue
        kind: kpi
        title: Revenue
        metric: revenue
        compare: previous
        place: {x: 0, y: 0, w: 3, h: 2}
      - id: channel
        kind: filter
        dimension: orders.channel
        place: {x: 3, y: 0, w: 3, h: 2}
      - id: revenue-over-time
        kind: line
        metric: revenue
        place: {x: 0, y: 2, w: 6, h: 6}
      - id: revenue-by-country
        kind: bar
        metric: revenue
        by: customers.country
        place: {x: 6, y: 2, w: 6, h: 6}
      - id: latest-orders
        kind: table
        table: orders
        columns: [id, order_date, status, total_amount]
        rows: 20
        place: {x: 0, y: 8, w: 12, h: 6}
```

- Every tile has `id`, `kind` and `place`. Kinds: `kpi` (`metric`, optional `compare: previous`
  or a typed `comparison`), `line` (`metric`, optional `by`, `grain`), `bar` and `donut`
  (`metric`, `by`), `stacked` (`metric`, `by`, optional `across`, `grain`), `table` (`table`,
  `columns`, `rows` up to 50), `filter` (`dimension`), `text` and `note` (`text`).
- `grain` is `day`, `week`, `month`, `quarter` or `year`; a page's is `month` when left out.
- The grid is 12 columns: `x` 0 to 11, `w` 1 to 12, `x + w` at most 12. KPIs `h: 2`, charts
  `h: 5` to `7`. Do not overlap tiles. Keep `place` on one line.
- Page ids are unique among pages, tile ids among all tiles of the file. When editing, keep
  every id: links and comments point at them.
- Unknown keys are errors. Limits: 20 pages, 60 tiles a page, 200 characters a name.

## 3. Check it

```bash
model2data dashboard check <name>.dashboard.yml
```

It prints `file:line:column: error|warning: message`, then a count per file. It finds the
model the file's `model` names (`<model>.model2data.yml` beside it) or the only model beside
it, and the metrics
file beside the model; pass `--model` and `--metrics` to choose. With neither, metric names
are checked against the metrics the model implies.

- An error (exit 1): fix the value at that line and column, check again.
- `metric 'x' is not a metric of this model (did you mean 'y'?)`: use the suggested name, or
  add the metric to the metrics file and `model2data validate` it first.
- `by 'a.b' is not a column of this model`, `table 'x' is not a table of this model`: use a
  column or table the model has.
- `place runs past the grid`: make `x + w` at most 12.

Warnings exit 0: the file conforms, and the tile shows the name as unknown. Fix them anyway
unless the user asked for a metric that does not exist yet.

The full format is `model2data/spec/dashboards/README.md` in the package.

next: `model2data dashboard check <name>.dashboard.yml`
