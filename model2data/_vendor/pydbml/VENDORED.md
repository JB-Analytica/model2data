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
2. **Number and null literals** (`definitions/generic.py`, `definitions/column.py`):
   numbers take a sign and an exponent (`-5`, `1e3`); `default: null` is no
   default, where 1.2.1 returned the text `NULL` (a parse action returning
   None leaves the token in place).
3. **Names and types** (`definitions/generic.py`, `definitions/column.py`): a
   name is `\w+`, so unquoted non-ASCII names (`café`) read; a type may carry
   arguments and `[]` together (`numeric(10,2)[]`).
4. **A ref's colour** (`definitions/reference.py`): `color: #rrggbb` among a
   ref's settings is read and dropped, where 1.2.1 refused the file.
5. **Check constraints** (`definitions/column.py`, `definitions/table.py`,
   `parser/blueprints.py`): a column's `check: \`expr\`` and a table's
   `checks { \`expr\` [name: '...'] }` parse; the built `Column.checks` holds
   the expressions, `Table.checks` a list of `{expression, name?}`.

## Updating

Copy the new release over this directory, redo change 1, re-apply the patches
below it (each commit names what it changes and has a test in
`tests/test_vendored_pydbml.py`), and run the whole suite: the studio's DBML
conformance corpus in `tests/fixtures/dbml_conformance/` is the check that the
update reads DBML the way model2data studio does.
