# Vendored: PyDBML 1.2.1

The DBML reader behind `model2data.model.from_dbml`. Copied from
[PyDBML](https://github.com/Vanderhoof/PyDBML) 1.2.1 (PyPI, released
2025-10-24) under its MIT licence, which is kept beside it in `LICENSE`.

It is vendored rather than depended on so the engine can read the DBML that
dbdiagram and model2data studio accept without waiting on an upstream release:
PyDBML has one maintainer and parts of current DBML it does not parse. Every
change is listed below, one commit each, so each can be offered upstream.

## Changes from 1.2.1

1. `from pydbml` / `import pydbml` rewritten to `model2data._vendor.pydbml`.
   Nothing else in the first commit differs from the release.

## Updating

Copy the new release over this directory, redo change 1, re-apply the patches
below it (each commit names what it changes and has a test in
`tests/test_vendored_pydbml.py`), and run the whole suite: the studio's DBML
conformance corpus in `tests/fixtures/dbml_conformance/` is the check that the
update reads DBML the way model2data studio does.
