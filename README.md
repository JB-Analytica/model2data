# model2data

[![PyPI](https://img.shields.io/pypi/v/model2data)](https://pypi.org/project/model2data/)
[![CI](https://github.com/JB-Analytica/model2data/actions/workflows/ci.yml/badge.svg)](https://github.com/JB-Analytica/model2data/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/JB-Analytica/model2data/branch/main/graph/badge.svg)](https://codecov.io/gh/JB-Analytica/model2data)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

`model2data` turns **data models into analytics-ready datasets** in seconds.

Given a **DBML file**, it generates synthetic but realistic data, a complete dbt project scaffold, and everything you need to start analyzing or testing data pipelines.

---

## What problem does it solve?

Building analytics or testing dbt pipelines often requires realistic data, but using real data raises privacy concerns, and creating mock data manually is time-consuming. `model2data` automates this by generating synthetic datasets from your data model definitions, ensuring privacy-safe, deterministic, and relationship-preserving data for development and testing.

---

## How it works (high level)

1. **Parse DBML**: Reads your database schema from a DBML file, extracting tables, columns, types, and relationships.
2. **Generate Data**: Uses Faker and custom logic to create realistic synthetic data, respecting foreign keys and constraints.
3. **Scaffold dbt Project**: Creates a dbt project with seeds (CSV files), staging models, profiles, and tests, ready to run with DuckDB.

---

## Installation

```bash
pip install model2data
```

---

## Quick start

We provide an example Hacker News dataset in `examples/hackernews.dbml`.

Generate a project with synthetic data:

```bash
model2data generate --file examples/hackernews.dbml --rows 200 --seed 42
```

This creates a `dbt_hackernews/` folder with your data and dbt setup.

Run dbt to load and transform the data:

```bash
cd dbt_hackernews
dbt deps
dbt seed
dbt run
```

Your analytics-ready dataset is now in DuckDB!

---

## Generated dbt project structure

The generated dbt project includes:

```
dbt_{project_name}/
├── seeds/
│   └── {project_name}/
│       ├── table1.csv
│       └── table2.csv
├── models/
│   └── {project_name}/
│       └── staging/
│           ├── __sources.yml
│           ├── stg_table1.sql
│           ├── stg_table1.yml
│           └── ...
├── macros/
│   └── generate_schema_name.sql
├── dbt_project.yml
├── profiles.yml  # DuckDB config
└── {project_name}.duckdb
```

- **Seeds**: CSV files with generated synthetic data.
- **Staging Models**: Basic dbt models that load from seeds.
- **Sources & Tests**: YAML configs defining sources and basic tests (not_null, unique).
- **Profiles**: Pre-configured for DuckDB with schema handling.

---

## Design decisions / non-goals

- **DuckDB Default**: Chosen for its zero-config, file-based nature, making it easy to get started without database setup. Other adapters can be configured manually.
- **dbt Integration**: Leverages dbt's transformation capabilities for a familiar workflow in analytics engineering.
- **Synthetic Data**: Uses deterministic generation for reproducibility; not intended for production use or as a replacement for real data.
- **Non-goals**: This is not a data migration tool, ETL pipeline, or real-time data generator. It focuses on static, synthetic datasets for testing and prototyping.

---

## Limitations

- Supports basic DBML features; complex constraints or advanced SQL types may not be fully handled.
- Synthetic data generation is heuristic-based and may not perfectly mimic real-world distributions or edge cases.
- Currently optimized for DuckDB; other databases require manual profile adjustments.
- No support for incremental models or advanced dbt features in generated projects.

---

## Roadmap

- Support for additional database adapters (e.g., Snowflake, BigQuery).
- Enhanced data type handling and custom generators.
- Integration with more dbt features like incremental models.
- Web-based DBML editor and data preview.

---

## Contributing

We welcome contributions!

- Open issues for bugs or feature requests.
- Submit PRs to add new DBML examples, custom data generators, or improvements.
- Ensure all new features include tests if possible.

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) to understand our community standards.

---

## License

MIT License. See LICENSE for details.
