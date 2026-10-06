# Validate models in CI

Teams that keep `*.model2data.yml` models in git can fail a pull request that breaks one.
`model2data validate` takes several files and `--glob` patterns, prefixes each issue with its
file, exits 1 if any model has an error, and with `--format github` prints annotations that show
inline on the pull request's Files tab. `--require-files` fails when nothing matched.

**GitHub Actions**

```yaml
name: model2data
on:
  pull_request:
    paths: ["**/*.model2data.yml"]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: JB-Analytica/model2data@v1
```

Inputs: `files` (glob, default `**/*.model2data.yml`), `version` (default: the release the tag
points at, e.g. `1.13.0`; a specifier such as `>=1.10,<2` also works), `python-version` (3.12).

**pre-commit**

```yaml
repos:
  - repo: https://github.com/JB-Analytica/model2data
    rev: v1.13.0
    hooks:
      - id: model2data-validate
```

**GitLab CI**

```yaml
model2data:
  image: python:3.12-slim
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes: ["**/*.model2data.yml"]
  script:
    - pip install model2data==1.13.0
    - model2data validate --glob "**/*.model2data.yml"
```
