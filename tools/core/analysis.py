import logging 
logger = logging.getLogger(__name__)


extRatio = 1.011 # our dep number we got from ESEM


EXTRAPOLATION_NOTE = (
    "Modeled estimate of EXTRINSIC-bug exposure (bugs originating from dependencies). Extends the per-dependency odds ratio (1.011) from the study to the full transitive tree; the study fitted that coefficient on direct dependencies only, so figures including transitive dependencies are an extrapolation.")
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
 
    version_id, version_str, direct_count, transitive_count, truncated = vrow
    direct_count = direct_count or 0
    transitive_count = transitive_count or 0
    
    if transitive_count == 0:
        return {"status": "error", "code": "no_transitive_data", "owner": owner, "repo": repo_name, "version": version_str, "message": ("This version has no transitive dependency data. Run get_transitive_dependencies first.")}
 
    db.cursor.execute("SELECT root_dep, COUNT(*) FROM version_dependencies WHERE version_id = ? AND dep_kind = 'transitive' GROUP BY root_dep", (version_id,))
    subtree = {name: count for name, count in db.cursor.fetchall() if name}
 
    db.cursor.execute("SELECT dep_name FROM version_dependencies WHERE version_id = ? AND dep_kind = 'direct'", (version_id,))
    direct_names = [r[0] for r in db.cursor.fetchall()]
 
    perDep = []
    for name in direct_names:
        sub = subtree.get(name, 0)
        depTotal = 1 + sub
        perDep.append({
            "dependency": name,
            "transitive_subtree": sub,
            "dependency_total": depTotal,
            "modeled_odds_increase_pct": round(oddsIncreasePct(depTotal), 1),
        })
    perDep.sort(key=lambda d: d["transitive_subtree"], reverse=True)
 
    totalDeps = direct_count + transitive_count
    versionMod = round(oddsIncreasePct(totalDeps), 1)
 
    return {
        "status": "ok",
        "owner": owner,
        "repo": repo_name,
        "version": version_str,
        "direct_count": direct_count,
        "transitive_count": transitive_count,
        "transitive_truncated": bool(truncated) if truncated is not None else None,
        "total_dependencies": totalDeps,
        "version_modeled_odds_increase_pct": versionMod,
        "per_dependency": perDep,
        "model_note": EXTRAPOLATION_NOTE,
    }