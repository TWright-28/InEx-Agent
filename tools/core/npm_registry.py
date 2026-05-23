from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import List, Optional
import requests

REGISTRY_BASE = "https://registry.npmjs.org"
HTTP_TIMEOUT = 30
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def fetchFull(name: str, cache_dir: Path, session: Optional[requests.Session] = None) -> Optional[dict]:
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "__")
    cp = cache_dir / f"{safe}.json"
    if cp.exists():
        try:
            return json.loads(cp.read_text())
        except Exception:
            pass
    session = session or requests.Session()
    url = f"{REGISTRY_BASE}/{name.replace('/', '%2F')}"
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        cp.write_text(json.dumps(data))
        return data
    except Exception as e:
        print(f"  ! doc fetch failed for {name}: {e}", file=sys.stderr)
        return None


def sortVersionsDesc(versions: List[str]) -> List[str]:
    def key(v):
        m = SEMVER_RE.match(v)
        if not m:
            return (0, 0, 0, 1, v)
        major, minor, patch = map(int, m.groups())
        is_pre = "-" in v
        return (major, minor, patch, 1 if is_pre else 0)
    return sorted(versions, key=key, reverse=True)
