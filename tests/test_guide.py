"""`model2data guide`: every topic prints, detection picks by the directory, pages stay true."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path

import click
import pytest
import typer
from typer.testing import CliRunner

from model2data import cli
from model2data.cli import GuideTopic, app
from model2data.generate.faker import _infer_by_type
from model2data.model import SPEC_VERSION, validate

runner = CliRunner()
TOPICS = [topic.value for topic in GuideTopic]

# Options the pages name that belong to dbt or uv, not to model2data.
NOT_OURS = {"--profiles-dir", "--dev"}


def _page(topic: str) -> str:
    return resources.files("model2data").joinpath(f"guide/{topic}.md").read_text("utf-8")


def _code(topic: str) -> str:
    """The page's fenced blocks and inline code spans: what an agent would run."""
    page = _page(topic)
    fenced = re.findall(r"```[a-z]*\n(.*?)```", page, flags=re.S)
    inline = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", page, flags=re.S))
    return "\n".join([*fenced, *inline])


def _commands() -> dict[str, click.Command]:
    group = typer.main.get_command(app)
    assert isinstance(group, click.Group)
    return dict(group.commands)


@pytest.mark.parametrize("topic", TOPICS)
def test_every_topic_prints_and_exits_0(topic, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["guide", topic])
    assert result.exit_code == 0, result.output
    assert result.output == _page(topic)
    assert result.output.rstrip().splitlines()[-1].startswith("next: ")
    assert list(tmp_path.iterdir()) == []


def test_help_lists_every_topic():
    result = runner.invoke(app, ["guide", "--help"])
    assert result.exit_code == 0
    for topic in TOPICS:
        assert topic in result.output


def test_top_level_help_points_at_guide():
    result = runner.invoke(app, ["--help"])
    assert "model2data guide" in result.output


def test_empty_directory_picks_setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    first, _, rest = result.output.partition("\n")
    assert first == "# model2data guide: no *.model2data.yml or *.dbml in this directory -> setup"
    assert rest.lstrip("\n") == _page("setup")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["shop.model2data.yml", "shop.model2data.json", "shop.dbml"])
def test_directory_with_a_model_picks_triage(name, tmp_path, monkeypatch):
    (tmp_path / name).write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["guide"])
    assert result.exit_code == 0
    assert result.output.splitlines()[0] == f"# model2data guide: found {name} -> triage"
    assert _page("triage") in result.output


def test_detection_ignores_subdirectories(tmp_path, monkeypatch):
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "shop.model2data.yml").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert "-> setup" in runner.invoke(app, ["guide"]).output.splitlines()[0]


@pytest.mark.parametrize("topic", TOPICS)
def test_every_command_a_page_names_exists(topic):
    commands = _commands()
    named = set(re.findall(r"\bmodel2data ([a-z][a-z-]*)", _code(topic)))
    assert named, topic
    assert named <= set(commands), named - set(commands)


@pytest.mark.parametrize("topic", TOPICS)
def test_every_option_a_page_names_exists(topic):
    commands = list(_commands().values())
    # `metrics` is a group: its subcommands' options count too.
    for command in list(commands):
        if isinstance(command, click.Group):
            commands.extend(command.commands.values())
    options = {
        opt
        for command in commands
        for param in command.params
        for opt in [*param.opts, *param.secondary_opts]
    } | {"--help"}
    named = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", _code(topic))) - NOT_OURS
    assert named <= options, named - options


def _spans(topic: str) -> list[str]:
    """Inline code spans, single- or double-backticked, outside fenced blocks."""
    prose = re.sub(r"```.*?```", "", _page(topic), flags=re.S)
    return [
        (double or single).strip() for double, single in re.findall(r"``(.+?)``|`([^`\n]+)`", prose)
    ]


def test_quoted_output_is_what_the_cli_prints():
    """Every ❌ / ⚠️ / summary line a page quotes appears in the CLI's source, piece by piece.

    `<placeholders>`, `N` and `...` stand for what varies; the text between them must not.
    """
    source = Path(cli.__file__).read_text("utf-8")
    quoted = [
        span
        for topic in TOPICS
        for span in _spans(topic)
        if span.startswith(("❌", "⚠️", "✅", "Columns using"))
    ]
    assert len(quoted) >= 8
    for span in quoted:
        for piece in re.split(r"<[^>]+>[\w.]*|\.\.\.|\bN\b|\b\d+(?:\.\d+)*\b", span):
            piece = piece.strip(" .:")
            if len(piece) > 2:
                assert piece in source, f"{span!r}: {piece!r} is not in cli.py"


def test_spec_version_the_pages_name_is_current():
    for topic in TOPICS:
        for version in re.findall(r"spec (\d+\.\d+\.\d+)", _page(topic)):
            assert version == SPEC_VERSION, topic
        for version in re.findall(r"^model2data: (\S+)$", _page(topic), flags=re.M):
            assert version == SPEC_VERSION, topic


def test_model_keys_in_tune_are_the_schemas():
    schema = json.loads(
        resources.files("model2data").joinpath("spec/model.schema.json").read_text("utf-8")
    )
    defs = schema["$defs"]
    expected = {
        "Column": defs["column"]["properties"],
        "`generate`": defs["generate"]["properties"],
        "Table": defs["table"]["properties"],
        "Top level": schema["properties"],
    }
    section = _page("tune").split("## Model keys", 1)[1].split("```", 1)[0]
    for item in re.split(r"\n- ", section):
        label, _, keys = item.partition(":")
        label = label.strip().lstrip("- ")
        if label in expected:
            named = set(re.findall(r"`([a-z_0-9]+)`", keys))
            wanted = {key for key in expected.pop(label) if not key.startswith("x-")}
            assert named == wanted, (label, wanted - named, named - wanted)
    assert not expected, f"tune lists no keys for {sorted(expected)}"


@pytest.mark.parametrize("topic", ["setup", "tune"])
def test_example_models_validate(topic, tmp_path):
    (block,) = re.findall(r"```yaml\n(.*?)```", _page(topic), flags=re.S)
    path = tmp_path / f"{topic}.model2data.yml"
    path.write_text(block, encoding="utf-8")
    assert [issue for issue in validate(path) if issue.is_error] == []


def test_generator_types_tune_lists_all_work():
    section = _page("tune").split("## Generator types", 1)[1].split("##", 1)[0]
    listed = re.findall(
        r"`([a-z_0-9]+)`", section.split("always work:", 1)[1].split("Any other", 1)[0]
    )
    assert len(listed) > 20
    assert [name for name in listed if _infer_by_type(name) is None] == []
