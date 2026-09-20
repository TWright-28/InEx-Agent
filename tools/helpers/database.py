import sqlite3
from config import DB_PATH
from datetime import datetime
import json


class InExTool:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.connection.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            added_at TEXT,
            UNIQUE(owner, repo)
            )""")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS issues(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            issue_number INTEGER NOT NULL,
            github_id INTEGER,
            url TEXT,
            title TEXT,
            body TEXT,
            state TEXT,
            state_reason TEXT,
            locked INTEGER,
            created_at TEXT,
            closed_at TEXT,
            updated_at TEXT,
            comments_count INTEGER,
            raw_data TEXT,
            FOREIGN KEY (project_id) REFERENCES projects (id),
            UNIQUE(project_id, issue_number)
            )""")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS classifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL,
            classification TEXT,
            classification_probabilities TEXT,
            classification_raw_response TEXT,
            model TEXT,
            prompt_version TEXT,
            temperature REAL,
            classified_at TEXT,
            FOREIGN KEY (issue_id) REFERENCES issues(id),
            UNIQUE(issue_id, model, prompt_version, temperature)
        )""")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            package_name TEXT NOT NULL,
            version TEXT NOT NULL,
            published_at TEXT,
            direct_count INTEGER,
            peer_count INTEGER,
            dev_count INTEGER,
            raw_manifest TEXT,
            snapshotted_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            UNIQUE(project_id, version)
        )""")

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS version_dependencies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            dep_name TEXT NOT NULL,
            dep_kind TEXT NOT NULL CHECK(dep_kind IN ('direct','peer','dev')),
            dep_version_range TEXT,
            resolved_version TEXT,
            depth INTEGER,
            root_dep TEXT,
            FOREIGN KEY (version_id) REFERENCES versions(id)
        )""")

        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_version ON version_dependencies(version_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_name ON version_dependencies(dep_name)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_kind ON version_dependencies(dep_kind)")

        for alter in [
            "ALTER TABLE projects ADD COLUMN package_name TEXT",
            "ALTER TABLE issues ADD COLUMN version_id INTEGER REFERENCES versions(id)",
            "ALTER TABLE versions ADD COLUMN peer_count INTEGER",
            "ALTER TABLE version_dependencies ADD COLUMN resolved_version TEXT",
            "ALTER TABLE version_dependencies ADD COLUMN root_dep TEXT",
            # Outcome of the classification attempt. 'ok' means the label below came
            # from the model; anything else means it did not, and `classification`
            # is NULL. Legacy rows predate this column and are NULL -> treated as done.
            "ALTER TABLE classifications ADD COLUMN status TEXT",
            "ALTER TABLE classifications ADD COLUMN error TEXT",
            "ALTER TABLE classifications ADD COLUMN validation_flags TEXT",
            "ALTER TABLE classifications ADD COLUMN argmax_label TEXT",
            "ALTER TABLE classifications ADD COLUMN attempts INTEGER",
        ]:
            try:
                self.cursor.execute(alter)
            except sqlite3.OperationalError:
                pass

        self.connection.commit()

    def save_project(self, owner, repo):
        cur = self.connection.cursor()
        cur.execute("INSERT OR IGNORE INTO projects(owner, repo, added_at) VALUES(?,?,?)", (owner, repo, datetime.now().isoformat()))
        self.connection.commit()
        cur.execute("SELECT id FROM projects WHERE owner = ? AND repo = ?", (owner, repo))
        return cur.fetchone()[0]

    def save_issue(self, project_id, issue_data):
        cur = self.connection.cursor()
        issue_number = issue_data.get("number")
        github_id = issue_data.get("id")
        url = issue_data.get("url")
        title = issue_data.get("title")
        body = issue_data.get("body")
        state = issue_data.get("state")
        state_reason = issue_data.get("state_reason")
        locked = issue_data.get("locked")
        created_at = issue_data.get("created_at")
        closed_at = issue_data.get("closed_at")
        updated_at = issue_data.get("updated_at")
        comments_count = issue_data.get("comments_count")
        raw_data = json.dumps({
            "timestamp_metrics": issue_data.get("timestamp_metrics"),
            "participant_metrics": issue_data.get("participant_metrics"),
            "reopen_metrics": issue_data.get("reopen_metrics"),
            "author": issue_data.get("author"),
            "closed_by": issue_data.get("closed_by"),
            "labels": issue_data.get("labels"),
            "assignees": issue_data.get("assignees"),
            "milestone": issue_data.get("milestone"),
            "comments": issue_data.get("comments"),
            "comments_md": issue_data.get("comments_md"),
            "comments_text": issue_data.get("comments_text"),
            "maintainer_comments": issue_data.get("maintainer_comments"),
            "closing_pr": issue_data.get("closing_pr"),
            "closing_commit": issue_data.get("closing_commit"),
        })
        cur.execute(
            "INSERT OR IGNORE INTO issues(project_id, issue_number, github_id, url, title, body, state, state_reason, locked, created_at, closed_at, updated_at, comments_count, raw_data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project_id, issue_number, github_id, url, title, body, state, state_reason, locked, created_at, closed_at, updated_at, comments_count, raw_data))
        self.connection.commit()
        cur.execute("SELECT id FROM issues WHERE project_id = ? AND issue_number = ?", (project_id, issue_number))
        return cur.fetchone()[0]

    def save_classification(self, issue_id, classification_data, model, prompt, temp, classifiedat):
        """Persist one classification attempt.

        `classification_data` may carry the outcome fields `status`, `error`,
        `validation_flags`, `argmax_label` and `attempts`. When `status` is
        anything other than 'ok' the caller must leave `classification` as None:
        a failed attempt is never recorded as a taxonomy label.
        """
        cur = self.connection.cursor()
        classification = classification_data.get("classification")
        probabilites = json.dumps(classification_data.get("classification_probabilities"))
        classification_raw = classification_data.get("classification_raw_response")
        status = classification_data.get("status", "ok")
        error = classification_data.get("error")
        flags = classification_data.get("validation_flags")
        flags = json.dumps(flags) if flags else None
        argmax_label = classification_data.get("argmax_label")
        attempts = classification_data.get("attempts")

        if status != "ok" and classification is not None:
            raise ValueError(
                f"refusing to store classification {classification!r} with status {status!r}")

        # Upsert rather than INSERT OR REPLACE: the latter deletes and reinserts,
        # so classifications.id changes on every re-save.
        cur.execute("""INSERT INTO classifications(
                issue_id, classification, classification_probabilities,
                classification_raw_response, model, prompt_version, temperature,
                classified_at, status, error, validation_flags, argmax_label, attempts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(issue_id, model, prompt_version, temperature) DO UPDATE SET
                classification=excluded.classification,
                classification_probabilities=excluded.classification_probabilities,
                classification_raw_response=excluded.classification_raw_response,
                classified_at=excluded.classified_at,
                status=excluded.status,
                error=excluded.error,
                validation_flags=excluded.validation_flags,
                argmax_label=excluded.argmax_label,
                attempts=excluded.attempts""",
            (issue_id, classification, probabilites, classification_raw, model, prompt,
             temp, classifiedat, status, error, flags, argmax_label, attempts))
        self.connection.commit()
        cur.execute(
            "SELECT id FROM classifications WHERE issue_id=? AND model=? AND prompt_version=? AND temperature=?",
            (issue_id, model, prompt, temp))
        return cur.fetchone()[0]

    def setPackageName(self, project_id, package_name):
        cur = self.connection.cursor()
        cur.execute("UPDATE projects SET package_name = ? WHERE id = ?", (package_name, project_id))
        self.connection.commit()

    def getPackageName(self, project_id):
        cur = self.connection.cursor()
        cur.execute("SELECT package_name FROM projects WHERE id = ?", (project_id,))
        row = cur.fetchone()
        return row[0] if row else None

    # Failed attempts live in `classifications` too, so every downstream aggregate
    # must exclude them or it counts non-results as results. NULL = legacy row.
    _SUCCEEDED = "(c.status IS NULL OR c.status = 'ok')"

    def projectHasClassifications(self, project_id):
        cur = self.connection.cursor()
        cur.execute("SELECT 1 FROM classifications c INNER JOIN issues i ON i.id = c.issue_id "
                    f"WHERE i.project_id = ? AND {self._SUCCEEDED} LIMIT 1", (project_id,))
        return cur.fetchone() is not None

    def getClassificationIssueWindow(self, project_id, start=None, end=None):
        cur = self.connection.cursor()
        # DISTINCT: an issue may carry one row per prompt_version now that
        # prompt_version identifies content, and this returns issues, not rows.
        q = ("SELECT DISTINCT i.id, i.issue_number, i.created_at, i.version_id FROM issues i "
             "INNER JOIN classifications c ON c.issue_id = i.id "
             f"WHERE i.project_id = ? AND {self._SUCCEEDED}")
        params = [project_id]
        if start:
            q += " AND i.created_at >= ?"
            params.append(start)
        if end:
            q += " AND i.created_at <= ?"
            params.append(end)
        q += " ORDER BY i.created_at"
        cur.execute(q, params)
        return cur.fetchall()

    def getVersionByString(self, project_id, version):
        cur = self.connection.cursor()
        cur.execute("SELECT id FROM versions WHERE project_id = ? AND version = ?", (project_id, version))
        row = cur.fetchone()
        return row[0] if row else None

    def saveVersion(self, project_id, package_name, version, published_at, counts, raw_manifest):
        cur = self.connection.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO versions(project_id, package_name, version, published_at, direct_count, peer_count, dev_count, raw_manifest, snapshotted_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, package_name, version, published_at,
             counts.get("direct"), counts.get("peer"), counts.get("dev"),
             json.dumps(raw_manifest) if raw_manifest else None,
             datetime.now().isoformat()))
        self.connection.commit()
        cur.execute("SELECT id FROM versions WHERE project_id = ? AND version = ?", (project_id, version))
        return cur.fetchone()[0]

    def save_dependencies(self, version_id, rows):
        cur = self.connection.cursor()
        cur.executemany("INSERT INTO version_dependencies(version_id, dep_name, dep_kind, dep_version_range, resolved_version, depth, root_dep) VALUES (?,?,?,?,?,?,?)", [(version_id, *r) for r in rows])
        self.connection.commit()

    def link_issue_to_version(self, issue_id, version_id):
        cur = self.connection.cursor()
        cur.execute("UPDATE issues SET version_id = ? WHERE id = ?", (version_id, issue_id))
        self.connection.commit()

    # An issue counts as already done for a run only if it has a row for THAT
    # model/prompt/temperature which did not fail. The predicates live in the ON
    # clause on purpose: moving them to WHERE would turn the LEFT JOIN into an
    # inner join and match nothing. status IS NULL means a legacy row, treated as done.
    _DONE_JOIN = """LEFT JOIN classifications c
                           ON c.issue_id = i.id
                          AND c.model = ? AND c.prompt_version = ? AND c.temperature = ?
                          AND (c.status IS NULL OR c.status = 'ok')"""

    _ISSUE_COLS = ("i.id, i.issue_number, i.title, i.body, i.state, i.state_reason, "
                   "i.created_at, i.closed_at, i.raw_data")

    def getIssuesByIds(self, issue_ids, model, prompt_version, temperature):
        if not issue_ids:
            return []
        cur = self.connection.cursor()
        placeholders = ",".join("?" * len(issue_ids))
        q = f"""SELECT {self._ISSUE_COLS}
                FROM issues i {self._DONE_JOIN}
                WHERE i.id IN ({placeholders}) AND c.id IS NULL"""
        cur.execute(q, [model, prompt_version, temperature, *issue_ids])
        return cur.fetchall()

    def countUnclassifiedInWindow(self, project_id, model, prompt_version, temperature,
                                  start=None, end=None):
        """Unbounded backlog count, so a preview can report the true total."""
        cur = self.connection.cursor()
        q = f"SELECT COUNT(*) FROM issues i {self._DONE_JOIN} WHERE i.project_id = ? AND c.id IS NULL"
        params = [model, prompt_version, temperature, project_id]
        if start:
            q += " AND i.created_at >= ?"
            params.append(start)
        if end:
            q += " AND i.created_at <= ?"
            params.append(end)
        cur.execute(q, params)
        return cur.fetchone()[0]

    def getUnclassifiedInWindow(self, project_id, model, prompt_version, temperature,
                                start=None, end=None, direction="desc", limit=None):
        cur = self.connection.cursor()
        q = (f"SELECT {self._ISSUE_COLS} FROM issues i {self._DONE_JOIN} "
             "WHERE i.project_id = ? AND c.id IS NULL")
        params = [model, prompt_version, temperature, project_id]
        if start:
            q += " AND i.created_at >= ?"
            params.append(start)
        if end:
            q += " AND i.created_at <= ?"
            params.append(end)
        direction = "ASC" if str(direction).lower() == "asc" else "DESC"
        q += f" ORDER BY i.created_at {direction}"
        if limit:
            q += " LIMIT ?"
            params.append(int(limit))
        cur.execute(q, params)
        return cur.fetchall()