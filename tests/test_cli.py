"""Direct tests of CLI main function for proper coverage."""

import os

import pandas as pd
from typer.testing import CliRunner

from model2data.cli import app

runner = CliRunner()


def test_cli_basic_generation(tmp_path):
    """Test basic CLI generation with direct invocation."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    # Change to tmp_path so files are created there
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0
    assert "📦 Creating dbt project scaffold" in result.stdout
    assert "🧮 Generating synthetic datasets" in result.stdout
    assert "🗂️ Building staging models" in result.stdout
    assert "🧪 Generating dbt yml" in result.stdout
    assert "🪪 Ensuring dbt profile exists" in result.stdout
    assert "🎉 model2data generation complete!" in result.stdout
    assert "Next steps:" in result.stdout
    assert "dbt deps" in result.stdout
    assert "dbt seed" in result.stdout
    assert "dbt run" in result.stdout


def test_cli_with_seed(tmp_path):
    """Test CLI with deterministic seed."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--seed",
                "42",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0
    assert "🔁 Using deterministic seed: 42" in result.stdout


def test_cli_no_tables_found(tmp_path):
    """Test CLI with empty DBML file."""
    dbml_file = tmp_path / "empty.dbml"
    dbml_file.write_text("")

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 1
    assert "❌ No tables found in the provided DBML file." in result.stdout


def test_cli_with_custom_name(tmp_path):
    """Test CLI with custom project name."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--name",
                "custom_project",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0
    project_dir = tmp_path / "dbt_custom_project"
    assert project_dir.exists()


def test_cli_without_custom_name_uses_file_stem(tmp_path):
    """Test that project name defaults to file stem."""
    dbml_file = tmp_path / "my_model.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0
    project_dir = tmp_path / "dbt_my_model"
    assert project_dir.exists()


def test_cli_destination_already_exists_without_force(tmp_path):
    """Test that CLI fails when destination exists without --force."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        # First run
        result1 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
        assert result1.exit_code == 0

        # Second run without force should fail
        result2 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result2.exit_code == 1
    assert "already exists" in result2.stdout
    assert "Use --force to overwrite" in result2.stdout


def test_cli_force_overwrites_existing_directory(tmp_path):
    """Test that --force removes and recreates directory."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        # First run
        result1 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
        assert result1.exit_code == 0

        project_dir = tmp_path / "dbt_test"
        random_file = project_dir / "random.txt"
        random_file.write_text("should be deleted")
        assert random_file.exists()

        # Second run with force
        result2 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--force",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result2.exit_code == 0
    # Random file should be gone (directory was removed)
    assert not random_file.exists()
    # But project should exist again
    assert project_dir.exists()


def test_cli_copies_original_dbml_file(tmp_path):
    """Test that original DBML file is copied to project directory."""
    dbml_file = tmp_path / "my_model.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0

    # Check that DBML file was copied to project
    project_dir = tmp_path / "dbt_my_model"
    copied_dbml = project_dir / "my_model.dbml"
    assert copied_dbml.exists()
    assert copied_dbml.read_text() == dbml_file.read_text()


def test_cli_creates_seeds_with_correct_rows(tmp_path):
    """Test that seeds are created with correct number of rows."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
        email varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "25",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0

    # Check seed has correct row count
    seed_file = tmp_path / "dbt_test" / "seeds" / "raw" / "users.csv"
    assert seed_file.exists()

    df = pd.read_csv(seed_file)
    assert len(df) == 25


def test_cli_deterministic_seed_produces_identical_output(tmp_path):
    """Test that same seed produces identical data."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
        email varchar
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)

        # First run with seed
        result1 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--seed",
                "123",
                "--name",
                "run1",
            ],
        )
        assert result1.exit_code == 0

        # Second run with same seed
        result2 = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--seed",
                "123",
                "--name",
                "run2",
            ],
        )
        assert result2.exit_code == 0
    finally:
        os.chdir(original_cwd)

    # Compare outputs
    csv1 = tmp_path / "dbt_run1" / "seeds" / "raw" / "users.csv"
    csv2 = tmp_path / "dbt_run2" / "seeds" / "raw" / "users.csv"

    df1 = pd.read_csv(csv1)
    df2 = pd.read_csv(csv2)

    pd.testing.assert_frame_equal(df1, df2)


def test_cli_profile_name_derived_from_project_name(tmp_path):
    """Test that profile name is correctly derived from project name."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
                "--name",
                "my_analytics",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0

    # Check profiles.yml contains expected profile name
    profiles_yml = tmp_path / "dbt_my_analytics" / "profiles.yml"
    assert profiles_yml.exists()

    content = profiles_yml.read_text()
    assert "my_analytics_profile" in content


def test_cli_all_output_messages_present(tmp_path):
    """Test that all expected progress messages are printed."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }
    """
    )

    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "--file",
                str(dbml_file),
                "--rows",
                "10",
            ],
        )
    finally:
        os.chdir(original_cwd)

    assert result.exit_code == 0

    # Verify all progress messages
    expected_messages = [
        "📦 Creating dbt project scaffold",
        "🧮 Generating synthetic datasets from DBML definitions",
        "🗂️ Building staging models for generated seeds",
        "🧪 Generating dbt yml with tests",
        "🪪 Ensuring dbt profile exists",
        "🎉 model2data generation complete!",
        "Next steps:",
        "dbt deps",
        "dbt seed",
        "dbt run",
    ]

    for message in expected_messages:
        assert message in result.stdout, f"Missing expected message: {message}"
