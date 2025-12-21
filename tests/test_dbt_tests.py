from model2data.cli import main as generate_cli


def test_dbt_tests_generation(tmp_path, monkeypatch):
    """Test that dbt tests are generated for various column constraints."""
    # Change to temp directory
    monkeypatch.chdir(tmp_path)

    dbml_file = tmp_path / "simple.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
        email varchar [unique]
    }

    Table posts {
        id int [pk]
        user_id int [not null]
        title varchar [not null]
    }

    Ref {
        posts.user_id > users.id}
    """
    )

    # Call generate function
    generate_cli(
        file=dbml_file,
        rows=10,
        seed=42,
        name="test_project",
        force=True,
    )

    project_dir = tmp_path / "dbt_test_project"

    # Test users model
    users_yml = project_dir / "models" / "staging" / "stg_users.yml"
    assert users_yml.exists()
    users_content = users_yml.read_text()
    assert "not_null" in users_content
    assert "unique" in users_content

    # Test posts model with foreign key relationship
    posts_yml = project_dir / "models" / "staging" / "stg_posts.yml"
    assert posts_yml.exists()
    posts_content = posts_yml.read_text()

    # Debug: print the actual content
    print("\n=== Posts YML Content ===")
    print(posts_content)
    print("=== End Content ===\n")

    assert "not_null" in posts_content
    # The relationship test should be on user_id column
    assert "user_id" in posts_content
    assert "relationships" in posts_content, (
        f"Expected 'relationships' in content:\n{posts_content}"
    )
    assert "ref('stg_users')" in posts_content
