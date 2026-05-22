def build_db_summary(db):
    lines = ["# Current database state (snapshot at session start)", ""]
    db.cursor.execute("""
        SELECT p.id, p.owner, p.repo, p.package_name,
               COUNT(DISTINCT i.id) AS issue_count,
               COUNT(DISTINCT c.id) AS classified_count
        FROM projects p
        LEFT JOIN issues i ON i.project_id = p.id
        LEFT JOIN classifications c ON c.issue_id = i.id
        GROUP BY p.id
        ORDER BY classified_count DESC
    """)
    projects = db.cursor.fetchall()

    if not projects:
        lines.append("The database is empty - no projects have been added yet.")
        return "\n".join(lines)

    lines.append("Projects:")
    for pid, owner, repo, pkg, issues, classified in projects:
        db.cursor.execute(
            "SELECT COUNT(*) FROM versions WHERE project_id = ?", (pid,))
        version_count = db.cursor.fetchone()[0]

        pkg_str = f", npm: {pkg}" if pkg else ""
        lines.append(
            f"- {owner}/{repo}{pkg_str} — {issues} issues, "
            f"{classified} classified, {version_count} versions snapshotted")

    db.cursor.execute("SELECT classification, COUNT(*) FROM classifications GROUP BY classification")
    totals = db.cursor.fetchall()
    if totals:
        tstr = ", ".join(f"{label} {count}" for label, count in totals)
        lines.append("")
        lines.append(f"Classification totals: {tstr}")

    lines.append("")
    lines.append("Note: this is a snapshot from session start. After any collect, classify, or snapshot operation it is stale - re-query with tools rather than trusting it.")
    return "\n".join(lines)