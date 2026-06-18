from tools.helpers.database import InExTool
from tools.helpers.collect import Collect
from tools.helpers.classify import Classify
from tools.helpers.sqlQuery import runSql, describeSchema, exportQuery
from tools.helpers.issueList import parseIssueListCsv
from config import DB_PATH, GITHUB_TOKEN, temperature, classifier, classifyPrompt
from langchain.tools import tool
from tools.core.dependencies import DependencySnapshotter
from collections import defaultdict
import sqlite3


db = InExTool(DB_PATH)
cl = Classify(classifier, temperature, classifyPrompt)
snap = DependencySnapshotter()
ro_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
MAX_SQL_ROWS = 50

@tool('count_issues', description="Count the total number of issues from a github repo without collecting them. Use this before Collect_all to check if the repo is large. Input should be owner/repo. Note: counts above 1000 are reported as '1000+' due to GitHub API limits.")
def count_issues(repoName: str) -> str:
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    count = collector.countIssues(owner, repo)
    if count >= 1000: 
        return f"{owner}/{repo} has 1000+ issues (exact count unavailable due to GitHub API limit)."
    return f"{owner}/{repo} has {count} issues."

@tool('collect_all', description="Collect issues from a GitHub repo and save to the database. Input: repo_name as 'owner/repo'. Optional: count (max number of issues), start_date and end_date (ISO 'YYYY-MM-DD', filter by issue creation date), direction ('desc' for newest-first, 'asc' for oldest-first; default desc). Long-running: multiple API calls per issue.")
def collect_all(repo_name: str, count: int = None, start_date: str = None, end_date: str = None, direction: str = "desc") -> str: 
    parts = repo_name.strip("/").split("/")
    if len(parts) != 2:
        return f"Invalid format. Expected 'owner/repo', got '{repo_name}'."
    
    owner, repo = parts
    if end_date and len(end_date) == 10:
        end_date = end_date + "T23:59:59Z"
    collector = Collect(GITHUB_TOKEN)
    return collector.collectAll(owner, repo, db, max_issues=count, start_date=start_date, end_date=end_date, direction=direction)


@tool('classify_all', description="Classify unclassified issues for a repo already in the database. SLOW - each issue is an LLM call. Input: repo_name as 'owner/repo'. Optional: count (cap how many to classify in this run), start_date/end_date (ISO 'YYYY-MM-DD', filter by issue creation date), direction ('desc'/'asc'). Only classifies issues not already classified, so it can be run repeatedly to classify in batches. Call preview_classification first and confirm with the user before calling this.")
def classify_all(repoName: str, count: int = None, start_date: str = None, end_date: str = None, direction: str = "desc") -> str: 
    parts = repoName.strip("/").split("/")
    if len(parts) != 2:
        return f"Invalid format. Expected 'owner/repo', got '{repoName}'."
    owner, repo = parts
    if end_date and len(end_date) == 10:
        end_date = end_date + "T23:59:59Z"
    return cl.classifyAll(owner, repo, db, max_issues=count, start=start_date, end=end_date, direction=direction)

@tool("snapshot_dependencies", description=("Fetch npm dependency data for a project and save it to the database. This tool does everything in one step: it contacts the npm registry, finds the package version that was live when each classified issue was filed, saves that version and its direct/peer/dev dependencies to the database, and links each issue to its version. You do NOT need versions in the database beforehand — this tool creates them. Requires classifications to exist. DO NOT pass last_n_versions, version_start, or version_end unless the user explicitly asks for extra version history — by default only snapshot the versions that issues map to."))
def snapshot_dependencies(repo_name: str, npm_package: str, start_date: str = None, end_date: str = None, version_range: str = None, last_n_versions: int = None, version_start: str = None, version_end: str = None) -> dict:
    parts = repo_name.strip("/").split("/")
    if len(parts) != 2:
        return {"status": "error", "code": "invalid_repo_format",
                "received": repo_name}
    owner, repo = parts
    return snap.snapshot_for_classified(owner, repo, db, npm_package=npm_package,start=start_date, end=end_date, version_range=version_range,last_n_versions=last_n_versions,version_start=version_start, version_end=version_end)


@tool("describe_schema", description=("Returns the database schema — every table and its columns. Call before writing a run_sql query so you use correct names." ))
def describe_schema() -> dict:
    return describeSchema()


@tool("run_sql", description=("Run a READ-ONLY SQL SELECT against the database. For analysis questions not covered by other tools. Only SELECT allowed; writes are rejected. Call describe_schema first. Results capped at 200 rows — prefer COUNT/AVG/GROUP BY for large tables."))
def run_sql(query: str) -> dict:
    return runSql(query)

@tool("preview_classification", description="Preview what classify_all would classify, WITHOUT classifying anything. Returns how many unclassified issues match the filters, their date span, and a small sample. Call before classify_all and show the user so they can confirm. Inputs: repo_name as 'owner/repo'. Optional: count, start_date, end_date (ISO 'YYYY-MM-DD'), direction ('desc'/'asc').")
def preview_classification(repo_name: str, count: int = None, start: str = None, end: str = None, direction: str = "desc") -> dict:
    parts = repo_name.strip("/").split("/")
    if len(parts) != 2:
        return {"status": "error", "code": "invalid_repo_format", "received": repo_name}
    owner, repo = parts
    if end and len(end) == 10:
        end = end + "T23:59:59Z"
    return cl.previewClassification(owner, repo, db, max_count=count, start=start, end=end, direction=direction)

@tool("preview_issue_list", description="Preview a CSV file of specific issues to import, WITHOUT collecting or classifying anything. The CSV must have a column of 'owner/repo#number' references (e.g. 'astropy/astropy#12906'). Returns the total parsed, a per-repo breakdown, how many are already in the database, and any rows that could not be parsed. Read-only and fast. Call before import_issue_list and show the user to confirm. Inputs: csv_path (path to the CSV). Optional: column (the column name; defaults to the first column).")
def preview_issue_list(csv_path: str, column: str = None) -> dict:
    try:
        entries, unparseable = parseIssueListCsv(csv_path, column)
    except FileNotFoundError:
        return {"status": "error", "code": "file_not_found", "csv_path": csv_path}

    by_repo = defaultdict(list)
    for owner, repo, num in entries:
        by_repo[(owner, repo)].append(num)

    repos = []
    total_already = 0
    for (owner, repo), nums in sorted(by_repo.items()):
        db.cursor.execute("SELECT id FROM projects WHERE owner=? AND repo=?", (owner, repo))
        row = db.cursor.fetchone()
        already = 0
        if row:
            placeholders = ",".join("?" * len(nums))
            db.cursor.execute(
                f"SELECT COUNT(*) FROM issues WHERE project_id=? AND issue_number IN ({placeholders})",
                [row[0], *nums])
            already = db.cursor.fetchone()[0]
        total_already += already
        repos.append({"repo": f"{owner}/{repo}", "issues": len(nums), "already_in_db": already})

    return {
        "status": "ok",
        "csv_path": csv_path,
        "total_issues": len(entries),
        "distinct_repos": len(by_repo),
        "already_in_db": total_already,
        "to_collect": len(entries) - total_already,
        "repos": repos,
        "unparseable_count": len(unparseable),
        "unparseable_sample": unparseable[:5],
    }

@tool("import_issue_list", description="Collect and classify a specific list of GitHub issues from a CSV file. The CSV must have a column of 'owner/repo#number' references. For each issue: fetches it from GitHub (skipping ones already in the database), then classifies it (Intrinsic/Extrinsic/Not-a-Bug/Unknown). SLOW — one or more GitHub API calls plus one LLM call per issue. Call preview_issue_list first and confirm with the user before calling this. Inputs: csv_path. Optional: column (defaults to first column); classify (default True; set False to only collect).")
def import_issue_list(csv_path: str, column: str = None, classify: bool = True) -> dict:
    try:
        entries, unparseable = parseIssueListCsv(csv_path, column)
    except FileNotFoundError:
        return {"status": "error", "code": "file_not_found", "csv_path": csv_path}

    if not entries:
        return {"status": "error", "code": "no_valid_entries", "csv_path": csv_path,
                "unparseable_sample": unparseable[:5]}

    by_repo = defaultdict(list)
    for owner, repo, num in entries:
        by_repo[(owner, repo)].append(num)

    collector = Collect(GITHUB_TOKEN)
    results = []
    totals = {"collected": 0, "skipped": 0, "failed": 0, "classified": 0}

    for (owner, repo), nums in sorted(by_repo.items()):
        project_id, issue_ids, collected, skipped, failed = collector.collectSpecific(owner, repo, nums, db)
        classified = 0
        if classify and issue_ids:
            cl.classifyByIds(owner, repo, issue_ids, db)
            db.cursor.execute(
                "SELECT COUNT(*) FROM classifications c JOIN issues i ON i.id=c.issue_id WHERE i.project_id=? AND i.issue_number IN (%s)" % ",".join("?" * len(nums)),
                [project_id, *nums])
            classified = db.cursor.fetchone()[0]

        totals["collected"] += collected
        totals["skipped"] += skipped
        totals["failed"] += failed
        totals["classified"] += classified
        results.append({"repo": f"{owner}/{repo}", "collected": collected,
                        "skipped": skipped, "failed": failed, "classified": classified})

    return {
        "status": "ok",
        "csv_path": csv_path,
        "totals": totals,
        "repos": results,
        "unparseable_count": len(unparseable),
    }

@tool("export_data", description=("Export the results of a SQL SELECT query to a file. Use when the user wants to save data for external analysis. query: a valid SELECT statement. format: 'csv' or 'json' (default 'csv'). filename: optional, auto-generated if omitted. No row limit — exports all matching rows. Files are written to the exports/ directory. Call describe_schema first if column names are needed."))
def export_data(query: str, format: str = "csv", filename: str = None) -> dict:
    return exportQuery(query, format, filename)

