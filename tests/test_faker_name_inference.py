"""Tests for column-name-based Faker inference in generate.faker."""

import re

from model2data.generate.faker import (
    _deduplicate,
    generate_column_values,
    get_unmapped_columns,
    reset_stats,
)
from model2data.parse.dbml import ColumnDef

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TestNameInference:
    def test_email_column_gets_real_emails(self):
        col = ColumnDef(name="email", data_type="varchar", settings={"not null"})
        values = generate_column_values(col, row_count=20)
        assert all(EMAIL_RE.match(v) for v in values)

    def test_first_and_last_name_are_distinct_providers(self):
        first = generate_column_values(
            ColumnDef(name="first_name", data_type="varchar", settings={"not null"}),
            row_count=10,
        )
        last = generate_column_values(
            ColumnDef(name="last_name", data_type="varchar", settings={"not null"}),
            row_count=10,
        )
        # Different providers should not consistently produce identical output.
        assert first != last

    def test_generic_product_name_is_not_treated_as_a_person_name(self):
        # "name" alone is intentionally unmapped so "product_name" style
        # columns don't get filled with people's names.
        col = ColumnDef(name="product_name", data_type="varchar", settings={"not null"})
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5
        assert all(isinstance(v, str) for v in values)

    def test_city_and_country_columns(self):
        city_values = generate_column_values(
            ColumnDef(name="city", data_type="varchar", settings={"not null"}), row_count=5
        )
        country_values = generate_column_values(
            ColumnDef(name="country", data_type="varchar", settings={"not null"}), row_count=5
        )
        assert all(isinstance(v, str) and v for v in city_values)
        assert all(isinstance(v, str) and v for v in country_values)

    def test_unmatched_type_and_name_is_tracked_in_stats(self):
        reset_stats()
        col = ColumnDef(name="misc_notes_field", data_type="weird_custom_type")
        generate_column_values(col, row_count=3)
        unmapped = get_unmapped_columns()
        assert ("misc_notes_field", "weird_custom_type") in unmapped

    def test_matched_name_is_not_tracked_as_unmapped(self):
        reset_stats()
        col = ColumnDef(name="email", data_type="varchar", settings={"not null"})
        generate_column_values(col, row_count=3)
        assert get_unmapped_columns() == []

    def test_ensure_unique_deduplicates_name_inferred_values(self):
        col = ColumnDef(name="email", data_type="varchar", settings={"not null", "pk"})
        values = generate_column_values(col, row_count=50, ensure_unique=True)
        assert len(values) == len(set(values)) == 50

    def test_deduplicate_retries_on_collision(self):
        # Value space of 2 with 3 requested values forces at least one retry.
        pool = iter(["a", "a", "b", "c", "a"])
        result = _deduplicate(["a", "a", "b"], generator=lambda: next(pool))
        assert len(result) == len(set(result)) == 3

    def test_deduplicate_gives_up_after_max_attempts_on_tiny_value_space(self):
        # Only one possible value: dedup can't succeed, but must terminate.
        result = _deduplicate(["x", "x", "x"], generator=lambda: "x", max_attempts=3)
        assert result == ["x", "x", "x"]
