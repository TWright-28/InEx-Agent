import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from fakes import FakeCollection, fake_app

from inex.cli import main

REPO = Path(__file__).resolve().parents[1]
COMMANDS = (
    "count",
    "collect",
    "classify",
    "link",
    "import",
    "load",
    "query",
    "export",
    "chat",
    "db",
)


@pytest.fixture
def built():
    return []


@pytest.fixture
def cli(settings, tmp_path, monkeypatch, built):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("inex.cli.configure_logging", lambda path: None)
    services = {}

    def build(cli_settings):
        app = fake_app(settings, **services)
        built.append((cli_settings, app))
        return app

    def run(argv, **fakes):
        services.clear()
        services.update(fakes)
        return main(argv, build=build)

    return run


def test_count_prints_the_summary(cli, capsys):
    assert cli(["count", "facebook/react"]) == 0
    assert capsys.readouterr().out == "facebook/react has 14779 issues.\n"


def test_collect_summary(cli, capsys):
    assert cli(["collect", "acme/widget", "--count", "5"]) == 0
    assert capsys.readouterr().out == (
        "Collected 10 issues from acme/widget (0 already in the database).\n"
    )


def test_json_output_is_valid_json_for_every_command(cli, capsys):
    for argv in (
        ["count", "facebook/react"],
        ["export", "SELECT 1 AS x", "--name", "r"],
        ["db", "init"],
        ["query", "SELECT 1 AS x"],
    ):
        assert cli(["--json", *argv]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_json_error_shape(cli, capsys):
    assert cli(["--json", "count", "nope/nope"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "code": "repo_not_found",
        "message": "nope/nope was not found on GitHub.",
    }


def test_invalid_input_exits_1_with_message(cli, capsys, built):
    assert cli(["collect", "react"]) == 1
    assert capsys.readouterr().err.startswith("error: ")
    assert built[0][1].collection.calls == []


def test_preview_flag_calls_preview_not_classify(cli, capsys, built):
    assert cli(["classify", "acme/widget", "--preview", "--oldest-first"]) == 0
    calls = built[0][1].classification.calls
    assert [c[0] for c in calls] == ["preview"]
    assert calls[0][3]["order"] == "asc"
    assert capsys.readouterr().out == (
        "10 of 10 unclassified issues in acme/widget would be classified with m (p).\n"
    )


def test_query_prints_rows(cli, capsys):
    assert cli(["query", "SELECT 1 AS x, 'a' AS y"]) == 0
    assert capsys.readouterr().out == "x\ty\n1\ta\n"


def test_db_reset_requires_yes(cli):
    assert cli(["db", "reset"]) == 2


def test_db_flag_reaches_settings(cli, built):
    cli(["--db", "x/y.db", "db", "init"])
    assert built[0][0].database == Path("x/y.db")


def test_load_requires_model_and_prompt_version(cli):
    assert cli(["load", "x.jsonl"]) == 2


def test_ctrl_c_exits_130(cli, capsys):
    assert (
        cli(["collect", "acme/widget"], collection=FakeCollection(error=KeyboardInterrupt())) == 130
    )
    err = capsys.readouterr().err
    assert err == "interrupted\n"


def test_unexpected_error_exits_1_and_logs(cli, capsys, caplog):
    with caplog.at_level(logging.ERROR):
        code = cli(
            ["collect", "acme/widget"], collection=FakeCollection(error=RuntimeError("boom"))
        )
    assert code == 1
    assert capsys.readouterr().err.startswith("error: unexpected failure")
    assert "boom" in caplog.text


def test_module_entry_point_lists_every_command():
    result = subprocess.run(
        [sys.executable, "-m", "inex", "--help"], cwd=REPO, capture_output=True, text=True
    )
    assert result.returncode == 0
    for command in COMMANDS:
        assert command in result.stdout


def test_logs_never_reach_stdout(tmp_path, capsys):
    from inex.cli import configure_logging

    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": "INFO", "handlers": ["stdout"]},
    }
    path = tmp_path / "logging.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging(path)
        logging.getLogger("inex.test").info("progress line")
        captured = capsys.readouterr()
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
    assert "progress line" not in captured.out
    assert "progress line" in captured.err
