"""export_data writes only inside exports/, whatever filename the agent passes."""

import sqlite3

import pytest

from tools.helpers import sqlQuery


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute("CREATE TABLE t(x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    monkeypatch.setattr(sqlQuery, "ro_conn", conn)
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def test_plain_filename_is_written_inside_exports(workdir):
    result = sqlQuery.exportQuery("SELECT x FROM t", "csv", "results")
    assert result["status"] == "ok"
    assert (workdir / "exports" / "results.csv").read_text(encoding="utf-8").split() == [
        "x",
        "1",
        "2",
    ]


def test_relative_path_cannot_leave_exports(workdir):
    result = sqlQuery.exportQuery("SELECT x FROM t", "csv", "../escaped")
    assert result["status"] == "error"
    assert result["code"] == "invalid_filename"
    assert not (workdir / "escaped.csv").exists()


def test_absolute_path_is_rejected(workdir, tmp_path):
    target = tmp_path / "absolute"
    result = sqlQuery.exportQuery("SELECT x FROM t", "csv", str(target))
    assert result["status"] == "error"
    assert result["code"] == "invalid_filename"
    assert not (tmp_path / "absolute.csv").exists()
