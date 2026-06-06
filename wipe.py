import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""
DROP TABLE IF EXISTS version_dependencies;
DROP TABLE IF EXISTS versions;
DROP TABLE IF EXISTS classifications;
DROP TABLE IF EXISTS issues;
DROP TABLE IF EXISTS projects;

CREATE TABLE projects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    owner           TEXT NOT NULL,
    repo            TEXT NOT NULL,
    added_at        TEXT,
    package_name    TEXT,
    UNIQUE(owner, repo)
);

CREATE TABLE issues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL,
    issue_number    INTEGER NOT NULL,
    github_id       INTEGER,
    url             TEXT,
    title           TEXT,
    body            TEXT,
    state           TEXT,
    state_reason    TEXT,
    locked          INTEGER,
    created_at      TEXT,
    closed_at       TEXT,
    updated_at      TEXT,
    comments_count  INTEGER,
    raw_data        TEXT,
    version_id      INTEGER REFERENCES versions(id),
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, issue_number)
);

CREATE TABLE classifications (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id                    INTEGER NOT NULL,
    classification              TEXT,
    classification_probabilities TEXT,
    classification_raw_response TEXT,
    model                       TEXT,
    prompt_version              TEXT,
    temperature                 REAL,
    classified_at               TEXT,
    FOREIGN KEY (issue_id) REFERENCES issues(id),
    UNIQUE(issue_id, model, prompt_version, temperature)
);

CREATE TABLE versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      INTEGER NOT NULL,
    package_name    TEXT NOT NULL,
    version         TEXT NOT NULL,
    published_at    TEXT,
    direct_count    INTEGER,
    peer_count      INTEGER,
    dev_count       INTEGER,
    raw_manifest    TEXT,
    snapshotted_at  TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id),
    UNIQUE(project_id, version)
);

CREATE TABLE version_dependencies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id          INTEGER NOT NULL,
    dep_name            TEXT NOT NULL,
    dep_kind            TEXT NOT NULL CHECK(dep_kind IN ('direct','peer','dev')),
    dep_version_range   TEXT,
    resolved_version    TEXT,
    depth               INTEGER,
    root_dep            TEXT,
    FOREIGN KEY (version_id) REFERENCES versions(id)
);

CREATE INDEX idx_vdeps_version ON version_dependencies(version_id);
CREATE INDEX idx_vdeps_name    ON version_dependencies(dep_name);
CREATE INDEX idx_vdeps_kind    ON version_dependencies(dep_kind);
 """)

conn.commit()
conn.close()
print("Database dropped and rebuilt.")