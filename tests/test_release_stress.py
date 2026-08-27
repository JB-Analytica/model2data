"""
Depth-focused stress/regression coverage for the four bugs fixed in the
"final 1.0.0 scrutiny pass" (see CHANGELOG.md / model2data/generate/core.py
and model2data/generate/faker.py):

  1. composite-key dedup could break FK integrity on a collision retry
  2. an enum column whose name contained "int" as a substring crashed
     dtype coercion
  3. nullable FK columns could never actually come back null
  4. non-pk `[unique]` columns had no real uniqueness guarantee

Each single-repro regression test for these already lives in
tests/test_generation.py. This module instead hammers the same code paths
across many seeds, several row counts, and (where relevant) real example
schemas shipped in examples/, to build confidence the fixes are robust
rather than just patched for the one repro case each was found with.

These are Python-level invariant checks only. A separate, manual real-dbt
verification pass (fresh venv wheel install -> generate -> dbt seed/run/
build) was performed for a sample of these (seed, rows) combinations for
the 1.0.0 release; see CHANGELOG.md.
"""

from functools import lru_cache
from pathlib import Path

import pytest

import model2data.generate.core as core
from model2data.generate.core import generate_data_from_dbml
from model2data.generate.relationships import build_fk_lookup, classify_refs
from model2data.parse.dbml import ColumnDef, TableDef, parse_dbml

SEEDS = list(range(1, 21))  # 20 seeds


@lru_cache(maxsize=None)
def _load(dbml_relpath: str):
    # Parsing is read-only with respect to the returned TableDef/ColumnDef
    # objects (generation never mutates them), so it's safe to cache and
    # reuse across every (seed, rows) case below instead of re-parsing the
    # same small file hundreds of times.
    return parse_dbml(Path(dbml_relpath))


def _composite_key_invariant_failures(tables, refs, data) -> list[str]:
    """
    For every pk/unique composite key in `tables`, check:
      (a) the key's column combination is unique across all generated rows
          for keys of type "pk" (a composite *unique* key, like a plain
          single-column `unique`, doesn't forbid null members in standard
          SQL, so this repo's own dedup intentionally doesn't force full
          uniqueness there when nulls are involved -- see
          core._deduplicate_composite_keys).
      (b) every non-null value in a key column that's also an FK is a
          real value from the parent table's actual generated key column
          (no dangling/orphaned FK references).
      (c) a composite *pk* member column is never null (SQL primary-key
          semantics forbid it, regardless of whether the column carries
          `not null`/`pk` in its own individual DBML settings).
    Returns a list of human-readable failure descriptions (empty if none).
    """
    fk_refs, _ = classify_refs(tables, refs)
    fk_lookup = build_fk_lookup(fk_refs)

    failures = []
    for table_name, table_def in tables.items():
        df = data[table_name]
        for key in table_def.composite_keys:
            key_type = key.get("type")
            if key_type not in ("pk", "unique"):
                continue
            key_columns = key.get("columns") or []
            if not key_columns or any(c not in df.columns for c in key_columns):
                continue

            if key_type == "pk":
                for col in key_columns:
                    if df[col].isna().any():
                        failures.append(f"{table_name}.{col}: composite PK member has null values")
                combos = list(zip(*(df[c] for c in key_columns), strict=True))
                if len(combos) != len(set(combos)):
                    failures.append(
                        f"{table_name}: composite pk {key_columns} has duplicate "
                        f"combinations ({len(combos)} rows, {len(set(combos))} unique)"
                    )

            for col in key_columns:
                fk_target = fk_lookup.get((table_name, col))
                if not fk_target:
                    continue
                parent_table, parent_column = fk_target
                parent_ids = set(data[parent_table][parent_column].dropna())
                child_values = set(df[col].dropna())
                if not child_values <= parent_ids:
                    failures.append(
                        f"{table_name}.{col}: dangling FK values not in "
                        f"{parent_table}.{parent_column} "
                        f"({child_values - parent_ids})"
                    )

    return failures


# ---------------------------------------------------------------------
# Bug 1: composite-key dedup must resample FKs from the real parent id
# pool on a collision retry, not regenerate them via the column's own
# type-based generator.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("rows", [15, 150, 1500])
@pytest.mark.parametrize("seed", SEEDS)
def test_tagging_m2m_composite_key_invariants_hold(seed, rows):
    tables, refs = _load("examples/tagging_m2m.dbml")
    data = generate_data_from_dbml(tables, refs, base_rows=rows, seed=seed)
    failures = _composite_key_invariant_failures(tables, refs, data)
    assert not failures, f"seed={seed} rows={rows}: {failures}"


@pytest.mark.parametrize("rows", [15, 150, 1500])
@pytest.mark.parametrize("seed", SEEDS)
def test_advanced_features_composite_key_invariants_hold(seed, rows):
    tables, refs = _load("examples/advanced_features.dbml")
    data = generate_data_from_dbml(tables, refs, base_rows=rows, seed=seed)
    failures = _composite_key_invariant_failures(tables, refs, data)
    assert not failures, f"seed={seed} rows={rows}: {failures}"


def _bridge_schema():
    tables = {
        "posts": TableDef(name="posts", columns=[ColumnDef("id", "int", {"pk"})]),
        "tags": TableDef(name="tags", columns=[ColumnDef("id", "int", {"pk"})]),
        "post_tags": TableDef(
            name="post_tags",
            columns=[
                ColumnDef("post_id", "int", {"not null"}),
                ColumnDef("tag_id", "int", {"not null"}),
            ],
            composite_keys=[{"columns": ["post_id", "tag_id"], "type": "pk"}],
        ),
    }
    refs = [
        {
            "source_table": "post_tags",
            "source_column": "post_id",
            "target_table": "posts",
            "target_column": "id",
        },
        {
            "source_table": "post_tags",
            "source_column": "tag_id",
            "target_table": "tags",
            "target_column": "id",
        },
    ]
    return tables, refs


@pytest.mark.parametrize(
    "parent_rows,bridge_rows,uniqueness_achievable",
    [
        # value space = parent_rows**2. Full row-level uniqueness needs
        # real headroom below that space to be reliably achievable with
        # this algorithm's *bounded* (max_attempts=20) per-row retry: as
        # the space fills up, hitting one of the last few remaining
        # combos becomes a coupon-collector problem, and occasionally
        # needs more than 20 random draws even well before 100% full (this
        # was verified empirically -- ratios above ~60% started flaking
        # even across a handful of seeds). That's a pre-existing, already
        # documented tradeoff ("give up gracefully on a tiny value space
        # rather than looping forever"), not something bug 1's fix
        # changed or needs to guarantee. FK-validity and no-null,
        # however, are the actual bug-1/composite-pk-nullability
        # invariants, and must hold unconditionally regardless of how
        # saturated the value space is -- checked below for every case.
        (5, 8, True),  # 25 combos, 32% full
        (5, 12, True),  # 48% full
        (5, 25, False),  # 100% full -- not reliably achievable
        (5, 60, False),  # over capacity
        (5, 300, False),  # far over capacity
        (20, 200, True),  # 400 combos, 50% full
        (20, 240, True),  # 60% full
        (20, 400, False),  # 100% full -- not reliably achievable
        (20, 2000, False),  # far over capacity
    ],
)
@pytest.mark.parametrize("seed", list(range(1, 16)))  # 15 seeds
def test_composite_key_dedup_extreme_value_space_stress(
    seed, parent_rows, bridge_rows, uniqueness_achievable, monkeypatch
):
    # Extends the original bug-1 regression test
    # (test_composite_key_dedup_retry_preserves_fk_validity in
    # test_generation.py, which used one fixed (seed=123, 5-row parents,
    # 200-row bridge) case) into a real sweep: many seeds, several
    # parent/bridge row-count ratios, including ones where the parent
    # value space is *smaller* than the bridge row count -- exactly the
    # value-space-vs-row-count relationship the original bug needed to
    # surface at all.
    tables, refs = _bridge_schema()

    def sized_row_counts(table_name, base_rows):
        return parent_rows if table_name in ("posts", "tags") else base_rows

    monkeypatch.setattr(core, "_determine_row_count", sized_row_counts)

    data = generate_data_from_dbml(tables, refs, base_rows=bridge_rows, seed=seed)

    post_ids = set(data["posts"]["id"].tolist())
    tag_ids = set(data["tags"]["id"].tolist())
    post_tags = data["post_tags"]

    # FK-validity must hold unconditionally: this is exactly what bug 1
    # broke (a dedup retry regenerating a colliding FK column via its own
    # type-based generator instead of resampling from the real parent
    # pool, silently producing a value that references no real row).
    assert set(post_tags["post_id"].tolist()) <= post_ids, (
        f"seed={seed} parent_rows={parent_rows} bridge_rows={bridge_rows}: "
        "dangling post_id FK values"
    )
    assert set(post_tags["tag_id"].tolist()) <= tag_ids, (
        f"seed={seed} parent_rows={parent_rows} bridge_rows={bridge_rows}: "
        "dangling tag_id FK values"
    )
    # Composite PK members must never be null (the adjacent fix made
    # alongside bug 1's stress testing in this same round).
    assert not post_tags["post_id"].isna().any()
    assert not post_tags["tag_id"].isna().any()

    if uniqueness_achievable:
        combos = list(zip(post_tags["post_id"], post_tags["tag_id"], strict=True))
        assert len(combos) == len(set(combos)), (
            f"seed={seed} parent_rows={parent_rows} bridge_rows={bridge_rows}: "
            f"duplicate composite-key combos despite an achievable value space"
        )


# ---------------------------------------------------------------------
# Bug 3: a nullable FK column (self-referencing or not) must actually be
# able to come back null, while a not-null FK column must never be null,
# and every non-null FK value (nullable or not) must still be valid.
# ---------------------------------------------------------------------


@pytest.mark.parametrize("rows", [500, 1000])
@pytest.mark.parametrize("seed", SEEDS)
def test_nullable_and_not_null_fk_invariants_advanced_features(seed, rows):
    tables, refs = _load("examples/advanced_features.dbml")
    data = generate_data_from_dbml(tables, refs, base_rows=rows, seed=seed)

    employees = data["employees"]
    expenses = data["expenses"]

    # employees.department_id is `[not null]`: must never be null, and
    # every value must be a real department id.
    assert not employees["department_id"].isna().any(), f"seed={seed} rows={rows}"
    assert employees["department_id"].isin(data["departments"]["id"]).all()

    # employees.manager_id is a nullable *self-referencing* FK: at this
    # row count, if nullability were broken again (bug 3's early-return),
    # this would show exactly zero nulls, not just "fewer than expected".
    assert employees["manager_id"].isna().any(), f"seed={seed} rows={rows}: no nulls at all"
    assert employees["manager_id"].dropna().isin(employees["id"]).all()

    # expenses.approved_by is a nullable *ordinary* (non-self) FK.
    assert expenses["approved_by"].isna().any(), f"seed={seed} rows={rows}: no nulls at all"
    assert expenses["approved_by"].dropna().isin(employees["id"]).all()


# ---------------------------------------------------------------------
# Bug 4: a non-pk `[unique]` column must actually be deduplicated, across
# every code path that can produce one (typed numeric with an explicit
# range, a name-inferred pattern like email, a literal Faker provider
# fallback like ean13, and UUIDs), and an explicit narrow range must
# never be silently widened past what was declared.
# ---------------------------------------------------------------------


def _int_unique_table(min_val: int, max_val: int) -> dict[str, TableDef]:
    return {
        "widgets": TableDef(
            name="widgets",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef(
                    "code", "int", {"unique", "not null"}, note={"min": min_val, "max": max_val}
                ),
            ],
        )
    }


@pytest.mark.parametrize("rows", [10, 100, 1000, 2000])
@pytest.mark.parametrize("seed", SEEDS)
def test_unique_int_column_tight_but_sufficient_range_never_duplicates(seed, rows):
    # Range sized to comfortably exceed row_count (hitting the
    # random.sample guaranteed-unique path) while still being narrow
    # relative to the row count -- the regime `ensure_unique` needs to
    # keep handling correctly now that it's passed for non-pk `[unique]`
    # columns too, not just `pk`.
    tables = _int_unique_table(0, rows * 2)
    df = generate_data_from_dbml(tables, [], base_rows=rows, seed=seed)["widgets"]
    codes = df["code"].tolist()
    assert len(codes) == len(set(codes)), f"seed={seed} rows={rows}"
    assert all(0 <= c <= rows * 2 for c in codes)


@pytest.mark.parametrize("seed", SEEDS)
def test_unique_int_column_explicit_narrow_range_is_not_silently_widened(seed):
    # Explicit range (30 possible values) smaller than row_count (200):
    # full uniqueness genuinely isn't achievable on a value space this
    # tight, so generation falls back to bounded-retry dedup instead --
    # but the declared range must never be silently widened past
    # [min, max] to work around that, for pk *or* non-pk unique columns.
    tables = _int_unique_table(0, 29)
    df = generate_data_from_dbml(tables, [], base_rows=200, seed=seed)["widgets"]
    codes = df["code"].tolist()
    assert all(0 <= c <= 29 for c in codes), f"seed={seed}: range was widened past [0, 29]"


@pytest.mark.parametrize("rows", [10, 100, 1000, 2000])
@pytest.mark.parametrize("seed", SEEDS)
def test_ecommerce_unique_columns_never_duplicate(seed, rows):
    tables, refs = _load("examples/ecommerce.dbml")
    data = generate_data_from_dbml(tables, refs, base_rows=rows, seed=seed)

    # customers.email [unique, not null]: name-inference code path.
    emails = data["customers"]["email"].tolist()
    assert len(emails) == len(set(emails)), f"seed={seed} rows={rows}: duplicate emails"

    # products.sku [unique, not null], type "ean13": literal Faker
    # provider fallback code path (not a recognized name pattern, not a
    # structured numeric/date/uuid type).
    skus = data["products"]["sku"].tolist()
    assert len(skus) == len(set(skus)), f"seed={seed} rows={rows}: duplicate skus"


@pytest.mark.parametrize("seed", SEEDS)
def test_unique_uuid_column_never_duplicates(seed):
    tables = {
        "sessions": TableDef(
            name="sessions",
            columns=[
                ColumnDef("id", "int", {"pk"}),
                ColumnDef("token", "uuid", {"unique", "not null"}),
            ],
        )
    }
    df = generate_data_from_dbml(tables, [], base_rows=500, seed=seed)["sessions"]
    tokens = df["token"].tolist()
    assert len(tokens) == len(set(tokens)), f"seed={seed}: duplicate uuid tokens"
