from __future__ import annotations
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path 
from typing import Dict, List, Optional, Set, Tuple
import requests
from nodesemver import max_satisfying

REGISTRY_BASE = "https://registry.npmjs.org"
HTTP_TIMEOUT = 30
MAX_WORKERS = 16
#our forhow many nodes we want to max at, can change but we dont wantt to max API calls yet
MAX_TRANSITIVE_NODES = 2000
SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

class Registry: 
    
    def __init__(self, cache_dir):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents = True, exist_ok = True)
        self._mem: Dict[str, dict] ={}
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS)
        self.session.mount("https://", adapter)
        
        
    def _cachePath(self, name:str) -> Path: 
        sub = name.replace("/", "__") #need to imeplement this to deal with package names like babel/core not making dirs
        return self.cache_dir / f"{sub}.json"

    def fetchDoc(self, name: str) -> Optional[dict]:
        if name in self._mem:
            return self._mem[name]
 
        cp = self._cache_path(name)
        if cp.exists():
            try:
                data = json.loads(cp.read_text())
                self._mem[name] = data
                return data
            except Exception:
                pass
 
        url = f"{REGISTRY_BASE}/{name.replace('/', '%2F')}"
        try:
            r = self.session.get(url, timeout=HTTP_TIMEOUT)
            data = r.json() if r.status_code == 200 else None
        except Exception as e:
            print(f"  ! doc fetch failed for {name}: {e}", file=sys.stderr)
            data = None

        try:
            cp.write_text(json.dumps(data))
        except Exception:
            pass
        self._mem[name] = data
        return data
    
    def prefetch(self, names: Set[str]):
        todo = [
            n for n in names
            if n not in self._mem and not self._cachePath(n).exists()
        ]
        if not todo:
            return
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            futs = {ex.submit(self.fetch, n): n for n in todo}
            for fut in as_completed(futs):
                fut.result()
    
   
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

def candidatesBefore(doc: dict, T: str) -> List[str]:
    versions = doc.get("versions", {})
    time_map = doc.get("time", {})
    out = []
    for v in versions:
        pub = time_map.get(v)
        if pub and pub <= T:
            out.append(v)
    return out

def resolveTime(reg: Registry, name: str, version_range: str, T: str, cache: Dict[Tuple[str, str], Optional[str]]) -> Optional[str]:
    key = (name, version_range)
    if key in cache:
        return cache[key]
 
    doc = reg.fetch_doc(name)
    if not doc or not doc.get("versions"):
        cache[key] = None
        return None
 
    candidates = candidatesBefore(doc, T)
    if not candidates:
        cache[key] = None
        return None
 
    try:
        resolved = max_satisfying(candidates, version_range, loose=True)
    except Exception:
        resolved = None
 
    cache[key] = resolved
    return resolved
    
def buildTransitive(reg: Registry, root_deps: Dict[str, str], T: str, max_nodes: int = MAX_TRANSITIVE_NODES) -> Tuple[Dict[str, dict], bool, int]:
    resolution_cache: Dict[Tuple[str, str], Optional[str]] = {}
    nodes: Dict[str, dict] = {}
    truncated = False
    unresolved = 0

    frontier: List[Tuple[str, str]] = list(root_deps.items())
    for name, rng in root_deps.items():
        resolved = resolveTime(reg, name, rng, T, resolution_cache)
        if resolved is None:
            unresolved += 1
        nodes[name] = {"resolved_version": resolved, "depth": 1, "range": rng}
 
    current_depth = 1
    while frontier:
        reg.prefetch({n for n, _ in frontier})
        next_depth = current_depth + 1
        new_frontier: List[Tuple[str, str]] = []
 
        for name, _rng in frontier:
            info = nodes.get(name)
            if not info or info["resolved_version"] is None:
                continue 
 
            doc = reg.fetch_doc(name)
            if not doc:
                continue
            manifest = doc.get("versions", {}).get(info["resolved_version"], {})
            child_deps = manifest.get("dependencies") or {}
 
            for child, child_rng in child_deps.items():
                if child in nodes:
                    continue 
                if len(nodes) >= max_nodes:
                    truncated = True
                    continue
                resolved = resolveTime(reg, child, child_rng, T,resolution_cache)
                if resolved is None:
                    unresolved += 1
                nodes[child] = {
                    "resolved_version": resolved,
                    "depth": next_depth,
                    "range": child_rng,
                }
                new_frontier.append((child, child_rng))
 
        frontier = new_frontier
        current_depth = next_depth
 
    return nodes, truncated, unresolved