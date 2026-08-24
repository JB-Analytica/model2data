# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
