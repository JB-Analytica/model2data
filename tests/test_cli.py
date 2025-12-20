import subprocess
from pathlib import Path

import pandas as pd


def test_cli_generate_smoke(tmp_path):
    """Basic smoke test for CLI generation."""
    dbml = Path("examples/hackernews.dbml").resolve()

    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml),
            "--rows",
            "20",
            "--seed",
            "1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    # dbt project created
    projects = list(tmp_path.glob("dbt_*"))
    assert projects, "No dbt project directory created"

    project = projects[0]

    # Seeds exist
    seeds = project / "seeds" / "raw"
    assert seeds.exists()
    csv_files = list(seeds.glob("*.csv"))
    assert csv_files

    # Check row counts
    for csv_file in csv_files:
        df = pd.read_csv(csv_file)
        assert len(df) == 20, f"{csv_file.name} has {len(df)} rows, expected 20"


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

    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
            "--name",
            "my_custom_project",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    # Check project name is used
    project = tmp_path / "dbt_my_custom_project"
    assert project.exists()

    # Check dbt_project.yml has correct name
    dbt_project_yml = project / "dbt_project.yml"
    assert dbt_project_yml.exists()


def test_cli_force_overwrite(tmp_path):
    """Test that --force flag overwrites existing directory."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    # First run
    result1 = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0

    # Second run without --force should fail
    result2 = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 1
    assert "already exists" in result2.stdout

    # Third run with --force should succeed
    result3 = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
            "--force",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result3.returncode == 0


def test_cli_empty_dbml_file(tmp_path):
    """Test CLI with empty/invalid DBML file."""
    dbml_file = tmp_path / "empty.dbml"
    dbml_file.write_text("")

    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "No tables found" in result.stdout


def test_cli_deterministic_seed(tmp_path):
    """Test that same seed produces identical output."""
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

    # First run with seed
    result1 = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
            "--seed",
            "42",
            "--name",
            "run1",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result1.returncode == 0

    # Second run with same seed
    result2 = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
            "--seed",
            "42",
            "--name",
            "run2",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0

    # Compare CSV outputs
    csv1 = tmp_path / "dbt_run1" / "seeds" / "raw" / "users.csv"
    csv2 = tmp_path / "dbt_run2" / "seeds" / "raw" / "users.csv"

    df1 = pd.read_csv(csv1)
    df2 = pd.read_csv(csv2)

    pd.testing.assert_frame_equal(df1, df2)


def test_cli_creates_all_required_files(tmp_path):
    """Test that all expected files and directories are created."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
        name varchar
    }
    """
    )

    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "10",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    project = tmp_path / "dbt_test"

    # Check directory structure
    assert (project / "seeds" / "raw").exists()
    assert (project / "models" / "staging").exists()
    assert (project / "macros").exists()

    # Check key files
    assert (project / "dbt_project.yml").exists()
    assert (project / "profiles.yml").exists()

    # Check seeds created
    assert (project / "seeds" / "raw" / "users.csv").exists()

    # Check staging models created
    assert (project / "models" / "staging" / "stg_users.sql").exists()
    assert (project / "models" / "staging" / "stg_users.yml").exists()


def test_cli_min_rows_validation(tmp_path):
    """Test that rows parameter has minimum validation."""
    dbml_file = tmp_path / "test.dbml"
    dbml_file.write_text(
        """
    Table users {
        id int [pk]
    }
    """
    )

    # Try with less than minimum rows (min is 10)
    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(dbml_file),
            "--rows",
            "5",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    # Should fail validation
    assert result.returncode != 0


def test_cli_nonexistent_file(tmp_path):
    """Test CLI with nonexistent file."""
    result = subprocess.run(
        [
            "model2data",
            "--file",
            str(tmp_path / "nonexistent.dbml"),
            "--rows",
            "10",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    # Should fail with file not found
    assert result.returncode != 0
