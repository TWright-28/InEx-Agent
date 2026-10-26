"""export_data writes only inside exports/, whatever filename the agent passes."""

import sqlite3

import pytest

from inex.store.database import Store
from tools.helpers import sqlQuery


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "q.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE t(x INTEGER)")
    raw.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    raw.commit()
    raw.close()
    return Store(path)


@pytest.fixture
def workdir(tmp_path, monkeypatch, store):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


def test_plain_filename_is_written_inside_exports(workdir, store):
    result = sqlQuery.exportQuery(store, "SELECT x FROM t", "csv", "results")
    assert result["status"] == "ok"
    assert (workdir / "exports" / "results.csv").read_text(encoding="utf-8").split() == [
        "x",
        "1",
        "2",
    ]


def test_relative_path_cannot_leave_exports(workdir, store):
    result = sqlQuery.exportQuery(store, "SELECT x FROM t", "csv", "../escaped")
    assert result["status"] == "error"
    assert result["code"] == "invalid_filename"
    assert not (workdir / "escaped.csv").exists()


def test_absolute_path_is_rejected(workdir, tmp_path, store):
    target = tmp_path / "absolute"
    result = sqlQuery.exportQuery(store, "SELECT x FROM t", "csv", str(target))
    assert result["status"] == "error"
    assert result["code"] == "invalid_filename"
    assert not (tmp_path / "absolute.csv").exists()
