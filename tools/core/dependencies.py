from __future__ import annotations
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import requests
from tools.core.npm_registry import fetchFull, sortVersionsDesc

logger = logging.getLogger(__name__)

DOC_CACHE_DIR = Path(".npm_doc_cache")


def _clean(v):
    if v is None:
        return None
    v = str(v).strip()
    return None if v.lower() in ("", "*", "all", "none") else v


class DependencySnapshotter:

    def __init__(self):
        self.session = requests.Session()

    def resolve_package_name(self, db, project_id, npm_package):
        cached = db.getPackageName(project_id)
        if cached and cached == npm_package:
            return cached

        doc = fetchFull(npm_package, DOC_CACHE_DIR, self.session)
        if not (doc and doc.get("versions")):
            logger.warning("npm registry has no package %r", npm_package)
            return None

        db.setPackageName(project_id, npm_package)
        return npm_package

    def _live_version_at(self, issue_created_at, versions_with_times):
        for version, pub in versions_with_times:
            if pub and pub <= issue_created_at:
                return version, pub
        return None

    def _filter_versions_by_range(self, versions, version_range):
        m = re.match(r"^(\d+)\.x$", version_range)
        if m:
            major = int(m.group(1))
            out = []
            for v in versions:
                vm = re.match(r"^(\d+)\.", v)
                if vm and int(vm.group(1)) == major:
                    out.append(v)
            return out

        m = re.match(r"^(\d+\.\d+\.\d+)\.\.(\d+\.\d+\.\d+)$", version_range)
        if m:
            lo, hi = m.group(1), m.group(2)

            def to_tuple(s):
                return tuple(int(x) for x in s.split("."))

            lo_t, hi_t = to_tuple(lo), to_tuple(hi)
            out = []
            for v in versions:
                vm = re.match(r"^(\d+)\.(\d+)\.(\d+)", v)
                if not vm:
                    continue
                t = tuple(int(x) for x in vm.groups())
                if lo_t <= t < hi_t:
                    out.append(v)
            return out

        logger.warning("Unrecognized version_range %r; ignoring filter", version_range)
        return versions

    def _select_versions(self, versions_desc, time_map, last_n_versions=None, version_start=None, version_end=None):
        selected = set()

        if last_n_versions:
            try:
                n = int(last_n_versions)
                if n > 0:
                    selected.update(versions_desc[:n])
            except (TypeError, ValueError):
                logger.warning("Bad last_n_versions %r; ignoring", last_n_versions)

        if version_start or version_end:
            for v in versions_desc:
                pub = time_map.get(v)
                if not pub:
                    continue
                if version_start and pub < version_start:
                    continue
                if version_end and pub > version_end:
                    continue
                selected.add(v)

        return selected

    def _snapshot_version(self, db, project_id, package_name, version, published_at, versions_map):

        manifest = versions_map.get(version, {})
        direct = manifest.get("dependencies") or {}
        peer = manifest.get("peerDependencies") or {}
        dev = manifest.get("devDependencies") or {}

        logger.info("  Snapshotting %s@%s (direct=%d, peer=%d, dev=%d)",
                    package_name, version, len(direct), len(peer), len(dev))

        counts = {
            "direct": len(direct),
            "peer": len(peer),
            "dev": len(dev),
        }
        raw_manifest = {
            "dependencies": direct,
            "peerDependencies": peer,
            "devDependencies": dev,
        }

        version_id = db.saveVersion(project_id, package_name, version, published_at, counts, raw_manifest)

        rows = []
        for name, rng in direct.items():
            rows.append((name, "direct", rng, None, 0, None))
        for name, rng in peer.items():
            rows.append((name, "peer", rng, None, 0, None))
        for name, rng in dev.items():
            rows.append((name, "dev", rng, None, 0, None))
        db.save_dependencies(version_id, rows)
        return version_id

    def snapshot_for_classified(self, owner, repo, db, npm_package,start=None, end=None, version_range=None,last_n_versions=None,version_start=None, version_end=None) -> dict:

        start = _clean(start)
        end = _clean(end)
        version_range = _clean(version_range)
        version_start = _clean(version_start)
        version_end = _clean(version_end)

        db.cursor.execute("SELECT id FROM projects WHERE owner = ? AND repo = ?", (owner, repo))
        row = db.cursor.fetchone()
        if not row:
            return {"status": "error", "code": "project_not_found",
                    "owner": owner, "repo": repo}
        project_id = row[0]

        if not db.projectHasClassifications(project_id):
            return {"status": "error", "code": "no_classifications",
                    "owner": owner, "repo": repo}

        package_name = self.resolve_package_name(db, project_id, npm_package)
        if not package_name:
            return {"status": "error", "code": "npm_package_not_found",
                    "attempted": npm_package}

        doc = fetchFull(package_name, DOC_CACHE_DIR, self.session)
        if not doc:
            return {"status": "error", "code": "npm_doc_fetch_failed",
                    "package_name": package_name}

        versions_map = doc.get("versions", {})
        time_map = doc.get("time", {})
        all_versions = list(versions_map.keys())

        if version_range:
            all_versions = self._filter_versions_by_range(all_versions, version_range)
            if not all_versions:
                return {"status": "error", "code": "no_versions_match_range","package_name": package_name, "version_range": version_range}

        versions_desc = sortVersionsDesc(all_versions)
        versions_with_times = [(v, time_map.get(v)) for v in versions_desc]

        issues = db.getClassificationIssueWindow(project_id, start, end)
        if not issues:
            return {"status": "error", "code": "no_classified_issues", "owner": owner, "repo": repo, "start": start, "end": end}

        slice_versions = self._select_versions(versions_desc, time_map, last_n_versions, version_start, version_end)

        snapshotted = 0
        linked = 0
        unlinkable_pre_release = 0
        already_linked = 0

        def ensure_version(version, published_at):
            nonlocal snapshotted
            vid = db.getVersionByString(project_id, version)
            if vid is None:
                vid = self._snapshot_version(
                    db, project_id, package_name, version, published_at,
                    versions_map
                )
                snapshotted += 1
            return vid

        for issue_id, issue_number, created_at, existing_version_id in issues:
            if existing_version_id is not None:
                already_linked += 1
                continue
            picked = self._live_version_at(created_at, versions_with_times)
            if not picked:
                unlinkable_pre_release += 1
                logger.info("  Issue #%s created %s predates first release",
                            issue_number, created_at)
                continue
            version, published_at = picked
            version_id = ensure_version(version, published_at)
            db.link_issue_to_version(issue_id, version_id)
            linked += 1

        extra = 0
        for version in slice_versions:
            if db.getVersionByString(project_id, version) is None:
                self._snapshot_version(
                    db, project_id, package_name, version,
                    time_map.get(version), versions_map
                )
                extra += 1

        return {
            "status": "ok",
            "package_name": package_name,
            "owner": owner,
            "repo": repo,
            "issue_versions_snapshotted": snapshotted,
            "slice_versions_snapshotted": extra,
            "linked_issues": linked,
            "already_linked": already_linked,
            "unlinkable_pre_release": unlinkable_pre_release,
            "window": {
                "start": start,
                "end": end,
                "version_range": version_range,
                "last_n_versions": last_n_versions,
                "version_start": version_start,
                "version_end": version_end,
            },
        }

