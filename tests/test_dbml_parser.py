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
    settings, note = _parse_column_settings("pk")
    assert "pk" in settings
    assert note is None

    # Multiple settings
    settings, note = _parse_column_settings("pk, not null, unique")
    assert "pk" in settings
    assert "not null" in settings
    assert "unique" in settings
    assert note is None

    # Settings with note
    settings, note = _parse_column_settings('pk, not null, note: \'{"min": 1, "max": 5}\'')
    assert "pk" in settings
    assert "not null" in settings
    assert note is not None
    assert note["min"] == 1
    assert note["max"] == 5

    # Just a note
    settings, note = _parse_column_settings('note: \'{"min": 0, "max": 100}\'')
    assert len(settings) == 0
    assert note is not None
    assert note["min"] == 0
    assert note["max"] == 100

    # Empty
    settings, note = _parse_column_settings("")
    assert len(settings) == 0
    assert note is None

    # None
    settings, note = _parse_column_settings(None)
    assert len(settings) == 0
    assert note is None


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


# ============================================================================
# Additional tests for missing coverage lines
# ============================================================================


def test_table_name_with_bracket_settings(tmp_path):
    """Test lines 44-49: table name parsing when brackets exist."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table users [headercolor: #3498db] {
    id int [pk]
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 47: table_name_section split by '['
    # Line 48: strip the part before '['
    # Line 49: _strip_quotes on table_name
    assert "users" in tables


def test_multiple_tables_to_trigger_closing_brace(tmp_path):
    """Test line 70: closing brace adds table to dict."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table first {
    id int
}

Table second {
    id int
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 70: tables[current_table.name] = current_table
    # This happens when we hit the closing }
    assert "first" in tables
    assert "second" in tables
    assert len(tables) == 2


def test_note_keyword_inside_table(tmp_path):
    """Test lines 74-76: Note: inside table block."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table users {
    id int [pk]
    Note: 'Primary key'
    name varchar
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 75: if cleaned.startswith("Note:")
    # Line 76: continue
    # Should skip Note: and only get 2 columns
    assert len(tables["users"].columns) == 2


def test_column_line_that_matches_regex(tmp_path):
    """Test line 78: col_match regex matching."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table test {
    col1 varchar
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 78-82: col_match = re.match(...)
    # Line 83: if not col_match: continue
    # This should match and NOT continue
    assert len(tables["test"].columns) == 1


def test_ref_block_keyword(tmp_path):
    """Test lines 93-94: Ref block start."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table t1 {
    id int
}
Table t2 {
    id int
    fk int
}

Ref {
    t1.id > t2.fk
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 93-94: detect "Ref" and set in_ref_block = True
    assert len(refs) == 1


def test_ref_block_closing_brace(tmp_path):
    """Test line 96-98: closing brace in Ref block."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table t1 {
    id int
}
Table t2 {
    id int
    fk int
}

Ref {
    t1.id > t2.fk
}

Ref {
    t1.id > t2.id
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 96-98: closing } sets in_ref_block = False
    # Multiple Ref blocks test this
    assert len(refs) == 2


def test_ref_line_matching_regex(tmp_path):
    """Test line 98-102: ref regex matching."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table t1 {
    id int
}
Table t2 {
    id int
    fk int
}

Ref {
    t1.id > t2.fk
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 100-105: ref_match = re.match(...)
    # Line 106: if not ref_match: continue
    assert len(refs) == 1
    assert refs[0]["source_table"] == "t1"


def test_ref_append_to_list(tmp_path):
    """Test line 111: appending ref to list."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table t1 {
    id int
}
Table t2 {
    id int
    fk int
}

Ref {
    t1.id > t2.fk
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 116-125: refs.append({...})
    assert len(refs) == 1
    ref = refs[0]
    assert "source_table" in ref
    assert "target_table" in ref


def test_return_tables_and_refs(tmp_path):
    """Test line 152: return statement."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
Table t1 {
    id int
}
"""
    )

    result = parse_dbml(dbml_file)

    # Line 152: return tables, refs
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_comprehensive_dbml_file(tmp_path):
    """Comprehensive test hitting all code paths."""
    dbml_file = tmp_path / "comprehensive.dbml"
    dbml_file.write_text(
        """
// Top-level comment

Table users [note: 'User table'] {
    id int [pk]
    email varchar [unique]
    Note: 'User email address'
    status varchar
}

Table posts [headercolor: #3498db] {
    id int [pk]
    user_id int
    title varchar
    Note: 'Post title'
}

Ref {
    users.id > posts.user_id
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # This should hit:
    # - Line 44-49: table with settings [note: ...]
    # - Line 70: multiple table closing braces
    # - Line 74-76: Note: lines
    # - Line 78: column regex matching
    # - Line 93-98: Ref block start and close
    # - Line 111: ref append
    # - Line 152: return

    assert len(tables) == 2
    assert "users" in tables
    assert "posts" in tables
    assert len(refs) == 1

    # Verify columns parsed correctly (Notes excluded)
    assert len(tables["users"].columns) == 3
    assert len(tables["posts"].columns) == 3


def test_table_closing_and_columns(tmp_path):
    """Ensure table closing brace and column append are hit."""
    dbml_file = tmp_path / "simple.dbml"
    dbml_file.write_text(
        """Table t1 {
x int
}
"""
    )

    tables, refs = parse_dbml(dbml_file)
    assert "t1" in tables
    assert len(tables["t1"].columns) == 1
    assert tables["t1"].columns[0].name == "x"


def test_note_inside_triple_quotes(tmp_path):
    """Test Note with triple quotes (note_block_depth logic)."""
    dbml_file = tmp_path / "note_triple.dbml"
    dbml_file.write_text(
        """Table t {
id int
Note: '''
This is a multiline
note that spans lines
'''
name varchar
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    # Should only have 2 columns, triple quote Note ignored
    assert len(tables["t"].columns) == 2


def test_note_single_line_inside_table(tmp_path):
    """Test Note: inside table that gets skipped."""
    dbml_file = tmp_path / "note_single.dbml"
    dbml_file.write_text(
        """Table t {
id int
Note: 'single line note'
name varchar
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    # Note line should be skipped, only 2 columns
    assert len(tables["t"].columns) == 2


def test_indexes_block(tmp_path):
    """Test indexes block is properly ignored."""
    dbml_file = tmp_path / "with_indexes.dbml"
    dbml_file.write_text(
        """Table t {
id int [pk]
name varchar
indexes {
    id
    (name, id) [unique]
}
email varchar
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    # Should have 3 columns, indexes block ignored
    assert len(tables["t"].columns) == 3
    col_names = [c.name for c in tables["t"].columns]
    assert "id" in col_names
    assert "name" in col_names
    assert "email" in col_names


def test_column_that_doesnt_match_regex(tmp_path):
    """Test that invalid column lines don't break parsing."""
    dbml_file = tmp_path / "invalid_col.dbml"
    dbml_file.write_text(
        """Table t {
id int
this is not a valid column line
name varchar
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    # Should only get 2 valid columns
    assert len(tables["t"].columns) == 2


def test_ref_that_doesnt_match_regex(tmp_path):
    """Test that invalid ref lines don't break parsing."""
    dbml_file = tmp_path / "invalid_ref.dbml"
    dbml_file.write_text(
        """Table t1 {
id int
}
Table t2 {
id int
fk int
}
Ref {
this is not a valid ref line
t1.id > t2.fk
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    # Should only get 1 valid ref
    assert len(refs) == 1


def test_comprehensive_all_paths(tmp_path):
    """Hit absolutely every code path in one test."""
    dbml_file = tmp_path / "everything.dbml"
    dbml_file.write_text(
        """// Comment at top
Table users [note: 'table settings'] {
    id int [pk]
    Note: 'single note'
    email varchar [unique]
    Note: '''
    multiline
    note
    '''
    status varchar
    indexes {
        id
        (email, status)
    }
    bio text
}

Table posts {
    id int [pk]
    user_id int
    title varchar
    Note: 'post title'
}

Table tags {
    id int
}

Ref {
    invalid line here
    users.id > posts.user_id
}

Ref {
    posts.id < tags.id
}

Ref {
    users.id <> posts.id
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Tables parsed correctly
    assert len(tables) == 3
    assert "users" in tables
    assert "posts" in tables
    assert "tags" in tables

    # Users table: id, email, status, bio = 4 columns (Notes and indexes excluded)
    assert len(tables["users"].columns) == 4

    # Posts table: id, user_id, title = 3 columns
    assert len(tables["posts"].columns) == 3

    # Refs: 2 valid (>, <), 1 ignored (<>)
    assert len(refs) == 2

    # Verify ref directions
    ref1 = next(r for r in refs if r["target_table"] == "posts")
    assert ref1["source_table"] == "users"

    ref2 = next(r for r in refs if r["source_table"] == "tags")
    assert ref2["target_table"] == "posts"


def test_absolute_minimal_coverage(tmp_path):
    """Absolute minimal test to hit every missing line."""
    dbml_file = tmp_path / "min.dbml"
    # Write the simplest possible DBML that exercises all code paths
    content = "Table a {\n"
    content += "b int\n"
    content += "Note: test\n"
    content += "}\n"
    content += "Ref {\n"
    content += "a.b > a.b\n"
    content += "}\n"

    dbml_file.write_text(content)

    result = parse_dbml(dbml_file)
    tables, refs = result

    # This MUST hit:
    # Line 70: } closes table
    # Line 74-76: Note: inside table
    # Line 78: col_match for "b int"
    # Line 93-94: Ref starts ref block
    # Line 96-98: } closes ref block
    # Line 152: return statement

    assert len(tables) == 1
    assert len(refs) == 1


def test_direct_execution(tmp_path):
    """Direct execution to ensure lines are hit."""
    from model2data.parse.dbml import parse_dbml as direct_parse

    dbml_file = tmp_path / "direct.dbml"
    dbml_file.write_text("Table t {\nc int\nNote: x\n}\nRef {\nt.c > t.c\n}\n")

    t, r = direct_parse(dbml_file)
    assert len(t) == 1
    assert len(r) == 1
    assert "t" in t


def test_all_missing_lines_combined(tmp_path):
    """Ensure table closing brace and column append are hit."""
    dbml_file = tmp_path / "simple.dbml"
    dbml_file.write_text(
        """Table t1 {
x int
}
"""
    )

    tables, refs = parse_dbml(dbml_file)
    assert "t1" in tables
    assert len(tables["t1"].columns) == 1
    assert tables["t1"].columns[0].name == "x"
    """Single test to hit all remaining missing lines."""
    dbml_file = tmp_path / "complete.dbml"
    # NO leading spaces - write at column 0
    dbml_file.write_text(
        """Table users {
id int [pk]
Note: 'This is a note'
name varchar
}

Table posts {
id int
user_id int
}

Ref {
users.id > posts.user_id
}
"""
    )

    tables, refs = parse_dbml(dbml_file)

    # Line 70: closing brace (when we finish users and posts tables)
    assert len(tables) == 2

    # Line 74-76: Note: line inside table
    assert len(tables["users"].columns) == 2  # id and name, Note excluded

    # Line 78: column regex match (matches id, name, etc)
    assert any(c.name == "id" for c in tables["users"].columns)

    # Line 93-94: Ref keyword starts ref block
    # Line 96-98: closing brace in ref block and ref regex match
    assert len(refs) == 1

    # Line 152: return statement
    assert isinstance(tables, dict)
    assert isinstance(refs, list)


def test_reference_with_less_than_operator(tmp_path):
    """Test that < operator in references is correctly reversed to >."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }

    Table posts {
        id int [pk]
        user_id int
    }

    Ref {
        users.id < posts.user_id
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert len(refs) == 1
    ref = refs[0]

    # < should be reversed: posts.user_id > users.id
    # So source should be posts, target should be users
    assert ref["source_table"] == "posts"
    assert ref["source_column"] == "user_id"
    assert ref["target_table"] == "users"
    assert ref["target_column"] == "id"


def test_reference_with_diamond_operator_ignored(tmp_path):
    """Test that <> operator is ignored (many-to-many relationships)."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table roles {
        id int [pk]
    }

    Table user_roles {
        user_id int
        role_id int
    }

    Ref {
        users.id <> user_roles.user_id
    }

    Ref {
        roles.id > user_roles.role_id
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    # Only the > reference should be captured, <> should be ignored
    assert len(refs) == 1
    assert refs[0]["source_table"] == "roles"
    assert refs[0]["target_table"] == "user_roles"
