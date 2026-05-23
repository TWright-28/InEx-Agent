def build_db_summary(db):
    lines = ["# Projects in database (session start)", ""]
    db.cursor.execute("""
        SELECT p.owner, p.repo, p.package_name
        FROM projects p
        ORDER BY p.owner, p.repo
    """)
    projects = db.cursor.fetchall()

    if not projects:
        lines.append("The database is empty - no projects have been added yet.")
    else:
        lines.append("Known projects (use run_sql for current counts):")
        for owner, repo, pkg in projects:
            pkg_str = f", npm: {pkg}" if pkg else ""
            lines.append(f"- {owner}/{repo}{pkg_str}")

    lines.append("")
    lines.append("Note: counts are intentionally omitted — use run_sql for any current figures.")
    return "\n".join(lines)
