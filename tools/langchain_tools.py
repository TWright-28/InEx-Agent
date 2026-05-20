from tools.lib.database import InExTool
from tools.lib.collect import Collect
from tools.lib.classify import Classify
from config import DB_PATH, GITHUB_TOKEN, temperature, max_tokens, classifier, classifyPrompt
from langchain.tools import tool
from tools.core.dependencies import DependencySnapshotter

db = InExTool(DB_PATH)
cl = Classify(classifier, temperature, classifyPrompt)
snap = DependencySnapshotter()

@tool('get_stats', description="Collect the stats about classification distributions from the database", return_direct=False)
def get_stats() -> str:
    results = db.get_stats()
    output = ""
    for label, count in results:
        output += f"{label}: {count}\n"
    return output

@tool('count_issues', description="Count the total number of issues from a github repo without collecting them. Use this before Collect_all to check if the repo is large. Input should be owner/repo. Note: counts above 1000 are reported as '1000+' due to GitHub API limits.")
def count_issues(repoName: str) -> str:
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    count = collector.countIssues(owner, repo)
    if count >= 1000: 
        return f"{owner}/{repo} has 1000+ issues (exact count unavailable due to GitHub API limit)."
    return f"{owner}/{repo} has {count} issues."

@tool('collect_all', description="Collect all the issues from a github repo and save to the database, Input should be in owner/repo format")
def collect_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    return collector.collectAll(owner, repo, db)

@tool('classify_all', description="Classify all the issues from a github repo in our database and classify them")
def classify_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    return cl.classifyAll(owner, repo, db)

@tool("snapshot_dependencies", description=("Snapshot npm direct/peer/dev dependencies for a project. Every classified issue is linked to the project version that was live at its creation date. Optionally also collect a slice of version history: last_n_versions (e.g. 10), or version_start/version_end (ISO dates, by version PUBLISH date). start_date/end_date filter which issues to link (by issue creation date). Transitive deps are NOT collected here - use get_transitive_dependencies for that. Requires classifications to exist."))
def snapshot_dependencies(repo_name: str, npm_package: str, start_date: str = None, end_date: str = None, version_range: str = None, last_n_versions: int = None, version_start: str = None, version_end: str = None) -> dict:
    parts = repo_name.strip("/").split("/")
    if len(parts) != 2:
        return {"status": "error", "code": "invalid_repo_format",
                "received": repo_name}
    owner, repo = parts
    return snap.snapshot_for_classified(owner, repo, db, npm_package=npm_package,start=start_date, end=end_date, version_range=version_range,last_n_versions=last_n_versions,version_start=version_start, version_end=version_end)


@tool("get_transitive_dependencies", description=("Walk and store the full transitive dependency tree for project versions that have already been snapshotted. Slow. Target one version (version='1.3.0') or a publish-date range (version_start/version_end ISO dates). Omit both to walk every snapshotted version. Versions that already have transitive data are skipped."))
def get_transitive_dependencies(repo_name: str, npm_package: str,version: str = None, version_start: str = None,  version_end: str = None) -> dict:
    parts = repo_name.strip("/").split("/")
    if len(parts) != 2:
        return {"status": "error", "code": "invalid_repo_format",
                "received": repo_name}
    owner, repo = parts
    return snap.snapshot_transitive(owner, repo, db, npm_package=npm_package, version=version, version_start=version_start, version_end=version_end)
    
@tool("list_projects",description=("List all projects in the database that have at least one classification, with counts and the most recent issue date. Use this when the user asks what projects are available to analyze, or when you need to confirm a project exists before running other tools."),)
def list_projects() -> dict:
    db.cursor.execute("""
        SELECT p.owner, p.repo, p.package_name,
               COUNT(DISTINCT c.id) AS classification_count,
               MAX(i.created_at) AS latest_issue
        FROM projects p
        INNER JOIN issues i ON i.project_id = p.id
        INNER JOIN classifications c ON c.issue_id = i.id
        GROUP BY p.id
        ORDER BY classification_count DESC
    """)
    rows = db.cursor.fetchall()
    return {
        "status": "ok",
        "projects": [
            {
                "owner": owner,
                "repo": repo,
                "npm_package": pkg,
                "classification_count": count,
                "latest_issue": latest,
            }
            for owner, repo, pkg, count, latest in rows
        ],
    }