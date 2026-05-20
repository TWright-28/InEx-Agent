from __future__ import annotations
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path 
from typing import Dict, List, Optional, Set, Tuple
import requests
from nodesemver import max_satisfying
from collections import deque

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
 
        cp = self._cachePath(name)
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
 
    doc = reg.fetchDoc(name)
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
    
def buildTransitive(reg: Registry, root_deps: Dict[str, str], T: str,max_nodes: int = MAX_TRANSITIVE_NODES) -> Tuple[Dict[str, dict], bool, int]:
  
    resolution_cache: Dict[Tuple[str, str], Optional[str]] = {}
 
    resolved_of: Dict[str, Optional[str]] = {}
    range_of: Dict[str, str] = {}
    children_of: Dict[str, List[str]] = {}
    unresolved = 0
    truncated = False
 
    for name, rng in root_deps.items():
        resolved_of[name] = resolveTime(reg, name, rng, T, resolution_cache)
        range_of[name] = rng
        if resolved_of[name] is None:
            unresolved += 1
 
    frontier = deque(root_deps.keys())
    while frontier:
        reg.prefetch(set(frontier))
        nxt = []
        for name in frontier:
            children_of.setdefault(name, [])
            resolved = resolved_of.get(name)
            if resolved is None:
                continue 
 
            doc = reg.fetchDoc(name)
            if not doc:
                continue
            manifest = doc.get("versions", {}).get(resolved, {})
            child_deps = manifest.get("dependencies") or {}
 
            for child, child_rng in child_deps.items():
                children_of[name].append(child)
                if child in resolved_of:
                    continue 
                if len(resolved_of) >= max_nodes:
                    truncated = True
                    continue
                resolved_child = resolveTime(
                    reg, child, child_rng, T, resolution_cache)
                resolved_of[child] = resolved_child
                range_of[child] = child_rng
                if resolved_child is None:
                    unresolved += 1
                nxt.append(child)
        frontier = deque(nxt)
 
    roots_of: Dict[str, Dict[str, int]] = {pkg: {} for pkg in resolved_of}
 
    for root in root_deps:
        seen = {root: 0}
        q = deque([(root, 0)])
        while q:
            pkg, depth = q.popleft()
            prev = roots_of[pkg].get(root)
            if prev is None or depth < prev:
                roots_of[pkg][root] = depth
            for child in children_of.get(pkg, []):
                if child not in resolved_of:
                    continue
                nd = depth + 1
                if child not in seen or nd < seen[child]:
                    seen[child] = nd
                    q.append((child, nd))
 
    nodes: Dict[str, dict] = {}
    for pkg in resolved_of:
        nodes[pkg] = {
            "resolved_version": resolved_of[pkg],
            "range": range_of.get(pkg),
            "roots": roots_of.get(pkg, {}),
        }
 
    return nodes, truncated, unresolved