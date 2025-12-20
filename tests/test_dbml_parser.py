from pathlib import Path

from model2data.parse.dbml import parse_dbml


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
