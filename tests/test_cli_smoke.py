import subprocess
from pathlib import Path

import pandas as pd


def test_cli_generate_smoke(tmp_path):
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
