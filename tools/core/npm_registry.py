from __future__ import annotations
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path 
from typing import Dict, List, Optional, Set, Tuple
import requests

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

    def fetch(self, name: str) -> dict:
        
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
            
        url = f"{REGISTRY_BASE}/{name.replace('/', '%2F')}/latest"
        
        try:
            r = self.session.get(url, timeout=HTTP_TIMEOUT)
            data = r.json() if r.status_code == 200 else {}
        except Exception as e:
            print(f"  ! fetch failed for {name}: {e}", file=sys.stderr)
            data = {}
            
        cp.write_text(json.dumps(data))
        self._mem[name] = data
        return data
    
    def deps(self, name: str) -> Dict[str, str]:
        m = self.fetch(name)
        return m.get("dependencies") or {}
    
    
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
    
    
def buildTransitive(reg: Registry, root_deps: Dict[str, str], max_nodes: int = MAX_TRANSITIVE_NODES) -> Tuple[Dict[str, int], bool]:
    depth: Dict[str, int] = {name: 1 for name in root_deps}
    frontier: List[str] = list(root_deps.keys())
    currentDepth = 1
    truncated = False
    while frontier:
        reg.prefetch(set(frontier))
        newFrontier: List[str] = []
        nextDepth = currentDepth + 1
        for pkg in frontier:
            for child in reg.deps(pkg):
                if child in depth:
                    continue
                if len(depth) >= max_nodes:
                    truncated = True
                    continue
                depth[child] = nextDepth
                newFrontier.append(child)
        frontier = newFrontier
        currentDepth = nextDepth
    return depth, truncated