"""Parallel classification.

Three races had to be closed for this to be safe: shared tenacity retry state,
duplicate client construction, and concurrent SQLite writes.
"""
import threading
import time

import pytest
from conftest import FULL_PROBS, PROMPT, row_for


@pytest.fixture
def rows():
    return [row_for(i, number=100 + i) for i in range(1, 13)]


def _ok():
    from tools.helpers.classify import ParsedResult
    return ParsedResult(label="Intrinsic", probs=dict(FULL_PROBS), raw_text="{}")


class RecordingDB:
    """Stands in for InExTool, capturing writes from whichever thread makes them."""

    def __init__(self):
        self.saved = []
        self.threads = set()
        self._lock = threading.Lock()

    def save_classification(self, issue_id, data, **kw):
        self.threads.add(threading.current_thread().name)
        with self._lock:
            self.saved.append((issue_id, data["status"], data["classification"]))
        return len(self.saved)


def make(workers, classify_one):
    from tools.helpers.classify import Classify
    cl = Classify("google_genai:gemini-2.5-pro", 0.2, PROMPT, workers=workers)
    cl.retry_wait = lambda *a, **k: 0
    cl._cloudStructured = lambda: None      # never build a real client
    cl.classifyOne = classify_one
    return cl


class TestParallelCorrectness:
    def test_same_outcome_as_serial(self, rows):
        serial = make(1, lambda issue: _ok())
        db1 = RecordingDB()
        t1 = serial._classifyRows("acme", "widget", rows, db1)

        parallel = make(8, lambda issue: _ok())
        db2 = RecordingDB()
        t2 = parallel._classifyRows("acme", "widget", rows, db2)

        assert t1 == t2 == {"ok": len(rows)}
        assert sorted(db1.saved) == sorted(db2.saved)

    def test_every_issue_persisted_exactly_once(self, rows):
        db = RecordingDB()
        make(8, lambda issue: _ok())._classifyRows("acme", "widget", rows, db)
        ids = [s[0] for s in db.saved]
        assert sorted(ids) == sorted(r[0] for r in rows)
        assert len(ids) == len(set(ids))

    def test_work_is_actually_spread(self, rows):
        def slow(issue):
            time.sleep(0.05)
            return _ok()
        db = RecordingDB()
        make(8, slow)._classifyRows("acme", "widget", rows, db)
        assert len(db.threads) > 1

    def test_single_worker_stays_on_the_calling_thread(self, rows):
        db = RecordingDB()
        make(1, lambda issue: _ok())._classifyRows("acme", "widget", rows, db)
        assert db.threads == {threading.current_thread().name}

    def test_worker_count_is_capped_by_the_batch(self, rows):
        db = RecordingDB()
        cl = make(64, lambda issue: _ok())
        cl._classifyRows("acme", "widget", rows[:2], db)
        assert len(db.saved) == 2


class TestFailureIsolation:
    def test_one_exception_does_not_kill_the_batch(self, rows):
        def flaky(issue):
            if issue["number"] % 3 == 0:
                raise RuntimeError("boom")
            return _ok()
        db = RecordingDB()
        tally = make(8, flaky)._classifyRows("acme", "widget", rows, db)
        assert sum(tally.values()) == len(rows)
        assert tally.get("api_error", 0) > 0 and tally.get("ok", 0) > 0

    def test_crashed_issues_are_stored_without_a_label(self, rows):
        def always_boom(issue):
            raise RuntimeError("boom")
        db = RecordingDB()
        make(8, always_boom)._classifyRows("acme", "widget", rows, db)
        assert all(status == "api_error" and label is None
                   for _id, status, label in db.saved)

    def test_mixed_statuses_all_accounted_for(self, rows):
        from tools.helpers.classify import ParsedResult

        def mixed(issue):
            n = issue["number"] % 3
            if n == 0:
                raise RuntimeError("boom")
            if n == 1:
                return ParsedResult(status="truncated", error="MAX_TOKENS", raw_text="cut")
            return _ok()
        db = RecordingDB()
        tally = make(8, mixed)._classifyRows("acme", "widget", rows, db)
        assert sum(tally.values()) == len(rows)
        assert len(tally) == 3


class TestRaces:
    def test_retry_state_is_not_shared_between_workers(self, rows):
        """A single tenacity Retrying object carries per-run state; workers sharing
        one would interfere with each other's attempt counts."""
        import collections

        from tools.helpers.classify import AttemptFailure, Classify

        seen = collections.Counter()
        lock = threading.Lock()

        class Retryer(Classify):
            def callOllama(self, prompt):
                import re
                n = int(re.search(r"Issue Number:\*\* #(\d+)", prompt).group(1))
                with lock:
                    seen[n] += 1
                    count = seen[n]
                if count < 2:
                    raise AttemptFailure("parse_error", "transient", raw_text="p", retryable=True)
                return _ok()

        cl = Retryer("qwen3:30b", 0.2, PROMPT, workers=8)
        cl.retry_wait = lambda *a, **k: 0
        db = RecordingDB()
        tally = cl._classifyRows("acme", "widget", rows, db)
        assert tally == {"ok": len(rows)}
        assert all(v == 2 for v in seen.values()), "each issue retried exactly once"

    def test_client_is_built_once_under_concurrency(self, rows):
        from tools.helpers.classify import Classify
        builds = {"n": 0}

        class Counting(Classify):
            def _cloudStructured(self):
                if self._structured is None:
                    with self._clientLock:
                        if self._structured is None:
                            builds["n"] += 1
                            time.sleep(0.05)
                            self._structured = "CLIENT"
                return self._structured

            def classifyOne(self, issue):
                self._cloudStructured()
                return _ok()

        cl = Counting("google_genai:gemini-2.5-pro", 0.2, PROMPT, workers=8)
        cl._classifyRows("acme", "widget", rows, RecordingDB())
        assert builds["n"] == 1

    def test_concurrent_writes_to_real_sqlite(self, db, seeded, rows):
        """The write lock: many workers upserting into one connection."""
        _db, pid, ids = seeded
        many = [row_for(i, number=200 + i) for i in ids]
        cl = make(8, lambda issue: _ok())
        tally = cl._classifyRows("acme", "widget", many, _db)
        assert tally == {"ok": len(many)}
        n = _db.cursor.execute("SELECT COUNT(*) FROM classifications").fetchone()[0]
        assert n == len(many)


class TestSummary:
    def test_failures_are_surfaced_in_the_message(self, classifier):
        msg = classifier._summarise("acme", "widget", {"ok": 5, "api_error": 2})
        assert "5" in msg and "2 failed" in msg

    def test_clean_run_says_nothing_about_failures(self, classifier):
        msg = classifier._summarise("acme", "widget", {"ok": 5})
        assert "failed" not in msg
