"""Storage guarantees: the migration, run-scoped resume, and the invariant that a
failed attempt is never stored as a taxonomy label."""
import pytest

from conftest import FULL_PROBS

RUN = dict(model="google_genai:gemini-2.5-pro", prompt="inex@abc123+t2", temp=0.2)
OTHER = dict(model="qwen3:30b", prompt="inex@abc123+t2", temp=0.2)


def save(db, issue_id, label=None, status="ok", probs=None, **over):
    kw = dict(RUN)
    kw.update(over)
    return db.save_classification(issue_id, {
        "classification": label,
        "classification_probabilities": probs if probs is not None else (FULL_PROBS if label else {}),
        "classification_raw_response": "{}",
        "status": status,
    }, classifiedat="2026-10-26T00:00:00", **kw)


class TestMigration:
    def test_outcome_columns_exist(self, db):
        cols = [r[1] for r in db.cursor.execute("PRAGMA table_info(classifications)")]
        for c in ("status", "error", "validation_flags", "argmax_label", "attempts"):
            assert c in cols

    def test_migration_is_idempotent(self, db_path):
        from tools.helpers.database import InExTool
        InExTool(db_path)
        InExTool(db_path)  # re-running must not raise on existing columns


class TestFailuresAreNotLabels:
    def test_failure_row_has_null_classification(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        row = db.cursor.execute(
            "SELECT classification, status FROM classifications WHERE issue_id=?",
            (ids[0],)).fetchone()
        assert row == (None, "api_error")

    def test_storing_a_label_with_a_failed_status_is_refused(self, seeded):
        db, _, ids = seeded
        with pytest.raises(ValueError):
            save(db, ids[0], label="Unknown", status="parse_error")

    def test_invariant_holds_across_mixed_outcomes(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[1], label=None, status="truncated")
        save(db, ids[2], label=None, status="empty_response")
        bad = db.cursor.execute(
            "SELECT COUNT(*) FROM classifications "
            "WHERE status IS NOT NULL AND status<>'ok' AND classification IS NOT NULL"
        ).fetchone()[0]
        assert bad == 0


class TestResumeIsRunScoped:
    def test_other_model_sees_everything_as_unclassified(self, seeded):
        db, pid, ids = seeded
        for i in ids:
            save(db, i, label="Intrinsic")
        assert db.getUnclassifiedInWindow(pid, **_keys(RUN)) == []
        assert len(db.getUnclassifiedInWindow(pid, **_keys(OTHER))) == 3

    def test_failed_rows_are_retried(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        remaining = [r[0] for r in db.getUnclassifiedInWindow(pid, **_keys(RUN))]
        assert ids[0] in remaining

    def test_success_removes_it_from_the_queue(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        save(db, ids[0], label="Intrinsic")
        remaining = [r[0] for r in db.getUnclassifiedInWindow(pid, **_keys(RUN))]
        assert ids[0] not in remaining

    def test_legacy_null_status_counts_as_done(self, seeded):
        db, pid, ids = seeded
        db.cursor.execute(
            "INSERT INTO classifications(issue_id, classification, model, prompt_version, temperature) "
            "VALUES(?,?,?,?,?)", (ids[0], "Intrinsic", RUN["model"], RUN["prompt"], RUN["temp"]))
        db.connection.commit()
        remaining = [r[0] for r in db.getUnclassifiedInWindow(pid, **_keys(RUN))]
        assert ids[0] not in remaining

    def test_get_issues_by_ids_is_scoped_too(self, seeded):
        db, _, ids = seeded
        for i in ids:
            save(db, i, label="Intrinsic")
        assert db.getIssuesByIds(ids, **_keys(RUN)) == []
        assert len(db.getIssuesByIds(ids, **_keys(OTHER))) == 3

    def test_count_matches_the_listing(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label="Intrinsic")
        assert db.countUnclassifiedInWindow(pid, **_keys(RUN)) == 2


class TestUpsert:
    def test_row_id_is_stable_across_resave(self, seeded):
        db, _, ids = seeded
        first = save(db, ids[0], label=None, status="api_error")
        second = save(db, ids[0], label="Intrinsic")
        assert first == second

    def test_resave_does_not_duplicate(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label=None, status="api_error")
        save(db, ids[0], label="Intrinsic")
        n = db.cursor.execute(
            "SELECT COUNT(*) FROM classifications WHERE issue_id=?", (ids[0],)).fetchone()[0]
        assert n == 1

    def test_different_prompt_version_adds_a_row(self, seeded):
        db, _, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[0], label="Extrinsic", prompt="inex@DIFFERENT+t2")
        n = db.cursor.execute(
            "SELECT COUNT(*) FROM classifications WHERE issue_id=?", (ids[0],)).fetchone()[0]
        assert n == 2

    def test_returned_id_is_for_the_right_run(self, seeded):
        db, _, ids = seeded
        a = save(db, ids[0], label="Intrinsic")
        b = save(db, ids[0], label="Extrinsic", **{"model": "qwen3:30b"})
        assert a != b


class TestAggregatesExcludeFailures:
    def test_failure_only_project_has_no_classifications(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="parse_error")
        assert db.projectHasClassifications(pid) is False
        assert db.getClassificationIssueWindow(pid) == []

    def test_one_success_is_enough(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label=None, status="parse_error")
        save(db, ids[1], label="Intrinsic")
        assert db.projectHasClassifications(pid) is True
        assert len(db.getClassificationIssueWindow(pid)) == 1

    def test_two_prompt_versions_do_not_double_count_issues(self, seeded):
        db, pid, ids = seeded
        save(db, ids[0], label="Intrinsic")
        save(db, ids[0], label="Extrinsic", prompt="inex@DIFFERENT+t2")
        assert len(db.getClassificationIssueWindow(pid)) == 1


def _keys(run):
    return {"model": run["model"], "prompt_version": run["prompt"],
            "temperature": run["temp"]}
