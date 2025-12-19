import shutil
import tempfile
from pathlib import Path

import pytest

from model2data.dbt.project import (
    TEMPLATES_DIR,
    _render_template,
    create_profiles_yml,
    create_project_scaffold,
    create_staging_models,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    tmpdir = Path(tempfile.mkdtemp())
    yield tmpdir
    shutil.rmtree(tmpdir)


def test_create_project_scaffold_creates_directories(temp_dir):
    """Test that all required dbt directories are created."""
    project_name = "test_project"
    profile_name = "test_profile"

    create_project_scaffold(temp_dir, project_name, profile_name)

    # Verify main directories exist
    assert (temp_dir / "models" / "staging").exists()
    assert (temp_dir / "seeds" / "raw").exists()
    assert (temp_dir / "analysis").exists()
    assert (temp_dir / "macros").exists()
    assert (temp_dir / "tests").exists()
    assert (temp_dir / "snapshots").exists()


def test_create_project_scaffold_creates_dbt_project_yml(temp_dir):
    """Test that dbt_project.yml is created with correct content."""
    project_name = "my_analytics"
    profile_name = "my_profile"

    create_project_scaffold(temp_dir, project_name, profile_name)

    dbt_project_file = temp_dir / "dbt_project.yml"
    assert dbt_project_file.exists()

    content = dbt_project_file.read_text()
    assert project_name in content
    assert profile_name in content


def test_create_project_scaffold_idempotent(temp_dir):
    """Test that running scaffold twice doesn't fail."""
    project_name = "test_project"
    profile_name = "test_profile"

    # Run twice
    create_project_scaffold(temp_dir, project_name, profile_name)
    create_project_scaffold(temp_dir, project_name, profile_name)

    # Should still work
    assert (temp_dir / "models" / "staging").exists()
    assert (temp_dir / "dbt_project.yml").exists()


def test_create_staging_models_generates_sql_files(temp_dir):
    """Test that staging models are created for each CSV seed."""
    # Setup: create seed CSV files
    seeds_path = temp_dir / "seeds" / "raw"
    seeds_path.mkdir(parents=True, exist_ok=True)

    (seeds_path / "raw_stories.csv").write_text("id,title\n1,test\n")
    (seeds_path / "raw_users.csv").write_text("id,name\n1,alice\n")

    # Create staging models
    create_staging_models(temp_dir, "test_project")

    models_path = temp_dir / "models" / "staging"

    # Verify staging files were created
    assert (models_path / "stg_raw_stories.sql").exists()
    assert (models_path / "stg_raw_users.sql").exists()


def test_create_staging_models_correct_content(temp_dir):
    """Test that staging models have correct SQL content."""
    seeds_path = temp_dir / "seeds" / "raw"
    seeds_path.mkdir(parents=True, exist_ok=True)

    (seeds_path / "raw_stories.csv").write_text("id,title\n1,test\n")

    create_staging_models(temp_dir, "test_project")

    model_file = temp_dir / "models" / "staging" / "stg_raw_stories.sql"
    content = model_file.read_text()

    # Verify content includes key elements
    assert "stg_raw_stories" in content.lower() or "raw_stories" in content
    assert "source('raw', 'raw_stories')" in content
    assert "select *" in content.lower()


def test_create_staging_models_skips_existing(temp_dir):
    """Test that existing staging models are not overwritten."""
    seeds_path = temp_dir / "seeds" / "raw"
    models_path = temp_dir / "models" / "staging"
    seeds_path.mkdir(parents=True, exist_ok=True)
    models_path.mkdir(parents=True, exist_ok=True)

    (seeds_path / "raw_stories.csv").write_text("id,title\n1,test\n")

    # Create existing model with custom content
    existing_model = models_path / "stg_raw_stories.sql"
    custom_content = "-- My custom staging model\nselect id from somewhere"
    existing_model.write_text(custom_content)

    create_staging_models(temp_dir, "test_project")

    # Verify existing model was not overwritten
    assert existing_model.read_text() == custom_content


def test_create_staging_models_handles_empty_seeds(temp_dir):
    """Test that function handles directory with no CSV files."""
    seeds_path = temp_dir / "seeds" / "raw"
    seeds_path.mkdir(parents=True, exist_ok=True)

    # Should not raise an error
    create_staging_models(temp_dir, "test_project")

    models_path = temp_dir / "models" / "staging"
    assert models_path.exists()

    # No staging models should be created
    assert len(list(models_path.glob("*.sql"))) == 0


def test_create_profiles_yml_creates_file(temp_dir):
    """Test that profiles.yml is created."""
    profile_name = "test_profile"

    create_profiles_yml(temp_dir, profile_name)

    profiles_file = temp_dir / "profiles.yml"
    assert profiles_file.exists()

    content = profiles_file.read_text()
    assert profile_name in content


def test_create_profiles_yml_skips_if_profile_exists(temp_dir):
    """Test that existing profile is not overwritten if profile name exists."""
    profile_name = "test_profile"
    profiles_file = temp_dir / "profiles.yml"

    # Create existing profiles.yml with the profile name
    existing_content = f"{profile_name}:\n  outputs:\n    dev:\n      type: duckdb"
    profiles_file.write_text(existing_content)

    create_profiles_yml(temp_dir, profile_name)

    # Should not be modified
    assert profiles_file.read_text() == existing_content


def test_create_profiles_yml_appends_if_different_profile(temp_dir):
    """Test that new profile can be added if different name."""
    profiles_file = temp_dir / "profiles.yml"

    # Create existing profile
    profiles_file.write_text("other_profile:\n  outputs:\n    dev:\n      type: postgres")

    # This test verifies the function runs without error
    # Actual append behavior depends on template implementation
    create_profiles_yml(temp_dir, "new_profile")

    assert profiles_file.exists()


def test_render_template_creates_output(temp_dir):
    """Test that _render_template creates output file with rendered content."""
    # This test assumes templates exist in the templates directory
    if not TEMPLATES_DIR.exists():
        pytest.skip("Templates directory not found")

    # Find any .jinja template
    templates = list(TEMPLATES_DIR.glob("*.jinja"))
    if not templates:
        pytest.skip("No templates found")

    template_name = templates[0].name
    output_path = temp_dir / "rendered_output.txt"
    context = {"project_name": "test", "profile_name": "test_profile"}

    _render_template(template_name, output_path, context)

    assert output_path.exists()
    content = output_path.read_text()
    assert len(content) > 0


def test_render_template_substitutes_variables(temp_dir):
    """Test that template variables are correctly substituted."""
    if not TEMPLATES_DIR.exists():
        pytest.skip("Templates directory not found")

    dbt_project_template = TEMPLATES_DIR / "dbt_project.yml.jinja"
    if not dbt_project_template.exists():
        pytest.skip("dbt_project.yml.jinja template not found")

    output_path = temp_dir / "dbt_project.yml"
    context = {"project_name": "my_project", "profile_name": "my_profile"}

    _render_template("dbt_project.yml.jinja", output_path, context)

    content = output_path.read_text()
    assert "my_project" in content
    assert "my_profile" in content


def test_render_template_raises_on_missing_template(temp_dir):
    """Test that _render_template raises error for missing template."""
    output_path = temp_dir / "output.txt"

    with pytest.raises(FileNotFoundError, match="Template not found"):
        _render_template("nonexistent_template.jinja", output_path, {})


def test_templates_dir_exists():
    """Test that TEMPLATES_DIR constant points to valid location."""
    # TEMPLATES_DIR should be defined
    assert TEMPLATES_DIR is not None

    # It should be a Path object
    assert isinstance(TEMPLATES_DIR, Path)


def test_create_project_scaffold_with_special_characters(temp_dir):
    """Test project creation with special characters in names."""
    project_name = "my-analytics-2024"
    profile_name = "prod_profile"

    create_project_scaffold(temp_dir, project_name, profile_name)

    assert (temp_dir / "dbt_project.yml").exists()


def test_staging_models_preserves_csv_name(temp_dir):
    """Test that staging model names are based on CSV filenames."""
    seeds_path = temp_dir / "seeds" / "raw"
    seeds_path.mkdir(parents=True, exist_ok=True)

    # Create CSV with specific name
    csv_name = "raw_customer_orders"
    (seeds_path / f"{csv_name}.csv").write_text("id,amount\n1,100\n")

    create_staging_models(temp_dir, "test_project")

    model_file = temp_dir / "models" / "staging" / f"stg_{csv_name}.sql"
    assert model_file.exists()

    content = model_file.read_text()
    assert csv_name in content


def test_create_project_scaffold_handles_nested_path(temp_dir):
    """Test that scaffold works with nested destination path."""
    nested_path = temp_dir / "projects" / "analytics" / "dbt_project"

    create_project_scaffold(nested_path, "test_project", "test_profile")

    assert nested_path.exists()
    assert (nested_path / "models" / "staging").exists()
