TABLES = (
    """CREATE TABLE IF NOT EXISTS projects(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner TEXT NOT NULL,
        repo TEXT NOT NULL,
        added_at TEXT,
        package_name TEXT,
        UNIQUE(owner, repo)
    )""",
    """CREATE TABLE IF NOT EXISTS issues(
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
        version_id INTEGER REFERENCES versions(id),
        FOREIGN KEY (project_id) REFERENCES projects (id),
        UNIQUE(project_id, issue_number)
    )""",
    """CREATE TABLE IF NOT EXISTS classifications(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_id INTEGER NOT NULL,
        classification TEXT,
        classification_probabilities TEXT,
        classification_raw_response TEXT,
        model TEXT,
        prompt_version TEXT,
        temperature REAL,
        classified_at TEXT,
        status TEXT,
        error TEXT,
        validation_flags TEXT,
        argmax_label TEXT,
        attempts INTEGER,
        FOREIGN KEY (issue_id) REFERENCES issues(id),
        UNIQUE(issue_id, model, prompt_version, temperature)
    )""",
    """CREATE TABLE IF NOT EXISTS versions(
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
    )""",
    """CREATE TABLE IF NOT EXISTS version_dependencies(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version_id INTEGER NOT NULL,
        dep_name TEXT NOT NULL,
        dep_kind TEXT NOT NULL CHECK(dep_kind IN ('direct','peer','dev')),
        dep_version_range TEXT,
        resolved_version TEXT,
        depth INTEGER,
        root_dep TEXT,
        FOREIGN KEY (version_id) REFERENCES versions(id)
    )""",
)

ADDED_COLUMNS = (
    ("projects", "package_name", "TEXT"),
    ("issues", "version_id", "INTEGER REFERENCES versions(id)"),
    ("versions", "peer_count", "INTEGER"),
    ("version_dependencies", "resolved_version", "TEXT"),
    ("version_dependencies", "root_dep", "TEXT"),
    ("classifications", "status", "TEXT"),
    ("classifications", "error", "TEXT"),
    ("classifications", "validation_flags", "TEXT"),
    ("classifications", "argmax_label", "TEXT"),
    ("classifications", "attempts", "INTEGER"),
)

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_vdeps_version ON version_dependencies(version_id)",
    "CREATE INDEX IF NOT EXISTS idx_vdeps_name ON version_dependencies(dep_name)",
    "CREATE INDEX IF NOT EXISTS idx_vdeps_kind ON version_dependencies(dep_kind)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_classifications_run "
    "ON classifications(issue_id, model, prompt_version, temperature)",
)

TABLE_NAMES = ("version_dependencies", "versions", "classifications", "issues", "projects")


def ensure_schema(connection) -> None:
    for statement in TABLES:
        connection.execute(statement)
    for table, column, column_type in ADDED_COLUMNS:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    for statement in INDEXES:
        connection.execute(statement)
    connection.commit()
