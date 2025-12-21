from pathlib import Path

import jinja2

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_project_scaffold(dest: Path, project_name: str, profile_name: str) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    # dbt folders
    (dest / "models" / "staging").mkdir(parents=True, exist_ok=True)
    (dest / "seeds" / "raw").mkdir(parents=True, exist_ok=True)
    (dest / "analysis").mkdir(exist_ok=True)
    (dest / "macros").mkdir(exist_ok=True)
    (dest / "tests").mkdir(exist_ok=True)
    (dest / "snapshots").mkdir(exist_ok=True)

    # dbt_project.yml
    _render_template(
        template_name="dbt_project.yml.jinja",
        output_path=dest / "dbt_project.yml",
        context={"project_name": project_name, "profile_name": profile_name},
    )

    # Copy over any macros from templates
    template_macros_dir = Path("model2data/dbt/templates/macros")
    if template_macros_dir.exists():
        for macro_file in template_macros_dir.glob("*.sql"):
            target_file = dest / "macros" / macro_file.name
            if not target_file.exists():
                target_file.write_text(macro_file.read_text())


def create_staging_models(dest: Path, project_name: str) -> None:
    """
    Creates staging models in models/staging/ folder that reference raw seed tables as sources.
    """
    seeds_path = dest / "seeds" / "raw"
    models_path = dest / "models" / "staging"
    models_path.mkdir(parents=True, exist_ok=True)

    for csv_file in seeds_path.glob("*.csv"):
        table_name = csv_file.stem  # keep full seed name, e.g., raw_stories
        model_file = models_path / f"stg_{table_name}.sql"

        if not model_file.exists():
            sql_content = f"""\
-- Auto-generated staging model for {table_name}
select *
from {{{{ source('raw', '{table_name}') }}}}
                """
            model_file.write_text(sql_content)


def create_profiles_yml(dest: Path, profile_name: str) -> None:
    profiles_file = dest / "profiles.yml"
    if profiles_file.exists():
        content = profiles_file.read_text()
        if profile_name in content:
            return
    _render_template(
        template_name="profiles.yml.jinja",
        output_path=profiles_file,
        context={"profile_name": profile_name},
    )


def _render_template(template_name: str, output_path: Path, context: dict) -> None:
    template_path = TEMPLATES_DIR / template_name
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")
    template = jinja2.Template(template_path.read_text())
    output_path.write_text(template.render(**context))
