# model2data — instructions for LLMs / coding agents

This file is written for an LLM or coding agent (Claude, GPT, etc.) that has file-write and
shell access and has been asked to turn a description of a data model into a demo-ready dbt
project. Read this before authoring a DBML file for `model2data`.

## What model2data does

`model2data` takes a [DBML](https://dbml.dbdiagram.io/docs/) schema file and generates, in one
command: realistic, relationship-preserving synthetic data (as dbt seed CSVs) and a complete,
runnable dbt project around it — staging models, schema tests, sources, and a DuckDB or Postgres
profile. No production data, no hand-written mock CSVs, no dbt boilerplate.

## The end-to-end workflow

Given a plain-English description of a data model (from a conversation, an existing system, a
rough ERD, a client's requirements):

1. **Author a DBML file** (`<name>.dbml`) describing the schema, following the DBML feature
   guidance below — the richer the schema, the more realistic and useful the generated data and
   dbt project will be.
2. **Install and run model2data**:
   ```bash
   pip install model2data
   model2data --file <name>.dbml --rows 200 --seed 42 --unit-tests
   ```
   Always pass `--seed` for reproducible output — useful when iterating on the DBML file, and
   when re-running for a client demo. `--rows` controls rows generated per table (raise it for a
   more convincing demo dataset, e.g. `500`-`2000`; keep it low, e.g. `20`-`50`, while iterating
   quickly on the schema itself).
3. **Verify it actually works before showing anyone**:
   ```bash
   cd dbt_<name>
   dbt deps && dbt seed && dbt run && dbt build
   ```
   `dbt build` runs the models *and* every generated test (including unit tests if you passed
   `--unit-tests`). If anything fails, don't hand this to a client — go back to step 1. Also
   check `model2data`'s own CLI output from step 2: it prints a summary including any DBML lines
   it couldn't fully parse, or any FK cycles it detected — treat those as things to fix in the
   DBML before demoing.
4. **Query the result.** DuckDB (the default adapter) produces a single `.duckdb` file inside
   the generated project directory — queryable directly, or point any BI/notebook tool at it.

## Writing DBML that gets the best results

model2data reads more of DBML than the bare minimum. Use these features — they directly improve
the quality of the generated data and the generated dbt project, not just cosmetics:

**Name columns for realistic values.** Untyped/string columns are matched by name against
patterns like `email`, `first_name`, `last_name`, `phone`, `city`, `country`, `company`, `url`,
`address`, `job_title`, and about 25 others — a column named `email` gets real-looking emails,
not lorem-ipsum text. Prefer descriptive column names over generic ones (`customer_email`, not
`field3`) whenever the underlying domain has an obvious name.

**Use `Enum` for any categorical/status column**, instead of a loose `varchar`:
```dbml
Enum order_status {
  pending
  shipped
  delivered
  cancelled
}

Table orders {
  id int [pk]
  status order_status [not null, default: 'pending']
}
```
Without this, a status-like column typed as plain text generates unrelated lorem-ipsum sentences
instead of realistic categorical values. `model2data` also emits a dbt `accepted_values` test for
enum columns automatically.

**Use `default:` for column defaults** — nullable columns with a declared default fill their
"empty" rows with that default instead of `NULL`, matching how a real database behaves:
```dbml
is_active boolean [default: true]
price_cents int [default: 1999]
```

**Use notes for documentation and numeric bounds.** A plain-text note becomes the column's
`description:` in the generated dbt schema YAML:
```dbml
age int [note: 'Customer age at signup, self-reported']
```
A note containing a JSON object with `min`/`max` constrains generated numeric values instead:
```dbml
age int [note: '{"min": 18, "max": 90}']
```
(These two forms are mutually exclusive per column — a note is read as JSON first, falling back
to plain text.)

**Declare relationships** — either syntax is fully supported and produces identical behavior:
```dbml
' inline, on the column itself
Table orders {
  id int [pk]
  customer_id int [ref: > customers.id]
}

' standalone, one-liner
Ref: orders.customer_id > customers.id

' standalone, block form
Ref {
  orders.customer_id > customers.id
}
```
Foreign keys resolve to real parent rows automatically — no extra configuration needed. This
includes **self-referencing FKs** (e.g. `employees.manager_id > employees.id` for an org
hierarchy) — these correctly respect nullability, so some rows (e.g. top-level managers) can
still have no parent.

**Use composite keys for join/bridge tables and multi-column uniqueness**, via an `indexes { }`
block:
```dbml
Table post_tags {
  post_id int [not null]
  tag_id int [not null]

  indexes {
    (post_id, tag_id) [pk]
  }
}

Ref: post_tags.post_id > posts.id
Ref: post_tags.tag_id > tags.id
```
This is the standard way to model a many-to-many relationship. Generated rows are deduplicated on
the declared composite key, and a dbt test enforcing it is generated automatically. Note: each FK
column in a composite key is generated independently — the *pair's combination* isn't guaranteed
to match a real composite key on the parent side unless the parent also enforces one via its own
`indexes { }` block.

**`Project { }` and `TableGroup { }` blocks are fine to include** (e.g. if reusing DBML exported
from dbdiagram.io) — they're recognized and silently ignored, no need to strip them out.

## Things to avoid / know about

- Don't invent DBML syntax that isn't standard — if unsure, keep to `Table`, `Enum`, `Ref`
  (block, one-liner, or inline `[ref: ...]`), `indexes { }`, and column settings
  (`pk`, `not null`, `unique`, `default:`, `note:`). Anything model2data's parser can't make
  sense of is reported as a warning (not a silent failure) in the CLI's post-run summary — read
  that summary.
- Composite *foreign keys* (a multi-column `Ref`, e.g. `Ref: t.(a,b) > t2.(c,d)`) are supported
  for parsing and generate independent per-column FK values, but — same caveat as above — the
  combination isn't guaranteed to match a real parent row unless separately enforced.
- Only DuckDB (default, zero-config) and Postgres (`--adapter postgres`, needs
  `pip install "model2data[postgres]"` and connection env vars — see README.md) are supported
  targets today.
- `--unit-tests` requires dbt-core >= 1.8 to actually run (already covered by this project's own
  floor, so no extra action needed if you installed model2data normally).

## Full CLI reference

```
model2data --file SCHEMA.dbml [OPTIONS]

--file, -f       PATH      Path to the DBML file (required)
--rows, -r       INT       Rows to generate per table (default: 100)
--seed           INT       Deterministic seed — same seed + schema always produces the same data
--name, -n       TEXT      Override the generated dbt project's name (default: derived from filename)
--force                    Overwrite the destination directory if it already exists
--adapter, -a    TEXT      duckdb (default) or postgres
--unit-tests               Also generate dbt unit test fixtures (requires dbt-core >= 1.8)
```

For anything not covered here, see [README.md](README.md) — this file exists to make a schema
→ demo turnaround fast, not to duplicate the full documentation.
