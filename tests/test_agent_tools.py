import logging

import pytest
from fakes import FakeLinkage, fake_app

from inex.agent.tools import TOOL_NAMES, build_tools

ARGS = {
    "count_issues": {"repo_name"},
    "collect_all": {"repo_name", "count", "start_date", "end_date", "direction"},
    "classify_all": {"repo_name", "count", "start_date", "end_date", "direction"},
    "preview_classification": {"repo_name", "count", "start_date", "end_date", "direction"},
    "snapshot_dependencies": {
        "repo_name",
        "npm_package",
        "start_date",
        "end_date",
        "version_range",
        "last_n_versions",
        "version_start",
        "version_end",
    },
    "run_sql": {"query"},
    "export_data": {"query", "format", "filename"},
    "preview_issue_list": {"csv_path", "column"},
    "import_issue_list": {"csv_path", "column", "classify"},
}


@pytest.fixture
def app(settings):
    app = fake_app(settings)
    yield app
    app.close()


def tool(app, name):
    return {t.name: t for t in build_tools(app)}[name]


def test_tool_names_and_arguments(app):
    tools = {t.name: t for t in build_tools(app)}
    assert set(tools) == set(ARGS) == set(TOOL_NAMES)
    assert {name: set(t.args) for name, t in tools.items()} == ARGS


def test_success_is_a_status_dict(app):
    out = tool(app, "count_issues").invoke({"repo_name": "facebook/react"})
    assert out == {"status": "ok", "owner": "facebook", "repo": "react", "issue_count": 14779}


def test_service_error_becomes_an_error_dict(app):
    out = tool(app, "count_issues").invoke({"repo_name": "nope/nope"})
    assert out == {
        "status": "error",
        "code": "repo_not_found",
        "message": "nope/nope was not found on GitHub.",
    }


def test_bad_input_never_reaches_the_service(app):
    out = tool(app, "collect_all").invoke({"repo_name": "react"})
    assert (out["status"], out["code"]) == ("error", "invalid_input")
    assert app.collection.calls == []


def test_inputs_are_normalised_before_the_service(app):
    tool(app, "collect_all").invoke(
        {
            "repo_name": "acme/widget",
            "count": 5,
            "start_date": "2020-01-01",
            "end_date": "2020-01-31",
            "direction": "ASC",
        }
    )
    assert app.collection.calls == [
        (
            "collect",
            "acme",
            "widget",
            {"count": 5, "start": "2020-01-01", "end": "2020-01-31T23:59:59Z", "order": "asc"},
        )
    ]


def test_unexpected_failure_is_internal_error(settings, caplog):
    app = fake_app(settings, linkage=FakeLinkage(error=RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        out = tool(app, "snapshot_dependencies").invoke(
            {"repo_name": "acme/widget", "npm_package": "widget"}
        )
    assert out == {
        "status": "error",
        "code": "internal_error",
        "message": "Unexpected failure; see the log for details.",
    }
    assert "boom" in caplog.text
    app.close()


def test_run_sql_description_states_the_real_cap(app):
    assert "capped at 50 rows" in tool(app, "run_sql").description


def test_run_sql_returns_rows(app):
    out = tool(app, "run_sql").invoke({"query": "SELECT 1 AS x"})
    assert out == {"status": "ok", "columns": ["x"], "rows": [[1]], "truncated": False}
