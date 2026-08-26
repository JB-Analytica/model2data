from pathlib import Path

import pytest

from model2data.parse.dbml import (
    _parse_column_settings,
    _sanitize_table_name,
    _strip_quotes,
    get_many_to_many_refs,
    get_parse_warnings,
    parse_dbml,
)
from model2data.utils import normalize_identifier


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
    settings, note, description, default, inline_ref = _parse_column_settings("pk")
    assert "pk" in settings
    assert note is None
    assert description is None
    assert default is None
    assert inline_ref is None

    # Inline ref setting
    settings, note, description, default, inline_ref = _parse_column_settings(
        "not null, ref: > users.id"
    )
    assert "not null" in settings
    assert inline_ref == {"operator": ">", "target_table": "users", "target_column": "id"}

    # Multiple settings
    settings, note, description, default, inline_ref = _parse_column_settings(
        "pk, not null, unique"
    )
    assert "pk" in settings
    assert "not null" in settings
    assert "unique" in settings
    assert note is None

    # Settings with JSON min/max note
    settings, note, description, default, inline_ref = _parse_column_settings(
        'pk, not null, note: \'{"min": 1, "max": 5}\''
    )
    assert "pk" in settings
    assert "not null" in settings
    assert note is not None
    assert note["min"] == 1
    assert note["max"] == 5
    assert description is None

    # Just a note
    settings, note, description, default, inline_ref = _parse_column_settings(
        'note: \'{"min": 0, "max": 100}\''
    )
    assert len(settings) == 0
    assert note is not None
    assert note["min"] == 0
    assert note["max"] == 100

    # Plain-text note becomes a description, not a discarded value
    settings, note, description, default, inline_ref = _parse_column_settings(
        "note: 'the user's primary email address'"
    )
    assert note is None
    assert description == "the user's primary email address"

    # default: values
    settings, note, description, default, inline_ref = _parse_column_settings("default: 'active'")
    assert default == "active"

    settings, note, description, default, inline_ref = _parse_column_settings("default: 0")
    assert default == 0 and isinstance(default, int)

    settings, note, description, default, inline_ref = _parse_column_settings("default: 3.14")
    assert default == 3.14

    settings, note, description, default, inline_ref = _parse_column_settings("default: true")
    assert default is True

    settings, note, description, default, inline_ref = _parse_column_settings("default: `now()`")
    assert default is None

    # Empty
    settings, note, description, default, inline_ref = _parse_column_settings("")
    assert len(settings) == 0
    assert note is None

    # None
    settings, note, description, default, inline_ref = _parse_column_settings(None)
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


def test_note_curly_brace_block_inside_table(tmp_path):
    """Test the `Note { ... }` block form (distinct from `Note: '...'`).

    Regression test: this form used to fall through to column parsing and
    produce a spurious column named "Note" with data_type "{".
    """
    dbml_file = tmp_path / "note_curly.dbml"
    dbml_file.write_text(
        """Table t {
id int
name varchar
Note {
    'Describes this table across multiple lines'
}
email varchar
}
"""
    )
    tables, refs = parse_dbml(dbml_file)
    columns = tables["t"].columns
    assert len(columns) == 3
    assert all(col.name != "Note" for col in columns)


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

    # But the <> ref is not silently dropped - it's captured separately.
    m2m = get_many_to_many_refs()
    assert len(m2m) == 1
    assert m2m[0]["source_table"] == "users"
    assert m2m[0]["source_column"] == "id"
    assert m2m[0]["target_table"] == "user_roles"
    assert m2m[0]["target_column"] == "user_id"


# ---------------------------------------------------------
# Inline column-level `[ref: ...]` support
# ---------------------------------------------------------


def test_inline_ref_greater_than_operator(tmp_path):
    """`[ref: > table.column]` on a column is equivalent to a standalone Ref."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table orders {
        id int [pk]
        user_id int [ref: > users.id]
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert len(refs) == 1
    ref = refs[0]
    assert ref["source_table"] == "orders"
    assert ref["source_column"] == "user_id"
    assert ref["target_table"] == "users"
    assert ref["target_column"] == "id"


def test_inline_ref_less_than_operator(tmp_path):
    """`[ref: < table.column]` is reversed, same as a standalone `<` Ref."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk, ref: < orders.user_id]
    }

    Table orders {
        id int [pk]
        user_id int
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert len(refs) == 1
    ref = refs[0]
    # users.id [ref: < orders.user_id] means orders.user_id is the actual
    # FK-holding side, so it becomes source; users.id becomes target.
    assert ref["source_table"] == "orders"
    assert ref["source_column"] == "user_id"
    assert ref["target_table"] == "users"
    assert ref["target_column"] == "id"


def test_inline_ref_many_to_many_operator(tmp_path):
    """`[ref: <> table.column]` is captured as a many-to-many ref, not an FK."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table posts {
        id int [pk]
        tag_id int [ref: <> tags.id]
    }

    Table tags {
        id int [pk]
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert refs == []

    m2m = get_many_to_many_refs()
    assert len(m2m) == 1
    assert m2m[0]["source_table"] == "posts"
    assert m2m[0]["source_column"] == "tag_id"
    assert m2m[0]["target_table"] == "tags"
    assert m2m[0]["target_column"] == "id"


def test_inline_ref_with_quoted_identifiers(tmp_path):
    """Inline refs handle double-quoted table/column names like standalone Refs do."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table "users" {
        "id" int [pk]
    }

    Table "orders" {
        "id" int [pk]
        "user_id" int [ref: > "users"."id"]
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert len(refs) == 1
    ref = refs[0]
    assert ref["source_table"] == "orders"
    assert ref["source_column"] == "user_id"
    assert ref["target_table"] == "users"
    assert ref["target_column"] == "id"


def test_inline_ref_malformed_value_is_ignored(tmp_path):
    """A `ref:` setting that doesn't match `<op> table.column` is dropped,
    not crashed on -- same tolerant handling as an unparseable standalone Ref."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table orders {
        id int [pk]
        user_id int [ref: > not_a_table_dot_column]
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    assert refs == []
    assert get_many_to_many_refs() == []
    orders = tables["orders"]
    user_id_col = next(c for c in orders.columns if c.name == "user_id")
    assert user_id_col is not None


def test_inline_ref_combined_with_other_settings(tmp_path):
    """An inline ref alongside other column settings doesn't disturb them."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table orders {
        id int [pk]
        user_id int [not null, ref: > users.id]
    }
    """
    )

    tables, refs = parse_dbml(dbml_file)

    orders = tables["orders"]
    user_id_col = next(c for c in orders.columns if c.name == "user_id")
    assert "not null" in user_id_col.settings
    assert "ref" not in " ".join(user_id_col.settings)

    assert len(refs) == 1
    assert refs[0]["source_table"] == "orders"
    assert refs[0]["target_table"] == "users"


# ---------------------------------------------------------
# Enum support
# ---------------------------------------------------------


def test_enum_block_resolves_column_type(tmp_path):
    dbml_file = tmp_path / "enums.dbml"
    dbml_file.write_text(
        """
    Enum status_enum {
        active
        inactive
        pending [note: 'awaiting review']
    }

    Table orders {
        id int [pk]
        status status_enum [not null]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    status_col = next(c for c in tables["orders"].columns if c.name == "status")
    assert status_col.enum_values == ["active", "inactive", "pending"]


def test_enum_match_is_case_insensitive(tmp_path):
    dbml_file = tmp_path / "enums.dbml"
    dbml_file.write_text(
        """
    enum Status_Enum {
        active
        inactive
    }

    Table orders {
        id int [pk]
        status STATUS_ENUM
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    status_col = next(c for c in tables["orders"].columns if c.name == "status")
    assert status_col.enum_values == ["active", "inactive"]


def test_column_without_enum_type_has_no_enum_values(tmp_path):
    dbml_file = tmp_path / "enums.dbml"
    dbml_file.write_text(
        """
    Enum status_enum {
        active
        inactive
    }

    Table orders {
        id int [pk]
        name varchar
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    name_col = next(c for c in tables["orders"].columns if c.name == "name")
    assert name_col.enum_values is None


# ---------------------------------------------------------
# Table and column descriptions from notes
# ---------------------------------------------------------


def test_table_single_line_note_captured(tmp_path):
    dbml_file = tmp_path / "notes.dbml"
    dbml_file.write_text(
        """
    Table users {
        Note: 'Stores registered users'
        id int [pk]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert tables["users"].description == "Stores registered users"


def test_table_multiline_note_block_captured(tmp_path):
    dbml_file = tmp_path / "notes.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        Note {
            'Stores registered users'
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert tables["users"].description == "Stores registered users"


def test_column_plain_text_note_becomes_description(tmp_path):
    dbml_file = tmp_path / "notes.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        email varchar [note: 'the primary email address']
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    email_col = next(c for c in tables["users"].columns if c.name == "email")
    assert email_col.description == "the primary email address"


def test_column_json_min_max_note_still_works(tmp_path):
    dbml_file = tmp_path / "notes.dbml"
    dbml_file.write_text(
        """
    Table products {
        id int [pk]
        price numeric [note: '{"min": 1, "max": 500}']
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    price_col = next(c for c in tables["products"].columns if c.name == "price")
    assert price_col.note == {"min": 1, "max": 500}
    assert price_col.description is None


# ---------------------------------------------------------
# Composite keys from indexes {} blocks
# ---------------------------------------------------------


def test_composite_pk_from_indexes_block(tmp_path):
    dbml_file = tmp_path / "composite.dbml"
    dbml_file.write_text(
        """
    Table order_items {
        order_id int
        product_id int
        quantity int

        indexes {
            (order_id, product_id) [pk]
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    keys = tables["order_items"].composite_keys
    assert len(keys) == 1
    assert keys[0]["columns"] == ["order_id", "product_id"]
    assert keys[0]["type"] == "pk"


def test_composite_unique_from_indexes_block(tmp_path):
    dbml_file = tmp_path / "composite.dbml"
    dbml_file.write_text(
        """
    Table memberships {
        user_id int
        org_id int

        indexes {
            (user_id, org_id) [unique, name: 'uq_membership']
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    keys = tables["memberships"].composite_keys
    assert len(keys) == 1
    assert keys[0]["columns"] == ["user_id", "org_id"]
    assert keys[0]["type"] == "unique"


def test_single_column_index_entry_still_captured(tmp_path):
    dbml_file = tmp_path / "composite.dbml"
    dbml_file.write_text(
        """
    Table users {
        email varchar

        indexes {
            (email) [unique]
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    keys = tables["users"].composite_keys
    assert len(keys) == 1
    assert keys[0]["columns"] == ["email"]
    assert keys[0]["type"] == "unique"


def test_indexes_block_without_pk_or_unique_is_ignored(tmp_path):
    dbml_file = tmp_path / "composite.dbml"
    dbml_file.write_text(
        """
    Table users {
        email varchar

        indexes {
            (email) [name: 'idx_email']
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert tables["users"].composite_keys == []


# ---------------------------------------------------------
# default: values
# ---------------------------------------------------------


def test_default_values_parsed_by_type(tmp_path):
    dbml_file = tmp_path / "defaults.dbml"
    dbml_file.write_text(
        """
    Table accounts {
        id int [pk]
        status varchar [default: 'active']
        retries int [default: 0]
        rate float [default: 3.14]
        is_active boolean [default: true]
        created_at timestamp [default: `now()`]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    cols = {c.name: c for c in tables["accounts"].columns}
    assert cols["status"].default == "active"
    assert cols["retries"].default == 0 and isinstance(cols["retries"].default, int)
    assert cols["rate"].default == 3.14
    assert cols["is_active"].default is True
    assert cols["created_at"].default is None


def test_default_value_helper_edge_cases():
    from model2data.parse.dbml import _parse_default_value

    assert _parse_default_value("") is None
    assert _parse_default_value("false") is False
    assert _parse_default_value("not_a_number") is None


def test_composite_key_pk_and_close_brace_on_same_line(tmp_path):
    dbml_file = tmp_path / "composite.dbml"
    dbml_file.write_text(
        """
    Table order_items {
        order_id int
        product_id int

        indexes {
            (order_id, product_id) [pk] }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    keys = tables["order_items"].composite_keys
    assert len(keys) == 1
    assert keys[0]["columns"] == ["order_id", "product_id"]
    assert keys[0]["type"] == "pk"


def test_indexes_block_malformed_entry_is_skipped():
    from model2data.parse.dbml import _parse_composite_key_line

    assert _parse_composite_key_line("(,) [pk]") is None
    assert _parse_composite_key_line("not an index line") is None


def test_table_single_line_inline_triple_quote_note(tmp_path):
    dbml_file = tmp_path / "notes.dbml"
    dbml_file.write_text(
        """
    Table users {
        Note: '''Stores registered users'''
        id int [pk]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert tables["users"].description == "Stores registered users"


def test_advanced_features_example_has_enum_and_composite_key():
    dbml_path = Path("examples/advanced_features.dbml")
    tables, refs = parse_dbml(dbml_path)

    status_col = next(c for c in tables["expenses"].columns if c.name == "status")
    assert status_col.enum_values == ["pending", "approved", "rejected"]

    keys = tables["project_assignments"].composite_keys
    assert {"columns": ["project_id", "employee_id"], "type": "unique"} in keys


def test_malformed_column_line_inside_table_is_skipped(tmp_path):
    dbml_file = tmp_path / "malformed.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        !!!
        name varchar
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    column_names = {c.name for c in tables["users"].columns}
    assert column_names == {"id", "name"}


def test_malformed_column_line_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "malformed.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        !!!
        name varchar
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("users" in w and "!!!" in w for w in warnings)
    # Rest of the table still parses fine (partial-failure tolerance).
    assert {c.name for c in tables["users"].columns} == {"id", "name"}


def test_sentence_like_column_definition_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "sentence.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        this is not really a column definition at all
        name varchar
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Sentence-like" in w for w in warnings)
    assert {c.name for c in tables["users"].columns} == {"id", "name"}


def test_malformed_ref_line_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "malformed_ref.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table posts {
        id int [pk]
        user_id int
    }

    Ref {
        this is not a valid ref line
        posts.user_id > users.id
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Unrecognized line in Ref block" in w for w in warnings)
    # The well-formed ref line right after it still parses.
    assert any(r["source_table"] == "posts" and r["target_table"] == "users" for r in refs)


def test_one_liner_ref_statement_is_parsed(tmp_path):
    """Regression test: a bare `Ref: a.b > c.d` statement (not wrapped in
    `Ref { ... }`) used to be mistaken for the start of a block and its
    content silently discarded, with zero warning, on every occurrence.
    """
    dbml_file = tmp_path / "one_liner_ref.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table posts {
        id int [pk]
        user_id int
    }

    Ref: posts.user_id > users.id
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert refs == [
        {
            "source_table": "posts",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        }
    ]
    assert get_parse_warnings() == []


def test_one_liner_ref_statement_with_less_than_operator(tmp_path):
    dbml_file = tmp_path / "one_liner_ref_lt.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Table posts {
        id int [pk]
        user_id int
    }

    Ref: users.id < posts.user_id
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert refs == [
        {
            "source_table": "posts",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        }
    ]


def test_multiple_one_liner_refs_all_parse(tmp_path):
    """The bug affected every one-liner Ref in a file, not just the first —
    confirm several in a row (interspersed with unrelated top-level
    statements) all get captured.
    """
    dbml_file = tmp_path / "multi_one_liner_refs.dbml"
    dbml_file.write_text(
        """
    Table a {
        id int [pk]
    }
    Table b {
        id int [pk]
        a_id int
    }
    Table c {
        id int [pk]
        b_id int
    }

    Ref: b.a_id > a.id
    Ref: c.b_id > b.id
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert {(r["source_table"], r["target_table"]) for r in refs} == {("b", "a"), ("c", "b")}
    assert get_parse_warnings() == []


def test_malformed_one_liner_ref_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "malformed_one_liner_ref.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }

    Ref: this is not a valid relationship expression
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Unrecognized Ref statement" in w for w in warnings)
    assert refs == []


def test_ref_pointing_at_nonexistent_table_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "dangling_ref.dbml"
    dbml_file.write_text(
        """
    Table posts {
        id int [pk]
        user_id int
    }

    Ref {
        posts.user_id > users.id
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("users" in w and "not found among parsed tables" in w for w in warnings)
    # The ref itself is still captured (visibility, not rejection).
    assert refs == [
        {
            "source_table": "posts",
            "source_column": "user_id",
            "target_table": "users",
            "target_column": "id",
        }
    ]


def test_valid_dbml_produces_no_parse_warnings():
    tables, refs = parse_dbml(Path("examples/hackernews.dbml"))
    assert get_parse_warnings() == []


def test_malformed_indexes_line_is_reported_as_parse_warning(tmp_path):
    dbml_file = tmp_path / "bad_index.dbml"
    dbml_file.write_text(
        """
    Table order_items {
        order_id int
        product_id int
        indexes {
            not a valid index line at all [pk]
        }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Unrecognized indexes{} line" in w for w in warnings)
    assert tables["order_items"].composite_keys == []


def test_malformed_indexes_line_on_closing_brace_line_is_reported(tmp_path):
    dbml_file = tmp_path / "bad_index_closing.dbml"
    dbml_file.write_text(
        """
    Table order_items {
        order_id int
        indexes {
            garbled nonsense here }
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Unrecognized indexes{} line" in w for w in warnings)
    assert tables["order_items"].composite_keys == []


def test_composite_ref_with_mismatched_column_counts_is_reported(tmp_path):
    dbml_file = tmp_path / "mismatched_composite_ref.dbml"
    dbml_file.write_text(
        """
    Table order_items {
        order_id int
        variant_id int
    }

    Table order_variants {
        order_id int
        variant_id int
        extra_id int
    }

    Ref {
        order_items.(order_id, variant_id) > order_variants.(order_id, variant_id, extra_id)
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Composite Ref column count mismatch" in w for w in warnings)
    assert refs == []


def test_composite_ref_block_expands_to_single_column_refs(tmp_path):
    dbml_file = tmp_path / "composite_ref.dbml"
    dbml_file.write_text(
        """
    Table order_variants {
        order_id int
        variant_id int
        indexes {
            (order_id, variant_id) [pk]
        }
    }

    Table order_items {
        order_id int
        variant_id int
    }

    Ref {
        order_items.(order_id, variant_id) > order_variants.(order_id, variant_id)
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert get_parse_warnings() == []
    assert len(refs) == 2
    assert {
        "source_table": "order_items",
        "source_column": "order_id",
        "target_table": "order_variants",
        "target_column": "order_id",
    } in refs
    assert {
        "source_table": "order_items",
        "source_column": "variant_id",
        "target_table": "order_variants",
        "target_column": "variant_id",
    } in refs


def test_strip_quotes_handles_backticks():
    """Regression test: `_strip_quotes` stripped '"' and "'" but never a
    backtick, even though backtick-quoted identifiers are explicitly
    documented as supported (see `_INLINE_REF_RE`'s docstring). A backtick-
    quoted table name like `` `user accounts` `` came out of parsing still
    wrapped in literal backticks, which then leaked into generated dbt
    model filenames/ref() calls dbt could not even parse.
    """
    assert _strip_quotes("`user accounts`") == "user accounts"
    assert _strip_quotes('"user accounts"') == "user accounts"
    assert _strip_quotes("'user accounts'") == "user accounts"
    assert _strip_quotes("bare_name") == "bare_name"


def test_sanitize_table_name_preserves_valid_identifiers():
    """`_sanitize_table_name` must round-trip an already-valid identifier
    unchanged -- including one with a leading underscore (e.g. dlt's
    `_dlt_version`) -- since generated seeds/models are keyed off the exact
    DBML table name (see test_dbt_naming.py's documented naming boundary).
    Unlike `normalize_identifier` (used only for the CLI's own --name
    option), it must not lowercase or strip leading/trailing underscores.
    """
    assert _sanitize_table_name("_dlt_version") == "_dlt_version"
    assert _sanitize_table_name("stories__kids") == "stories__kids"
    assert _sanitize_table_name("Users") == "Users"


def test_sanitize_table_name_makes_quoted_identifier_dbt_safe():
    """A quoted DBML table name may legally contain spaces or punctuation;
    `_sanitize_table_name` must turn that into something safe to use as a
    dbt model name and filesystem path component.
    """
    assert _sanitize_table_name("user accounts") == "user_accounts"
    assert _sanitize_table_name("123abc") == "t_123abc"
    assert _sanitize_table_name("!!!") == "table"


def test_backtick_quoted_table_name_is_sanitized_end_to_end(tmp_path):
    """Regression test for the bug found by a genuinely new hand-authored
    schema: a backtick-quoted table name with a space produced literal
    backticks and a space in `tables`, which downstream turned into a dbt
    model filename/ref() call (`stg_`user accounts`.sql`) that a real
    `dbt build` could not parse at all.
    """
    dbml_file = tmp_path / "backtick_table.dbml"
    dbml_file.write_text(
        """
    Table `user accounts` {
        id integer [pk]
        name varchar
    }

    Table orders {
        id integer [pk]
        account_id integer [ref: > `user accounts`.id]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert get_parse_warnings() == []
    assert "user_accounts" in tables
    assert "`" not in "".join(tables.keys())
    assert refs == [
        {
            "source_table": "orders",
            "source_column": "account_id",
            "target_table": "user_accounts",
            "target_column": "id",
        }
    ]


def test_table_name_with_space_normalized_consistently_across_refs(tmp_path):
    """A double-quoted table name with a space must be sanitized the same
    way at every point it's referenced -- table definition, standalone Ref
    block, and inline `[ref: ...]` -- so refs still resolve against the
    tables dict (which is keyed by the sanitized name).
    """
    dbml_file = tmp_path / "quoted_space.dbml"
    dbml_file.write_text(
        """
    Table "order items" {
        id integer [pk]
    }

    Table "shipping info" {
        id integer [pk]
        item_id integer
    }

    Ref: "shipping info".item_id > "order items".id
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert get_parse_warnings() == []
    assert set(tables.keys()) == {"order_items", "shipping_info"}
    assert refs == [
        {
            "source_table": "shipping_info",
            "source_column": "item_id",
            "target_table": "order_items",
            "target_column": "id",
        }
    ]


def test_project_and_tablegroup_blocks_are_silently_ignored(tmp_path):
    """`Project { ... }` and `TableGroup { ... }` are real DBML constructs
    model2data intentionally doesn't need to understand. They (including
    their multi-line interior content, and a nested brace inside the
    Project block, e.g. its own `Note { ... }`) must be skipped cleanly:
    no crash, no parse warning, and no effect on the tables/refs that
    surround them.
    """
    dbml_file = tmp_path / "project_and_tablegroup.dbml"
    dbml_file.write_text(
        """
    Project my_project {
      database_type: 'PostgreSQL'
      Note {
        'A nested brace inside the Project block should not close it early.'
      }
    }

    Table posts {
      id integer [pk]
    }

    Table tags {
      id integer [pk]
    }

    TableGroup content {
      posts
      tags
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert get_parse_warnings() == []
    assert set(tables.keys()) == {"posts", "tags"}
    assert refs == []


def test_unrecognized_top_level_line_is_reported_as_parse_warning(tmp_path):
    """Regression test: a stray character (e.g. a control character from a
    corrupted file) landing right before a `Table ...{` line used to make
    that `.lower().startswith("table ")` check fail silently -- the line
    matched nothing at the top level and, since only lines *inside* a
    table/ref/enum/note/indexes block ever warned about being
    unrecognized, the entire table (and everything up to its closing `}`)
    vanished from the parsed result with zero warnings. Any top-level line
    that isn't a table/enum/ref/Project/TableGroup construct must now be
    reported, not silently dropped.
    """
    dbml_file = tmp_path / "stray_top_level.dbml"
    dbml_file.write_text(
        """
    Table customers {
      id integer [pk]
    }

    this is not a real dbml construct

    Table orders {
      id integer [pk]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert set(tables.keys()) == {"customers", "orders"}
    warnings = get_parse_warnings()
    assert any("this is not a real dbml construct" in w for w in warnings)


def test_tagging_m2m_example_bridge_table_and_project_blocks():
    """examples/tagging_m2m.dbml: a many-to-many bridge table (post_tags,
    composite pk on both FK columns) plus Project {}/TableGroup {} blocks,
    which must parse cleanly with zero warnings.
    """
    tables, refs = parse_dbml(Path("examples/tagging_m2m.dbml"))
    assert get_parse_warnings() == []
    assert set(tables.keys()) == {"posts", "tags", "post_tags"}
    post_tags = tables["post_tags"]
    assert post_tags.composite_keys == [{"columns": ["post_id", "tag_id"], "type": "pk"}]
    assert {r["source_column"] for r in refs} == {"post_id", "tag_id"}


def test_mixed_quotes_crlf_example_parses_cleanly():
    """examples/mixed_quotes_crlf.dbml: backtick- and double-quoted
    identifiers (including one with a space), an inline `[ref: ...]`, and
    CRLF line endings all parse cleanly with a single sanitized table name
    across the board.
    """
    tables, refs = parse_dbml(Path("examples/mixed_quotes_crlf.dbml"))
    assert get_parse_warnings() == []
    assert set(tables.keys()) == {"user_accounts", "orders"}
    assert "`" not in "".join(tables.keys())
    assert refs == [
        {
            "source_table": "orders",
            "source_column": "account_id",
            "target_table": "user_accounts",
            "target_column": "id",
        }
    ]


@pytest.mark.parametrize(
    "fixture_name",
    ["out_of_order", "composite_only", "wide_fanout", "minimal_single_table"],
)
def test_fixture_dbml_files_parse_without_warnings(fixture_name):
    """tests/fixtures/*.dbml: regression coverage for edge cases found by
    genuinely new schemas during the 1.0 hardening pass -- out-of-order
    table/enum/Ref declarations, a table with only a composite key and no
    single-column id/pk, a wide fact table with 9 FK columns, and a
    single-table schema with no refs at all. All must parse cleanly.
    """
    tables, refs = parse_dbml(Path(f"tests/fixtures/{fixture_name}.dbml"))
    assert get_parse_warnings() == []
    assert tables


def test_unclosed_table_at_eof_is_recovered_and_reported(tmp_path):
    """Regression test: a truncated file (or one missing a closing brace)
    used to silently swallow the still-open table -- and, worse, every
    subsequent line in the file, since each was interpreted as if it
    belonged to that never-closed table. The unclosed table must now be
    recovered (best effort) and reported via a parse warning.
    """
    dbml_file = tmp_path / "unclosed_table.dbml"
    dbml_file.write_text(
        """
    Table customers {
      id integer [pk]
      name varchar
    """
    )
    tables, refs = parse_dbml(dbml_file)
    assert "customers" in tables
    assert {c.name for c in tables["customers"].columns} == {"id", "name"}
    warnings = get_parse_warnings()
    assert any("customers" in w and "never closed" in w for w in warnings)


def test_unclosed_project_block_does_not_swallow_rest_of_file(tmp_path):
    """Regression test: removing a Project block's closing brace used to
    make the ignored-block-depth tracker never return to 0, silently
    swallowing every table declared afterward for the rest of the file
    with zero warnings.
    """
    dbml_file = tmp_path / "unclosed_project.dbml"
    dbml_file.write_text(
        """
    Project my_project {
      database_type: 'PostgreSQL'

    Table customers {
      id integer [pk]
    }
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Project/TableGroup" in w and "never closed" in w for w in warnings)


def test_unclosed_note_block_at_eof_is_reported(tmp_path):
    """Regression test companion to the unclosed-table case above: a
    multi-line table note whose closing `'''` (or `}`) is missing must also
    be reported, not just the table that contains it.
    """
    dbml_file = tmp_path / "unclosed_note.dbml"
    dbml_file.write_text(
        """
    Table foo {
      id int [pk]
      note: '''
      an unterminated triple-quoted note
    """
    )
    tables, refs = parse_dbml(dbml_file)
    warnings = get_parse_warnings()
    assert any("Note block" in w and "never closed" in w for w in warnings)
    assert any("foo" in w and "never closed" in w for w in warnings)
