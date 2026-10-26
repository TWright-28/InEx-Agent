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
    from inex.services.classification import ParsedResult

    return ParsedResult(label="Intrinsic", probs=dict(FULL_PROBS), raw_text="{}")


class RecordingDB:
    """Stands in for Store, capturing writes from whichever thread makes them."""

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
    from inex.services.classification import ClassificationService

    cl = ClassificationService(None, PROMPT, "google_genai:gemini-2.5-pro", 0.2, workers=workers)
    cl.retry_wait = lambda *a, **k: 0
    cl._cloudStructured = lambda: None  # never build a real client
    cl.classifyOne = classify_one
    return cl


def run(cl, rows, store):
    cl.store = store
    return cl._classifyRows("acme", "widget", rows)


class TestParallelCorrectness:
    def test_same_outcome_as_serial(self, rows):
        serial = make(1, lambda issue: _ok())
        db1 = RecordingDB()
        t1 = run(serial, rows, db1)

        parallel = make(8, lambda issue: _ok())
        db2 = RecordingDB()
        t2 = run(parallel, rows, db2)

        assert t1 == t2 == {"ok": len(rows)}
        assert sorted(db1.saved) == sorted(db2.saved)

    def test_every_issue_persisted_exactly_once(self, rows):
        db = RecordingDB()
        run(make(8, lambda issue: _ok()), rows, db)
        ids = [s[0] for s in db.saved]
        assert sorted(ids) == sorted(r[0] for r in rows)
        assert len(ids) == len(set(ids))

    def test_work_is_actually_spread(self, rows):
        def slow(issue):
            time.sleep(0.05)
            return _ok()

        db = RecordingDB()
        run(make(8, slow), rows, db)
        assert len(db.threads) > 1

    def test_single_worker_stays_on_the_calling_thread(self, rows):
        db = RecordingDB()
        run(make(1, lambda issue: _ok()), rows, db)
        assert db.threads == {threading.current_thread().name}

    def test_worker_count_is_capped_by_the_batch(self, rows):
        db = RecordingDB()
        cl = make(64, lambda issue: _ok())
        run(cl, rows[:2], db)
        assert len(db.saved) == 2


class TestFailureIsolation:
    def test_one_exception_does_not_kill_the_batch(self, rows):
        def flaky(issue):
            if issue["number"] % 3 == 0:
                raise RuntimeError("boom")
            return _ok()

        db = RecordingDB()
        tally = run(make(8, flaky), rows, db)
        assert sum(tally.values()) == len(rows)
        assert tally.get("api_error", 0) > 0 and tally.get("ok", 0) > 0

    def test_crashed_issues_are_stored_without_a_label(self, rows):
        def always_boom(issue):
            raise RuntimeError("boom")

        db = RecordingDB()
        run(make(8, always_boom), rows, db)
        assert all(status == "api_error" and label is None for _id, status, label in db.saved)

    def test_mixed_statuses_all_accounted_for(self, rows):
        from inex.services.classification import ParsedResult

        def mixed(issue):
            n = issue["number"] % 3
            if n == 0:
                raise RuntimeError("boom")
            if n == 1:
                return ParsedResult(status="truncated", error="MAX_TOKENS", raw_text="cut")
            return _ok()

        db = RecordingDB()
        tally = run(make(8, mixed), rows, db)
        assert sum(tally.values()) == len(rows)
        assert len(tally) == 3


class TestRaces:
    def test_retry_state_is_not_shared_between_workers(self, rows):
        """A single tenacity Retrying object carries per-run state; workers sharing
        one would interfere with each other's attempt counts."""
        import collections

        from inex.services.classification import AttemptFailure, ClassificationService

        seen = collections.Counter()
        lock = threading.Lock()

        class Retryer(ClassificationService):
            def callOllama(self, prompt):
                import re

                n = int(re.search(r"Issue Number:\*\* #(\d+)", prompt).group(1))
                with lock:
                    seen[n] += 1
                    count = seen[n]
                if count < 2:
                    raise AttemptFailure("parse_error", "transient", raw_text="p", retryable=True)
                return _ok()

        cl = Retryer(None, PROMPT, "qwen3:30b", 0.2, workers=8)
        cl.retry_wait = lambda *a, **k: 0
        db = RecordingDB()
        tally = run(cl, rows, db)
        assert tally == {"ok": len(rows)}
        assert all(v == 2 for v in seen.values()), "each issue retried exactly once"

    def test_client_is_built_once_under_concurrency(self, rows):
        from inex.services.classification import ClassificationService

        builds = {"n": 0}

        class Counting(ClassificationService):
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

        cl = Counting(None, PROMPT, "google_genai:gemini-2.5-pro", 0.2, workers=8)
        run(cl, rows, RecordingDB())
        assert builds["n"] == 1

    def test_concurrent_writes_to_real_sqlite(self, db, seeded, rows):
        """The write lock: many workers upserting into one connection."""
        _db, pid, ids = seeded
        many = [row_for(i, number=200 + i) for i in ids]
        cl = make(8, lambda issue: _ok())
        tally = run(cl, many, _db)
        assert tally == {"ok": len(many)}
        n = _db.fetch_readonly("SELECT COUNT(*) FROM classifications")[1][0][0]
        assert n == len(many)


class TestResults:
    def test_classify_reports_failures_by_status(self, seeded):
        from inex.services.classification import ClassificationService, ClassifyResult, ParsedResult

        db, _, _ = seeded
        outcomes = iter([_ok(), ParsedResult(status="api_error", error="boom"), _ok()])
        cl = ClassificationService(db, PROMPT, "qwen3:30b", 0.2)
        cl.classifyOne = lambda issue: next(outcomes)
        assert cl.classify("acme", "widget") == ClassifyResult(
            "acme", "widget", classified=2, failed={"api_error": 1}
        )

    def test_clean_run_has_no_failures(self, seeded):
        from inex.services.classification import ClassificationService, ClassifyResult

        db, _, _ = seeded
        cl = ClassificationService(db, PROMPT, "qwen3:30b", 0.2)
        cl.classifyOne = lambda issue: _ok()
        assert cl.classify("acme", "widget") == ClassifyResult("acme", "widget", 3, {})

    def test_unknown_project_raises(self, db):
        from inex.errors import InexError
        from inex.services.classification import ClassificationService

        cl = ClassificationService(db, PROMPT, "qwen3:30b", 0.2)
        for call in (cl.classify, cl.preview):
            with pytest.raises(InexError) as e:
                call("nope", "nope")
            assert e.value.code == "project_not_found"

    def test_preview_counts_backlog_and_caps_the_batch(self, seeded):
        from inex.services.classification import ClassificationService

        db, _, _ = seeded
        preview = ClassificationService(db, PROMPT, "qwen3:30b", 0.2).preview(
            "acme", "widget", count=2
        )
        assert (preview.would_classify, preview.total_remaining, len(preview.sample)) == (2, 3, 2)

    def test_classify_ids_only_touches_the_given_issues(self, seeded):
        from inex.services.classification import ClassificationService

        db, _, ids = seeded
        cl = ClassificationService(db, PROMPT, "qwen3:30b", 0.2)
        cl.classifyOne = lambda issue: _ok()
        result = cl.classify_ids("acme", "widget", ids[:2])
        assert (result.classified, result.failed) == (2, {})
        assert cl.classify("acme", "widget").classified == 1

    def test_ollama_base_is_used(self, monkeypatch):
        import inex.services.classification as mod

        seen = []

        class EmptyResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"response": ""}

        monkeypatch.setattr(
            mod.requests, "post", lambda url, **k: seen.append(url) or EmptyResponse()
        )
        cl = mod.ClassificationService(
            None, PROMPT, "qwen3:30b", 0.2, retries=1, ollama_base="http://example:1234"
        )
        assert cl.callModel("x").status == "empty_response"
        assert seen == ["http://example:1234/api/generate"]
