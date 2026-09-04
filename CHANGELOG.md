# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed
- **A column's declared type now outranks the guess made from its name.** DBML gives one place to
  say which generator a column should use — its type — and a recognised column name used to
  overrule it silently: `billing_country state` generated countries, `first_name email` generated
  first names, and the declared type had no effect whatsoever. Any zero-argument Faker provider
  works as a type name, and it now wins. This was the one behaviour users consistently read as a
  bug rather than a rule.

  SQL types that share a name with a provider (`text`, `json`, `jsonb`, `xml`, `binary`, `year`)
  are still read as the SQL type, so the commonest declaration in any schema — `email text` —
  keeps generating emails. Names remain the inference for everything untyped; only an explicit,
  non-SQL provider type overrides them.

## [1.1.0] - 2026-09-03

### Added
- **Per-table row counts.** `generate_data_from_dbml(..., row_overrides={"orders": 5000})` and the
  CLI's repeatable `--rows-for TABLE=N` set the row count for individual tables, falling back to
  `--rows` for the rest. Real schemas are rarely uniform — a few dimension rows against a fact
  table two orders of magnitude larger is the normal shape, and generating the same count for every
  table makes joins and aggregates behave nothing like the warehouse being modelled.
  `--rows-for` is validated before any files are written, so a mistyped table name fails
  immediately instead of leaving a half-scaffolded project behind.

### Changed
- The two tests that reached into the private `_determine_row_count` via `monkeypatch` to fake
  per-table sizing now use `row_overrides` directly.

## [1.0.0] - 2026-08-27

First stable release. Mostly about the project's presentation and long-term stance, plus a final
scrutiny pass that turned up a handful of generation bugs in genuinely untested territory
(a fresh domain schema, a large 18-table schema, and real Postgres) — all fixed below before
tagging. A follow-up depth pass then hammered those same four fixes specifically (many seeds, many
row counts, extreme value-space-vs-row-count ratios, and real end-to-end `dbt build` runs, not
just Python-level checks) instead of sweeping new territory again, and found one more bug in the
same neighborhood — also fixed below, with 495 new parametrized regression cases
(`tests/test_release_stress.py`) added as permanent coverage.

### Added
- `LLMS.md`: instructions for LLM/coding-agent users, covering the full DBML feature set
  model2data understands and the exact command sequence to go from a schema description to a
  running dbt project.

### Fixed
- **`dbt build` failed on every freshly generated project**, with or without `--unit-tests`,
  typically as `Runtime Error in model stg_<table> ... Table with name <table> does not exist`.
  Staging models read from `{{ source('raw', '<table>') }}`, and the generated
  `models/staging/__sources.yml` declared each seed as a dbt *source*. But a dbt source is only an
  assertion that some relation already exists — it carries no DAG edge back to the seed that
  actually materializes it. `dbt build` schedules seeds and models in a single dependency-ordered
  pass, so with no edge joining the two, a staging model (or a unit test needing to introspect
  the real relation) could be scheduled before its seed had ever been loaded. It went unnoticed
  for the project's whole history because CI only ever ran `dbt seed` and `dbt run` as separate,
  already-ordered steps, never a bare `dbt build` against a fresh database.
  Fixed by having staging models `ref('<table>')` the seeds instead: seeds are first-class refable
  dbt nodes, so `ref()` creates the real DAG edge and one `dbt build` now orders seeds before the
  models that read them. `--unit-tests` fixtures reference the seeds the same way. The now-dead
  `__sources.yml` is no longer generated; the table descriptions it carried (DBML `Note` → dbt
  docs) moved to the seeds properties YAML at `seeds/raw/__seed_config.yml`, so raw tables stay
  documented on the node that actually exists. Verified from a clean install on all four bundled
  examples: a bare `dbt build` on a fresh database reaches `ERROR=0` for each. A permanent
  dbt-CLI regression test (`tests/test_dbt_integration.py::
  test_bare_dbt_build_succeeds_on_a_fresh_database`) now runs exactly that.
- **The PyPI page had no project links, classifiers, keywords, author, or license metadata**, so
  its sidebar was effectively empty and there was no link back to the repository, issues, or
  changelog from the package page. All now declared in `pyproject.toml`.
- **Five links in the PyPI description pointed at repository-relative paths** (`LICENSE`,
  `CONTRIBUTING.md`, `DEVELOPMENT.md`, `CODE_OF_CONDUCT.md`, `LLMS.md`) and so 404'd on PyPI —
  the same class of bug as the broken images fixed above, just in link targets rather than image
  sources. `README_PYPI.md` now uses absolute GitHub URLs; `README.md` keeps the relative ones,
  which are correct there.

- **`--seed` did not actually produce identical data across runs.** `_random_datetime` anchored
  its window on `datetime.now()` but offset by a whole number of seconds, so the anchor's
  microsecond component leaked straight through into every generated timestamp: two runs with the
  same seed produced rows differing only in their sub-second fraction. Every other column was
  byte-identical, which is why it went unnoticed — but it was enough to make committed seed
  fixtures churn on each regeneration, defeating the entire purpose of `--seed`. The window is now
  anchored to midnight, so timestamps carry no sub-second component and same-seed runs are
  byte-identical. (Date and timestamp columns are still generated relative to the current date, so
  regenerating on a later day shifts them — determinism holds for a given day.)
- **`zip()` without `strict=` when expanding a composite `Ref`.** Guarded by an explicit
  length-equality check immediately above, so not reachable in practice, but now stated
  explicitly rather than relying on that guard staying in place. Surfaced by the ruff fix below.

### Changed
- Ruff's `target-version` was pinned to `py39`, below the project's own `requires-python =
  ">=3.10"`, so it lint-checked against a Python older than any supported version and never
  flagged 3.10+ idioms. Now `py310`.

- **Every `dbt build` printed a deprecation warning.** Generated schema tests passed their
  parameters as bare keys (`relationships:` with `to:`/`field:` directly under it), the shape dbt
  now deprecates in favour of nesting them under `arguments:` — so every run of every generated
  project ended with a `MissingArgumentsPropertyInGenericTestDeprecation` block. Noise in a
  project whose whole purpose is to be handed straight to someone else. Generated tests now use
  the `arguments:` shape, which required raising the dbt-core floor, since older versions
  hard-error on it (verified: 1.10.4 and 1.10.5 both reject it).
- **Raised the dbt-core floor to `>=1.11`** (from `>=1.8.5`; `dbt-duckdb`/`dbt-postgres` likewise),
  now tracking [dbt's own support policy](https://docs.getdbt.com/docs/dbt-versions) rather than a
  hand-picked date cutoff — dbt Labs supports each minor for one year, and 1.11 is the oldest that
  still is. This makes generated projects warning-free on every dbt-core version dbt itself
  supports, and lets the two now-obsolete caveats go: the 1.8.x unit-test failure on
  reserved-word column names, and the "unit tests need a newer dbt than the base floor" note.
  CI's floor job pins 1.11 and runs a real bare `dbt build` against both it and the latest
  release. Users pinned to older dbt-core can stay on model2data 0.5.x.

- **Every `dbt build` printed an "unused configuration paths" warning.** The generated
  `dbt_project.yml` declared a `models.<project>.marts` config block, but model2data only ever
  generates staging models, so the path matched no resource and dbt warned about it on every
  single run. The block is now commented out, with a note explaining when to uncomment it — the
  scaffolding intent is preserved for anyone adding their own marts models, without the noise.

- **The PyPI project page's description rendered broken.** Both embedded images used paths
  relative to the GitHub repository, which PyPI's renderer doesn't resolve — they appeared as
  broken image icons. The architecture diagram, a GitHub-flavored-Markdown Mermaid code fence,
  rendered as a raw, unparsed code block on PyPI (which doesn't support Mermaid) instead of a
  diagram. Fixed with a PyPI-specific `README_PYPI.md` (absolute image URLs, no Mermaid fence —
  the diagram's content is already covered in the prose immediately below it) now used as the
  package's `readme` in `pyproject.toml`; `README.md` keeps the richer GitHub-rendered version
  unchanged. Verified with `twine check` and a manual inspection of the packaged METADATA.
- **A composite key made of FK columns (the standard join/bridge-table pattern) could silently
  break referential integrity.** `_deduplicate_composite_keys`'s bounded-retry loop regenerated a
  colliding row's FK columns via the column's own type-based generator instead of resampling from
  the real parent id pool, so a retry could leave a `post_tags.post_id` (for example) that
  matched no real `posts.id` at all. Only surfaced under differential parent/bridge cardinality
  (small parent tables, a much larger bridge table) — every table getting the same `--rows` count
  kept collisions rare enough to hide it. Fixed by threading `fk_lookup`/`generated` into the
  dedup pass so a retry on an FK column resamples from the actual parent column (self-refs use
  the table's own already-resolved column). Regression test added.
- **An enum column crashed generation outright if the enum's name happened to contain "int" as a
  substring** (e.g. `maintenance_type`, `sprint_status`). `_coerce_integer_dtypes` substring-
  matched the raw declared type against `int`/`integer`/`bigint`/`smallint` with no enum guard,
  so it mistook the column for numeric and crashed casting its real string values ("repair",
  "cleaning", ...) to `Int64`. Fixed by skipping enum columns in that check, mirroring the
  enum-first guard `generate_column_values` already uses. Regression test added.
- **A nullable foreign-key column could never actually generate a null**, even across hundreds of
  rows — contradicting `LLMS.md`'s own documented behavior for self-referencing FKs ("some rows,
  e.g. top-level managers, can still have no parent") and, more broadly, any ordinary nullable FK
  (an order with no customer, an optional `manager_id`). `generate_column_values` returned early
  as soon as an `fk_series` was supplied, before the shared nullability pass at the end of the
  function ever ran. Fixed by folding the FK branch into the same if/elif dispatch chain so it
  falls through to nullability handling like every other branch. Two regression tests added
  (nullable FK can be null; `not null` FK is never null).
- **A `[unique]` column (not the primary key) had no actual uniqueness guarantee**, despite
  getting the same dbt `unique` schema test as a `pk` column — generation only ever passed
  `ensure_unique=True` for `pk` columns, so a unique column (a promo code, VIN, email) could
  non-deterministically fail its own generated dbt test on a chance collision in the fallback-text
  generator's effective value space. Fixed by treating `unique` the same as `pk` for generation
  purposes at both call sites (main column generation and self-referencing FK resolution).
  Regression test added.
- **A composite primary key's member columns could still end up `null`, when declared only via an
  `indexes { (a, b) [pk] }` block with no `pk`/`not null` on the individual columns themselves** —
  the standard DBML shape for a join/bridge table's key (`examples/tagging_m2m.dbml`'s
  `post_tags.post_id`/`tag_id` is a real, shipped example of exactly this). The nullability pass in
  `generate_column_values` only ever looked at a column's own `settings`, with no awareness of
  table-level composite-key membership, so ~14–20% of rows in such a column came back `null` —
  which, since the column is also almost always an FK in this pattern, looked exactly like a
  dangling/orphaned reference, and was confirmed to actually fail the generated
  `unique_combination_stg_post_tags_post_id_tag_id` dbt test outright (`FAIL 4`) on a real seed/run/
  build once enough rows made a null/null collision likely. Found while stress-testing bug 1's fix
  above across many more seeds and row counts than its original single repro case. Fixed with a new
  `force_not_null` parameter threaded from `generate_data_from_dbml`/`_resolve_self_referencing_fks`
  (which compute the table's composite-*pk* column set) down into `generate_column_values`,
  overriding the nullability pass for exactly those columns. A composite *unique* (non-pk) key
  deliberately keeps its previous, more permissive behavior — standard SQL unique constraints don't
  forbid nulls in their member columns, unlike primary keys.

- **Uniqueness that the generator gave up on was accepted silently.** Both de-duplication passes
  are deliberately bounded (retry a collision N times, then move on rather than loop forever on a
  value space that's too small) — but when that budget ran out, the duplicate was kept with no
  signal of any kind. The generated project then failed its *own* `unique` /
  `unique_combination_*` dbt test, leaving the user to reverse-engineer why from a red
  `dbt build`. Both paths now record what they couldn't resolve — `_deduplicate_composite_keys`
  for composite keys, `_deduplicate` for single-column `unique`/`pk` columns — and the CLI reports
  each one in its summary, naming the table/column and the number of duplicates left, with the two
  actionable fixes (lower `--rows`, or widen the key's value space). Verified end-to-end: a
  deliberately saturated schema now warns about exactly the three tests that subsequently fail in
  a real `dbt build`, and a healthy schema stays silent. The bounded retry itself is unchanged —
  it's the silence that was the bug.

### Changed
- Rewrote the README's "Roadmap" section as "Project status": as of 1.0.0, model2data is
  considered feature-complete for its intended use case, with no active roadmap of new
  capabilities — see the README for what was deliberately left out of scope for anyone
  interested in contributing it.
- Updated the README's "Limitations" section, which had gone stale relative to the DBML fidelity
  work landed across 0.4.x/0.5.x (composite keys, self-references, both `Ref` syntaxes, parse
  warnings, etc.).

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
