"""`model2data guide`: every topic prints, detection picks by the directory, pages stay true."""

from __future__ import annotations

import re
from importlib import resources

import click
import pytest
import typer
from typer.testing import CliRunner

from model2data.cli import GuideTopic, app

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
    options = {
        opt
        for command in _commands().values()
        for param in command.params
        for opt in [*param.opts, *param.secondary_opts]
    } | {"--help"}
    named = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", _code(topic))) - NOT_OURS
    assert named <= options, named - options
