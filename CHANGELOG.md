# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.5.1] - 2026-08-26

Continued 1.0-readiness hardening: this round tested genuinely new hand-authored DBML schemas
(many-to-many bridge tables, `Project`/`TableGroup` blocks, out-of-order declarations, mixed
quote styles, CRLF line endings, wide fan-out FK schemas) end-to-end through a fresh install and
a real `dbt build`, plus added fixed-seed fuzz testing of the parser against malformed/corrupted
DBML. Both efforts found real bugs, fixed below.

### Fixed
- **Backtick-quoted identifiers kept their literal backticks.** `_strip_quotes` stripped `"` and
  `'` but never `` ` ``, even though backtick-quoted identifiers are explicitly documented as
  supported. A backtick-quoted table name came out of parsing still wrapped in backticks, which
  then leaked into generated dbt model filenames and `ref()`/`source()` calls that `dbt` could not
  even parse.
- **A quoted table name containing a space or other punctuation broke the generated dbt project
  outright.** Table names were used verbatim as CSV/seed filenames and dbt model names with no
  validation; `dbt` requires those to be valid identifiers. Table names are now sanitized (via a
  new `_sanitize_table_name`, applied consistently to table definitions and every place a Ref
  references a table) into a dbt-safe identifier, while still round-tripping an already-valid name
  — including a leading-underscore name like `_dlt_version` — unchanged.
- **A quoted column name containing a space or other special character broke every generated
  `not_null`/`unique`/`relationships` test.** dbt's built-in generic tests interpolate the schema
  YAML's `name`/`field` value directly into compiled SQL with no quoting of their own; an unquoted
  `select display name` is a SQL syntax error. Non-bare column identifiers are now pre-quoted
  (ANSI double-quoting, reusing the existing `_quote_sql_identifier` helper) wherever they're
  emitted into generated schema YAML.
- **A single missing/corrupted brace could silently drop the rest of a DBML file.** A truncated
  file, or one with a removed closing `}`, left the parser's `current_table` (or a `Project`/
  `TableGroup` ignored-block depth counter) permanently "open" for the remainder of the file —
  every subsequent line was then either misparsed as a bogus column of the dangling table, or
  swallowed as ignored-block content, with zero warning and zero surviving tables. The parser now
  detects any block (table, enum, Ref block, `indexes{}` block, note block, `Project`/
  `TableGroup` block) still open at end-of-file, recovers what it can (an unclosed table is still
  added to the result), and always emits a parse warning.
- **A stray character before a top-level keyword silently dropped the whole construct.** Any line
  that didn't match a recognized top-level construct was previously ignored with no warning at
  all (originally so `Project`/`TableGroup` blocks could be skipped silently) — so a single
  corrupted character landing right before a `Table ...{` line made that `.startswith("table ")`
  check fail, and the entire table silently vanished. `Project`/`TableGroup` blocks are now
  recognized and skipped explicitly (tracked by brace depth so a nested brace inside one doesn't
  close it early); anything else unrecognized at the top level now emits a parse warning instead
  of disappearing.

### Added
- `examples/tagging_m2m.dbml` — a many-to-many bridge table (composite PK on both FK columns)
  alongside `Project {}`/`TableGroup {}` blocks.
- `examples/mixed_quotes_crlf.dbml` — mixed backtick/double-quoted identifiers (including one
  with a space), inline and standalone comment placement, and CRLF line endings.
- `tests/fixtures/` — regression fixtures for out-of-order table/enum/Ref declarations, a
  composite-only table with no single-column id/pk, a wide fact table with 9 FK columns, and a
  minimal single-table schema with no refs.
- `tests/test_dbml_parser_fuzz.py` — fixed-seed fuzz testing of `parse_dbml`: randomized garbage
  DBML fragments (asserting no unhandled exception) and targeted corruptions of every bundled
  example (truncation, brace removal, unicode/control-character injection, duplicated table
  names, stripped keywords, injected null bytes — asserting the parser never silently loses
  structure without at least one parse warning).

## [0.5.0] - 2026-08-26

1.0-readiness hardening pass: closes out the highest-severity gaps identified by direct code
inspection ahead of a 1.0 release — every one of these could previously produce silently wrong
or incomplete output with zero indication anything went wrong.

### Fixed
- **Inconsistent/unsafe escaping in generated YAML and SQL.** Most of `dbt/tests.py` hand-built
  YAML by string-interpolating table/column names directly into f-strings, and the composite-key
  singular SQL test spliced raw column names into a `group by` clause with no quoting. A DBML
  schema using a quoted identifier containing a space, colon, or single quote (all valid DBML)
  produced invalid/garbled generated YAML or SQL — `model2data` reported success, and `dbt seed`/
  `dbt run`/`dbt test` then failed on the garbled output far from the actual cause. `generate_dbt_yml`
  now builds one Python structure per generated YAML file and dumps it with a single `yaml.safe_dump`
  call instead of hand-rolled line lists; composite-key SQL now double-quotes each identifier
  (ANSI quoting, valid on both supported adapters); and the two places a raw name was spliced into
  a single-quoted Jinja `source(...)` string literal (`create_staging_models`, `generate_unit_tests`)
  now escape embedded single quotes.
- **A genuine multi-table FK cycle (A → B → A, or longer) was silently swallowed** by
  `_topological_table_order`'s fallback for tables the topological sort didn't reach — a fallback
  that was also (correctly) catching genuinely disconnected tables, so the two cases were
  indistinguishable and neither was ever surfaced. Only a table stuck in an actual unresolved
  cycle now hits that fallback (a genuinely disconnected table is already picked up by the normal
  zero-indegree pass), and the CLI's post-run summary now prints a clear warning naming the
  affected tables — generation still completes, but the user is told their FK data may not
  respect all relationships instead of finding out via a broken `relationships` test.
- **Silent partial DBML parses.** A malformed `Ref` line, a `Ref` pointing at a table/column that
  doesn't exist, an unparseable column definition, and a garbled `indexes {}` line were all
  dropped with a bare `continue` and no record kept — `model2data` reported success on an
  incompletely-understood schema. `parse_dbml` now collects these into a parse-warnings list
  (mirroring the existing `get_unmapped_columns()` pattern), surfaced by the CLI's summary section
  immediately after generation. Parsing still never raises on these — DBML has features (`Project`
  blocks, `TableGroup`, etc.) this tool intentionally doesn't need to understand, and those stay
  silent by design.

### Added
- Composite (multi-column) standalone `Ref` blocks are now parsed: `Ref: a.(x, y) > b.(x, y)`
  expands into one single-column ref per zipped column pair, so every existing FK-aware consumer
  (generation, `relationships` tests, `classify_refs`) handles it with no changes. Known
  limitation, documented in code: since each column is generated independently, the *combination*
  of values in the child table's two FK columns isn't guaranteed to match a real combination in
  the parent — solving joint-value consistency across independently-generated FK columns is out
  of scope.

### Changed
- **`dbt-core` floor corrected from an unverified `>=1.5.0` to a verified, intentionally-recent
  `>=1.8.5`** (dbt-core's release current as of ~August 2024, chosen instead of the oldest
  technically-working version since an unmaintained multi-year-old floor isn't a goal for a 1.0
  release; also happens to unify with the `--unit-tests` flag's own pre-existing `>=1.8`
  requirement, removing the previous "base floor 1.5, but this one flag needs 1.8+" caveat
  entirely). Manual testing against isolated environments pinned to dbt-core 1.5.12, 1.8.5/1.8.8/
  1.8.10, 1.9.11, and every 1.10.x patch from 1.10.0 found that the `arguments:`-nested generic
  test config this project started generating in 0.4.3 (to silence a deprecation warning on
  modern dbt-core) is a **hard parse error** on dbt-core below 1.10.5, so `generate_dbt_yml` now
  goes back to emitting the older, flat (non-`arguments:`-nested) generic test config across the
  board — universally compatible from 1.8.5 through current dbt-core (which still accepts the
  flat form, just with a soft, non-fatal deprecation warning). `dbt-core>=1.8.5` (plus matching
  `dbt-duckdb>=1.8.4` / `dbt-postgres>=1.8.0`) is enforced in `pyproject.toml`, and CI's `test`
  job gained a `dbt-version: [floor, latest]` matrix dimension (on one representative Python
  version, to avoid crossing it with the full Python-version matrix) that generates a project from
  `examples/hackernews.dbml` and runs `dbt deps && dbt seed && dbt run && dbt test` against both
  the pinned floor and whatever `uv sync` naturally resolves as latest, on every push/PR — so both
  ends of the supported range stay continuously verified instead of checked once by hand.
- Known caveat (dbt-core-internal, not a model2data bug): on the 1.8.x dbt-core line specifically,
  `--unit-tests` fails with a `syntax error` for any DBML column named after a SQL reserved word
  (e.g. `by`, present in `examples/hackernews.dbml`) — dbt-core's unit-test CSV-fixture rendering
  doesn't quote such identifiers on that release line. Fixed in later dbt-core; every other
  `--unit-tests` path, and the entire non-unit-test pipeline, works cleanly on 1.8.5.

## [0.4.3] - 2026-08-26

### Added
- Inline column-level `[ref: > table.column]` DBML foreign-key syntax is now parsed (alongside the previously-supported standalone `Ref { table.col > table.col }` block form). `>`, `<`, and `<>` inline operators all normalize into the exact same `refs`/many-to-many structures a standalone `Ref` block produces, so FK-aware generation and dbt `relationships` tests work identically regardless of which syntax a DBML file uses. `examples/ecommerce.dbml`'s `order_items` table now demonstrates this syntax.

### Fixed
- **A relationship declared via `Ref` was tested by dbt even when the generator didn't have enough signal to make the column FK-aware, guaranteeing a false test failure.** `generate_dbt_yml` emitted a `relationships` test for every parsed ref, but `generate_data_from_dbml` only fills a column with values sampled from the parent table for refs `classify_refs` recognizes as reliable FKs (target is a `pk` or named `id`) or attribute-mirror refs riding on an existing FK between the same two tables; a ref that met neither condition (e.g. `examples/hackernews.dbml`'s `_dlt_loads.schema_version_hash < _dlt_version.version_hash`, referencing a non-`id`, non-`pk` hash column) still got random, unrelated data but was tested against the parent anyway. `generate_dbt_yml` now only emits a `relationships` test for refs the generator actually makes FK-aware.
- **Nullable `int`/`bigint`/`smallint` columns round-tripped through the CSV seed as floats (e.g. `70.0` instead of `70`, with `None` becoming an empty float cell).** Building a `pd.DataFrame` from a plain Python list mixing ints and `None` silently upcasts the column to `float64`. Such columns are now cast to pandas' nullable `Int64` dtype after generation, so whole numbers and nulls serialize correctly.
- **A seed column containing all-digit generated text (e.g. an `ean13`-typed SKU, or a zero-padded postcode) could be mis-inferred as an integer by dbt/duckdb's CSV loader**, either overflowing (`examples/ecommerce.dbml`'s `products.sku` failed `dbt seed` outright: a 13-digit EAN13 value doesn't fit a 32-bit `INTEGER`) or silently dropping meaningful leading zeros. Every column `generate_column_values` fills with free text (name-pattern lookups, a literal Faker provider, or the generic fallback) is now pinned to `varchar` via a generated seed `column_types` config, so dbt never has to guess.
- **A dbt-core >= 1.10 deprecation:** the generated `accepted_values` schema test nested its `values` list directly instead of under an `arguments:` key (unlike the `relationships` test just below it in the same file), triggering a `MissingArgumentsPropertyInGenericTestDeprecation` warning on every `dbt seed`/`build` for a table with an enum-typed column.
- `examples/saas_platform.dbml` and `examples/ecommerce.dbml` had several columns (`role`, `plan_tier`, `status`, `event_type`) typed as a bare Faker-provider-name or generic `text` with no `Enum` and no realistic name-pattern match, so they generated multi-sentence lorem-ipsum paragraphs (or, for `role`, login-style usernames) instead of plausible short values. These now use proper DBML `Enum` types with realistic values, also serving as additional worked examples of the `Enum` feature.

## [0.4.2] - 2026-08-26

### Fixed
- **The composite-key singular SQL test was invisible to dbt.** `generate_dbt_yml` wrote it to `tests/`, but the generated `dbt_project.yml` declares `test-paths: ["data-tests"]`, so dbt never discovered or ran it even though the `.sql` file existed on disk. It's now written to `data-tests/`, and `create_project_scaffold` now creates that directory (instead of the now-unused `tests/`).
- **`--unit-tests` generated YAML dbt never parsed.** `generate_unit_tests` wrote `tests/unit/test_stg_<table>.yml`, a path outside every one of `model-paths`/`test-paths`/`seed-paths`/`analysis-paths`/`snapshot-paths`, so `dbt ls --resource-type unit_test` reported "No nodes selected!" despite the YAML files existing. Unit test fixtures are now written under `models/staging/ut_stg_<table>.yml`, alongside each staging model's schema YAML, where dbt's `model-paths` config actually parses them from.
- **Self-referencing foreign keys (e.g. `employees.manager_id > employees.id`) produced orphaned references.** While a table is still being generated, its own DataFrame isn't in the `generated` lookup yet, so a column referencing its own table's PK fell through to unrelated random values instead of the real PK pool, failing the generated `relationships` test. Self-referencing FK columns are now re-resolved against the table's own just-built PK column right after that table's DataFrame is built, respecting existing nullability so legitimate top-level rows (e.g. an employee with no manager) can still be null.
- Added a dbt-CLI integration test (`tests/test_dbt_integration.py`) and a fast path-config-consistency unit test (`tests/test_dbt_tests.py`) that generate a project and confirm dbt itself discovers and runs the generated test/unit-test nodes, guarding against this class of "file exists on disk but outside dbt's configured resource paths" bug going forward.

## [0.4.1] - 2026-08-26

### Fixed
- **Primary key columns could generate duplicate values, failing the generated `unique_stg_<table>_id` dbt test at moderate-to-large row counts.** `generate_column_values`'s `ensure_unique` flag was only honored by the generic/name-inference fallback branch; the int, decimal/float, and uuid/hash branches ignored it and generated independent random values with no collision handling. Since the default integer range is `[0, 100]`, any PK column with more than a handful of rows was virtually guaranteed to collide. Int PKs now sample without replacement from a (default-widened, if unspecified) range, or fall back to bounded-retry regeneration when an explicit user-specified range is genuinely too small; decimal/float and uuid PKs now also get a bounded-retry dedup pass. Additionally, a `[pk]` column is now always treated as implicitly not-null (mirroring real database semantics), so a PK declared without an explicit `not null` setting can no longer end up with a `None` value.

## [0.4.0] - 2026-08-26

### Added
- DBML `Enum` block support: `Enum <name> { ... }` blocks are parsed, and columns typed with an enum's name generate values only from that enum's set and get an `accepted_values` dbt schema test.
- Table and column `Note` text (both `Note: '...'` and multi-line `Note { ... }` forms) is now captured as `TableDef.description` / `ColumnDef.description` and rendered as `description:` fields in the generated dbt YAML, instead of being silently dropped or requiring the note to be JSON. The existing JSON `{"min": ..., "max": ...}` note convention keeps working exactly as before.
- Composite primary/unique keys declared in a table's `indexes { }` block (e.g. `(a, b) [pk]`) are now parsed into `TableDef.composite_keys`, deduplicated in generated seed data via bounded retry, and get a singular dbt SQL test under `tests/` that fails on any duplicate combination.
- `default:` column settings are now parsed into a typed `ColumnDef.default` (string/int/float/bool; backtick SQL expressions like `` `now()` `` are safely ignored). Nullable columns with a parsed default now fill "empty" rows with that default instead of `None`, mirroring real database behavior.
- `--unit-tests` CLI flag: opt-in generation of deterministic dbt unit test fixtures (`tests/unit/test_stg_<table>.yml`) built from a sample of the actually-generated seed rows. Requires dbt-core >= 1.8 to run; off by default since this project only requires `dbt-core>=1.5.0`.
- Many-to-many (`<>`) refs are no longer silently discarded during parsing; they're captured separately (`model2data.parse.dbml.get_many_to_many_refs()`) while remaining excluded from FK-based generation, as before.
- `examples/advanced_features.dbml` now also demonstrates an `Enum`-typed column and a composite unique key via `indexes { }`.

### Fixed
- Plain-text column notes (the normal DBML idiom, e.g. `note: 'the primary email address'`) are no longer discarded just because they aren't JSON.

## [0.3.0] - 2026-08-24

### Added
- Postgres output adapter: `model2data --adapter postgres` (requires `pip install "model2data[postgres]"`). Connection details are read from `MODEL2DATA_PG_HOST`, `MODEL2DATA_PG_PORT`, `MODEL2DATA_PG_USER`, `MODEL2DATA_PG_PASSWORD`, and `MODEL2DATA_PG_DATABASE`, with local-dev-friendly defaults.
- Name-aware synthetic data: untyped/string columns are now matched against ~35 common column-name patterns (`email`, `first_name`, `last_name`, `phone`, `city`, `country`, `company`, `url`, ...) and filled with realistic Faker output instead of generic placeholder text.
- Post-run generation summary: after each run, the CLI prints tables generated, total rows, relationships found in the DBML, and any columns that fell back to generic text (with column name and type), so gaps in coverage are visible immediately instead of silently producing weak data.
- CI now runs a Postgres integration job: it generates a project with `--adapter postgres` against a live `postgres:16` service container and runs `dbt seed && dbt run` end-to-end.

### Fixed
- **Generated projects no longer break `dbt run` when installed via pip.** The dbt schema-naming macro was copied into new projects using a path relative to the current working directory, so it silently copied nothing unless model2data happened to be run from inside its own source checkout — every real installation hit a `Catalog Error: Table ... does not exist!` on the very first `dbt run`. The macro is now located relative to the installed package.
- The DBML parser no longer mis-parses multi-line `Note { ... }` blocks inside a table as a spurious column named `Note` with data type `{`. This affected the bundled `examples/hackernews.dbml`, among other schemas using this note syntax.
- Every command in `README.md` and `examples/README.md` used a `model2data generate --file ...` form that the CLI has never accepted (`Got unexpected extra argument (generate)`); all examples are corrected to `model2data --file ...`.

## [0.2.3] and earlier

See git history for changes prior to this changelog's introduction.
