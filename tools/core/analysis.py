import logging 
logger = logging.getLogger(__name__)


extRatio = 1.011 # our dep number we got from ESEM


EXTRAPOLATION_NOTE = "Modeled estimate. Extends the per-dependency odds ratio (1.011) from Wright et al. to the full transitive tree; the original paper fitted this coefficient on direct dependencies only, so figures including transitive dependencies are an extrapolation, not a paper finding."

def oddsIncreasePct(dep_count: int) -> float:
    return (extRatio**dep_count  -1)*100.0

def depRisk(repo: str, db, version: str = None) -> dict:
    parts = repo.strip("/").split("/")
    if len(parts) != 2:
        return {"status": "error", "code": "invalid_repo_format", "received": repo}
    owner, repo_name = parts
    
    db.cursor.execute("SELECT id FROM projects WHERE owner = ? AND repo = ?",(owner, repo_name))
    row = db.cursor.fetchone()
    if not row:
        return {"status": "error", "code": "project_not_found","owner": owner, "repo": repo_name}
    project_id = row[0]
    
    if version:
        db.cursor.execute("SELECT id, version, direct_count, transitive_count, transitive_truncated FROM versions WHERE project_id = ? AND version = ?", (project_id, version))
    else:
        db.cursor.execute("SELECT id, version, direct_count, transitive_count, transitive_truncated FROM versions WHERE project_id = ? ORDER BY published_at DESC LIMIT 1", (project_id,))
    vrow = db.cursor.fetchone()
    if not vrow:
        return {"status": "error", "code": "no_snapshotted_version","owner": owner, "repo": repo_name, "version": version}
 
