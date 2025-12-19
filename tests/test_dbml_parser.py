from pathlib import Path

import pytest

from model2data.parse.dbml import (
    _parse_column_settings,
    _strip_quotes,
    normalize_identifier,
    parse_dbml,
)


def test_parse_hackernews_dbml():
    dbml_path = Path("examples/hackernews.dbml")
    tables, refs = parse_dbml(dbml_path)

    # Tables exist
    assert "stories" in tables
    assert "stories__kids" in tables

    stories = tables["stories"]
    column_names = {c.name for c in stories.columns}

    # Key columns
    assert "id" in column_names
    assert "_dlt_id" in column_names

    # Refs exist
    assert len(refs) > 0

    # FK reference example
    assert any(
        r["source_table"] == "stories__kids" and r["target_table"] == "stories" for r in refs
    )


def test_table_parsing():
    """Test that tables are correctly parsed with various formats."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # Verify tables were parsed
    assert len(tables) > 0
    assert isinstance(tables, dict)

    # Each table should have a name and columns list
    for table_name, table_def in tables.items():
        assert table_def.name == table_name
        assert isinstance(table_def.columns, list)


def test_column_parsing():
    """Test that columns are correctly parsed with names, types, and settings."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    stories = tables["stories"]

    # Verify columns exist
    assert len(stories.columns) > 0

    # Check column structure
    for col in stories.columns:
        assert hasattr(col, "name")
        assert hasattr(col, "data_type")
        assert hasattr(col, "settings")
        assert isinstance(col.settings, set)
        assert col.name  # Name should not be empty


def test_column_settings_parsing():
    """Test that column settings like pk, not null, etc. are parsed."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # Find a column with settings (typically id columns have pk)
    found_pk = False
    for table in tables.values():
        for col in table.columns:
            if "pk" in col.settings:
                found_pk = True
                # PK settings should be lowercase
                assert all(s.islower() or s.replace("_", "").islower() for s in col.settings)
                break
        if found_pk:
            break

    # Should find at least one primary key
    assert found_pk, "Should find at least one primary key column"


def test_reference_parsing():
    """Test that references/foreign keys are correctly parsed."""
    dbml_path = Path("examples/hackernews.dbml")
    _, refs = parse_dbml(dbml_path)

    assert len(refs) > 0

    # Each ref should have required fields
    for ref in refs:
        assert "source_table" in ref
        assert "source_column" in ref
        assert "target_table" in ref
        assert "target_column" in ref

        # Values should not be empty
        assert ref["source_table"]
        assert ref["source_column"]
        assert ref["target_table"]
        assert ref["target_column"]


def test_reference_direction():
    """Test that reference direction (> vs <) is handled correctly."""
    dbml_path = Path("examples/hackernews.dbml")
    _, refs = parse_dbml(dbml_path)

    # Find the stories__kids -> stories reference
    kids_ref = [
        r for r in refs if r["source_table"] == "stories__kids" and r["target_table"] == "stories"
    ]

    assert len(kids_ref) > 0, "Should find stories__kids reference"

    # Verify the foreign key points from child to parent
    ref = kids_ref[0]
    assert ref["source_table"] == "stories__kids"
    assert ref["target_table"] == "stories"


def test_comments_ignored():
    """Test that comments (//) are properly ignored during parsing."""
    # This is implicit in the parsing - if comments weren't ignored,
    # parsing would fail or produce incorrect results
    dbml_path = Path("examples/hackernews.dbml")
    tables, refs = parse_dbml(dbml_path)

    # Should parse successfully without comment content interfering
    assert len(tables) > 0


def test_note_blocks_ignored():
    """Test that Note blocks with triple quotes are properly ignored."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # Notes should not create columns or affect parsing
    # Verify that parsed columns don't contain note text
    for table in tables.values():
        for col in table.columns:
            # Column names shouldn't contain "Note" or triple quotes
            assert "'''" not in col.name
            assert not col.name.startswith("Note:")


def test_indexes_blocks_ignored():
    """Test that indexes blocks are properly ignored."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # Indexes shouldn't create columns
    for table in tables.values():
        for col in table.columns:
            # Column names shouldn't look like index definitions
            assert not col.name.startswith("(")
            assert "indexes" not in col.name.lower()


def test_strip_quotes_helper():
    """Test the _strip_quotes helper function."""
    assert _strip_quotes('"table_name"') == "table_name"
    assert _strip_quotes("'table_name'") == "table_name"
    assert _strip_quotes('  "table_name"  ') == "table_name"
    assert _strip_quotes("table_name") == "table_name"
    assert _strip_quotes("  table_name  ") == "table_name"


def test_parse_column_settings_helper():
    """Test the _parse_column_settings helper function."""
    # Single setting
    settings = _parse_column_settings("pk")
    assert "pk" in settings

    # Multiple settings
    settings = _parse_column_settings("pk, not null")
    assert "pk" in settings
    assert "not null" in settings

    # With quotes
    settings = _parse_column_settings("'pk', 'not null'")
    assert "pk" in settings
    assert "not null" in settings

    # Empty/None
    assert _parse_column_settings(None) == set()
    assert _parse_column_settings("") == set()

    # Settings should be lowercase
    settings = _parse_column_settings("PK, NOT NULL")
    assert "pk" in settings
    assert "not null" in settings


def test_normalize_identifier_helper():
    """Test the normalize_identifier helper function."""
    # Basic normalization
    assert normalize_identifier("Table Name") == "table_name"
    assert normalize_identifier("table-name") == "table_name"
    assert normalize_identifier("table.name") == "table_name"

    # Multiple special characters
    assert normalize_identifier("table::name!!123") == "table_name_123"

    # Leading/trailing underscores removed
    assert normalize_identifier("_table_name_") == "table_name"

    # Starts with digit - should prefix with t_
    assert normalize_identifier("123_table") == "t_123_table"

    # Empty or all special chars
    assert normalize_identifier("!!!") == "table"
    assert normalize_identifier("") == "table"

    # Lowercase conversion
    assert normalize_identifier("TableName") == "tablename"


def test_quoted_identifiers():
    """Test that quoted table and column names are handled correctly."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, refs = parse_dbml(dbml_path)

    # After parsing, quotes should be stripped from names
    for table_name in tables.keys():
        assert '"' not in table_name
        assert "'" not in table_name
        assert "`" not in table_name

    for ref in refs:
        assert '"' not in ref["source_table"]
        assert '"' not in ref["target_table"]
        assert '"' not in ref["source_column"]
        assert '"' not in ref["target_column"]


def test_ref_block_parsing():
    """Test that Ref blocks (multi-line reference definitions) are parsed."""
    dbml_path = Path("examples/hackernews.dbml")
    _, refs = parse_dbml(dbml_path)

    # Should parse references regardless of whether they're inline or in Ref blocks
    assert len(refs) > 0


def test_multiple_tables():
    """Test that multiple tables are correctly parsed."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # Should have multiple tables
    assert len(tables) >= 2

    # Verify stories and stories__kids exist
    assert "stories" in tables
    assert "stories__kids" in tables


def test_column_data_types():
    """Test that column data types are preserved correctly."""
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    stories = tables["stories"]

    # Should have various data types
    data_types = {col.data_type for col in stories.columns}
    assert len(data_types) > 0

    # Data types should not be empty
    for col in stories.columns:
        assert col.data_type.strip()


def test_empty_or_missing_file():
    """Test handling of non-existent files."""
    with pytest.raises(FileNotFoundError):
        parse_dbml(Path("nonexistent.dbml"))


def test_table_without_columns():
    """Test that tables can be parsed even if they have no columns initially."""
    # This is more of a defensive test - the parser should handle edge cases
    dbml_path = Path("examples/hackernews.dbml")
    tables, _ = parse_dbml(dbml_path)

    # All parsed tables should be in the return dict
    for table_name, table_def in tables.items():
        assert table_def.name == table_name
        assert isinstance(table_def.columns, list)
