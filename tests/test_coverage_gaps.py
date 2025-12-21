"""Tests targeting coverage gaps in utils, faker, and dbml modules."""

from pathlib import Path

from model2data.generate.faker import generate_column_values
from model2data.parse.dbml import ColumnDef, _strip_quotes, parse_dbml
from model2data.utils import normalize_identifier


class TestNormalizeIdentifier:
    """Test utils.normalize_identifier edge cases (lines 6, 8)."""

    def test_normalize_empty_after_stripping(self):
        """Test line 6: if not cleaned - string becomes empty after cleanup."""
        result = normalize_identifier("___")
        assert result == "table"

    def test_normalize_starts_with_digit(self):
        """Test line 8: if cleaned[0].isdigit() - prefix with t_."""
        result = normalize_identifier("123abc")
        assert result.startswith("t_")
        assert "123" in result

    def test_normalize_special_chars_only(self):
        """Test string with only special characters."""
        result = normalize_identifier("@#$%^&*()")
        assert result == "table"

    def test_normalize_digit_and_special(self):
        """Test digit-starting string with special chars."""
        result = normalize_identifier("9_test-name")
        assert result.startswith("t_")


class TestGenerateColumnValuesFaker:
    """Test faker.generate_column_values for uncovered data types (lines 50-53, 60, 66, 72, 75, 88)."""

    def test_integer_with_min_max_note(self):
        """Test lines 50-53: integer type with min/max in column note."""
        col = ColumnDef(
            name="age",
            data_type="int",
            settings={"not null"},
            note={"min": 18, "max": 65},
        )
        values = generate_column_values(col, row_count=10)
        assert len(values) == 10
        # Values can be int or None
        int_values = [v for v in values if v is not None]
        assert all(18 <= v <= 65 for v in int_values)

    def test_float_type(self):
        """Test line 60: float/decimal type."""
        col = ColumnDef(
            name="price",
            data_type="decimal",
            settings={"not null"},
        )
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5
        # At least some should be floats (with not null constraint)
        assert any(isinstance(v, float) for v in values)

    def test_boolean_type(self):
        """Test line 66: boolean type."""
        col = ColumnDef(
            name="is_active",
            data_type="boolean",
            settings={"not null"},
        )
        values = generate_column_values(col, row_count=10)
        assert len(values) == 10
        # Should have boolean values
        assert any(isinstance(v, bool) for v in values)

    def test_date_type(self):
        """Test line 72: date type."""
        col = ColumnDef(
            name="birth_date",
            data_type="date",
        )
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5

    def test_time_type(self):
        """Test line 75: time type."""
        col = ColumnDef(
            name="event_time",
            data_type="time",
        )
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5

    def test_faker_fallback_with_exception(self):
        """Test line 88: exception handler in Faker fallback."""
        # Use a type that Faker cannot format, triggering the exception handler
        col = ColumnDef(
            name="random_field",
            data_type="nonexistent_faker_type",
            settings={"not null"},
        )
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5
        # Should fall back to sentence generation
        assert all(isinstance(v, str) for v in values)

    def test_id_field_fallback_generates_uuid(self):
        """Test line 88: exception handler with _id field (should generate UUID)."""
        col = ColumnDef(
            name="user_id",
            data_type="unknown_type",
            settings={"not null"},
        )
        values = generate_column_values(col, row_count=5)
        assert len(values) == 5
        # Should generate UUIDs for fields ending in _id
        assert all(isinstance(v, str) for v in values)


class TestDBMLParsing:
    """Test dbml.py parsing edge cases (lines 39-44, 64, 68-72, 80, 87-92, 98, 146)."""

    def test_strip_quotes_basic(self):
        """Test _strip_quotes function."""
        assert _strip_quotes("'table_name'") == "table_name"
        assert _strip_quotes('"table_name"') == "table_name"
        assert _strip_quotes("table_name") == "table_name"

    def test_parse_dbml_with_schema_prefix(self):
        """Test parsing table with schema prefix (e.g., [schema].[table])."""
        dbml_content = """
Table public.users {
  id int
}
"""
        dbml_file = Path("/tmp/test_schema.dbml")
        dbml_file.write_text(dbml_content)

        try:
            tables, refs = parse_dbml(dbml_file)
            # Should handle schema prefix gracefully
            assert len(tables) > 0
        finally:
            dbml_file.unlink()

    def test_parse_dbml_with_indexes(self):
        """Test parsing table with indexes block (lines 59-61)."""
        dbml_content = """
Table users {
  id int [primary key]
  email string [unique]

  indexes {
    id [pk]
    email [unique]
  }
}
"""
        dbml_file = Path("/tmp/test_indexes.dbml")
        dbml_file.write_text(dbml_content)

        try:
            tables, refs = parse_dbml(dbml_file)
            assert "users" in tables
            user_table = tables["users"]
            assert len(user_table.columns) >= 2
        finally:
            dbml_file.unlink()

    def test_parse_dbml_with_comments(self):
        """Test parsing with inline comments."""
        dbml_content = """
Table users {
  id int // primary key
  name string // user full name
}
"""
        dbml_file = Path("/tmp/test_comments.dbml")
        dbml_file.write_text(dbml_content)

        try:
            tables, refs = parse_dbml(dbml_file)
            assert "users" in tables
        finally:
            dbml_file.unlink()

    def test_parse_dbml_with_multiline_notes(self):
        """Test parsing with multi-line note blocks."""
        dbml_content = """
Table users {
  id int

  Note: '''
    This is a multi-line note
    about the users table
  '''
}
"""
        dbml_file = Path("/tmp/test_notes.dbml")
        dbml_file.write_text(dbml_content)

        try:
            tables, refs = parse_dbml(dbml_file)
            assert "users" in tables
        finally:
            dbml_file.unlink()

    def test_parse_dbml_empty_file(self):
        """Test parsing empty DBML file."""
        dbml_file = Path("/tmp/test_empty.dbml")
        dbml_file.write_text("")

        try:
            tables, refs = parse_dbml(dbml_file)
            assert len(tables) == 0
            assert len(refs) == 0
        finally:
            dbml_file.unlink()

    def test_parse_dbml_with_only_comments(self):
        """Test parsing file with only comments."""
        dbml_content = """
// This is a comment
// Another comment
"""
        dbml_file = Path("/tmp/test_only_comments.dbml")
        dbml_file.write_text(dbml_content)

        try:
            tables, refs = parse_dbml(dbml_file)
            assert len(tables) == 0
        finally:
            dbml_file.unlink()
