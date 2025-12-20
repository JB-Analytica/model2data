from model2data.cli import main as generate_cli


def test_dbt_tests_generation(tmp_path, monkeypatch):
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
    model_yml = project_dir / "models" / "staging" / "stg_users.yml"
    assert model_yml.exists()
    content = model_yml.read_text()
    assert "not_null" in content
    assert "unique" in content
