# Core Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure ICSME-Tool into the layered `inex/` package (store, clients, services, app, CLI and agent front ends) with classification profiles, enforced by tests, before the 2026-11-10 MSR submission.

**Architecture:** One composition root (`build_app`) builds a `Store`, an npm client and six services from `Settings`; the CLI and the LangChain agent are thin front ends over the same service calls. Layer, environment, import-side-effect and no-comment rules are enforced by `tests/test_architecture.py`, with `services/collection.py` and `services/classification.py` excepted from the HTTP/model import rule until they are split after submission.

**Tech Stack:** Python 3.14 venv (code targets 3.11+), SQLite (`sqlite3`), `requests`, LangChain/LangGraph, Pydantic 2, PyYAML, `argparse`, pytest with pytest-socket, ruff.

**Spec:** `docs/superpowers/specs/2026-10-26-core-seams-design.md` (including its "Refinements made while planning" section) and, for profiles, `docs/superpowers/specs/2026-10-26-classification-profiles-design.md` as adjusted by the core-seams spec.

## Global Constraints

- Python: run everything with `./venv/Scripts/python.exe` from the repo root (Git Bash).
- No docstrings and no comments in any Python file; `# noqa` forms are the only permitted comments. New code complies from Task 1; old code is stripped in Task 14.
- Only `inex/config.py` reads `os.environ`, `os.getenv` or imports `dotenv`.
- Nothing opens a file, connection or client when a module is imported.
- `sqlite3` only in `inex/store/`; `requests` only in `inex/clients/`; `langchain*` and `langgraph` only in `inex/agent/`; exceptions: `inex/services/collection.py` may import `requests`, `inex/services/classification.py` may import `requests` and `langchain*`.
- Absolute imports only (`from inex.store.database import Store`); relative imports are a lint error.
- PEP 8 names for all new and moved code, except the internals of the two excepted files.
- No unexplained numbers: name them as module-level constants.
- Services raise `InexError(code, message, **details)`; only `inex/cli.py` and the wrapper in `inex/agent/tools.py` turn errors into output.
- Every service receives its store in the constructor and keeps it as `self.store`.
- Tests never reach the network: pytest runs with `--disable-socket`.
- Move files with `git mv` when a file continues under a new name.
- Each task ends with `./venv/Scripts/python.exe check.py` passing (from Task 1 on).
- Commits: no Claude attribution or Co-Authored-By trailer, no emojis, no em dashes. Never push.
- New dependency allowed: `ruff==0.12.0` (dev only). PyYAML 6.0.3 is already pinned.

## Review Focus

1. A database path with spaces, parentheses or `#` (for example `C:/Users/x/ICSME (2)/db #1.db`) must open read-write and read-only. Test in Task 2.
2. Running `python -m inex` from a folder other than the repo root must still find the bundled profile, system prompt and logging config. Test in Task 3.
3. `--json` output must be valid JSON for every command, including results that hold paths. Test in Task 10.
4. Ctrl+C during a long `collect` or `classify` must exit with code 130 and a one-line message, not a traceback. Test in Task 10.
5. An issue-list CSV saved by Excel (UTF-8 BOM, CRLF line endings, padded cells) must parse exactly like a clean one. Test in Task 7.

---

### Task 1: Tooling baseline

**Files:**
- Create: `ruff.toml`, `requirements-dev.txt`, `pytest.ini`, `check.py`
- Modify: any file ruff reports

**Interfaces:**
- Produces: `check.py` (exit 0 only when format check, lint and tests pass); `pytest.ini` with `addopts = --disable-socket`.

- [ ] **Step 1: Write `requirements-dev.txt`** containing `-r requirements.txt` and `ruff==0.12.0`, then run `./venv/Scripts/python.exe -m pip install -r requirements-dev.txt`. Expected: ruff installs.

- [ ] **Step 2: Write `ruff.toml`**: `line-length = 100`, `target-version = "py311"`, `extend-exclude = ["venv", ".npm_doc_cache", "db", "logs"]`; `[lint] select = ["E", "F", "I", "B", "UP", "N", "TID"]`, `ignore = ["E501"]`; `[lint.flake8-tidy-imports] ban-relative-imports = "all"`; `[lint.per-file-ignores]` with `"tools/**" = ["N"]`, `"config.py" = ["N"]`, `"seeder.py" = ["N"]`, `"eval.py" = ["N"]`, `"import_list.py" = ["N"]`, `"agent.py" = ["N"]` (old code; entries are removed as files move).

- [ ] **Step 3: Write `pytest.ini`**: `[pytest]`, `testpaths = tests`, `addopts = --disable-socket`.

- [ ] **Step 4: Write `check.py`** with `main() -> int` running, in order, `[sys.executable, "-m", "ruff", "format", "--check", "."]`, `[sys.executable, "-m", "ruff", "check", "."]`, `[sys.executable, "-m", "pytest", "-q"]` via `subprocess.run`, printing each command, returning the first non-zero return code, else 0; `if __name__ == "__main__": raise SystemExit(main())`. No comments.

- [ ] **Step 5: Run `./venv/Scripts/python.exe -m ruff check .`** and fix every finding. Bare `except:` becomes `except Exception:`. Where a fix would change behaviour in `tools/helpers/collect.py` or `tools/helpers/classify.py`, add a per-file ignore for that rule instead and note the rule in the commit message. Expected: `All checks passed!`

- [ ] **Step 6: Run the suite.** `./venv/Scripts/python.exe -m pytest -q` Expected: `91 passed`.

- [ ] **Step 7: Commit** `git add ruff.toml requirements-dev.txt pytest.ini check.py` plus fixed files; `git commit -m "Add ruff, pytest socket blocking and check.py"`.

- [ ] **Step 8: Format everything.** `./venv/Scripts/python.exe -m ruff format .` then `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 9: Commit** `git add -A -- '*.py'`; `git commit -m "Format the codebase with ruff"`. This commit contains formatting only.

---

### Task 2: Store

**Files:**
- Create: `inex/__init__.py`, `inex/store/__init__.py` (both empty), `inex/errors.py`, `inex/store/schema.py`
- Move: `git mv tools/helpers/database.py inex/store/database.py`; `git mv tests/test_database.py tests/test_store.py`
- Modify: `tools/helpers/collect.py`, `tools/helpers/classify.py`, `tools/core/dependencies.py`, `tools/helpers/sqlQuery.py`, `tools/langchain_tools.py`, `seeder.py`, `eval.py`, `wipe.py`, `flush.py`, `import_list.py`, `tests/conftest.py`, `tests/test_export.py`, `tests/test_concurrency.py`

**Interfaces:**
- Produces:
  - `inex.errors.InexError(code: str, message: str, **details)` with attributes `code`, `message`, `details`.
  - `inex.store.schema`: `TABLES: tuple[str, ...]`, `ADDED_COLUMNS: tuple[tuple[str, str, str], ...]` as `(table, column, type)`, `INDEXES: tuple[str, ...]`, `TABLE_NAMES: tuple[str, ...]` in drop order, `ensure_schema(connection) -> None`.
  - `inex.store.database.Store(path: str | Path)` with `path: Path` and methods:
    `close()`, `save_project(owner, repo) -> int`, `project_id(owner, repo) -> int | None`,
    `save_issue(project_id, issue_data: dict) -> int`, `issue_id(project_id, number) -> int | None`,
    `count_existing_issues(project_id, numbers: list[int]) -> int`,
    `save_classification(issue_id, classification_data: dict, model, prompt, temp, classified_at) -> int`,
    `has_classification(issue_id) -> bool`,
    `count_ok_classifications(project_id, numbers, model, prompt_version, temperature) -> int`,
    `set_package_name(project_id, package_name)`, `package_name(project_id) -> str | None`,
    `project_has_classifications(project_id) -> bool`,
    `classified_issue_window(project_id, start=None, end=None) -> list[sqlite3.Row]`,
    `version_id(project_id, version) -> int | None`,
    `save_version(project_id, package_name, version, published_at, counts, raw_manifest) -> int`,
    `save_dependencies(version_id, rows)`, `link_issue_to_version(issue_id, version_id)`,
    `unclassified_by_ids(issue_ids, model, prompt_version, temperature) -> list[sqlite3.Row]`,
    `count_unclassified(project_id, model, prompt_version, temperature, start=None, end=None) -> int`,
    `unclassified_in_window(project_id, model, prompt_version, temperature, start=None, end=None, direction="desc", limit=None) -> list[sqlite3.Row]`,
    `run_readonly(sql: str, limit: int) -> tuple[list[str], list[tuple]]`,
    `fetch_readonly(sql: str) -> tuple[list[str], list[tuple]]`, `reset()`, `clear()`.
  - `run_readonly`/`fetch_readonly` raise `InexError("sql_error", str(error))` on any `sqlite3.Error`.

- [ ] **Step 1: Write the failing tests** in `tests/test_store.py` (rename the existing tests' calls to the new method names; keep their assertions) and add:

```python
LEGACY_CLASSIFICATIONS = """CREATE TABLE classifications(
    id INTEGER PRIMARY KEY AUTOINCREMENT, issue_id INTEGER NOT NULL, classification TEXT,
    classification_probabilities TEXT, classification_raw_response TEXT, model TEXT,
    prompt_version TEXT, temperature REAL, classified_at TEXT)"""

def test_legacy_database_gains_columns_and_unique_index_without_row_changes(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE projects(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, repo TEXT NOT NULL, added_at TEXT, UNIQUE(owner, repo))")
    raw.execute(LEGACY_CLASSIFICATIONS)
    raw.execute("INSERT INTO classifications(issue_id, classification, model, prompt_version, temperature) VALUES (1, 'Intrinsic', 'qwen3:30b', 'p', 0.2)")
    raw.commit(); raw.close()
    store = Store(path)
    cols = {r[1] for r in store.fetch_readonly("PRAGMA table_info(classifications)")[1]}
    assert {"status", "error", "validation_flags", "argmax_label", "attempts"} <= cols
    indexes = {r[1] for r in store.fetch_readonly("PRAGMA index_list(classifications)")[1]}
    assert "ux_classifications_run" in indexes
    assert store.fetch_readonly("SELECT issue_id, classification, model, prompt_version, temperature, status FROM classifications")[1] == [(1, "Intrinsic", "qwen3:30b", "p", 0.2, None)]
    first = store.save_classification(2, {"classification": "Extrinsic", "classification_probabilities": {}}, "m", "p", 0.2, "2026-01-01")
    again = store.save_classification(2, {"classification": "Intrinsic", "classification_probabilities": {}}, "m", "p", 0.2, "2026-01-02")
    assert first == again

def test_paths_with_spaces_and_hash_open_both_ways(tmp_path):
    store = Store(tmp_path / "ICSME (2)" / "db #1.db")
    store.save_project("acme", "widget")
    assert store.run_readonly("SELECT owner FROM projects", 50)[1] == [("acme",)]
```

Plus one test each, with literal expectations: `project_id` and `issue_id` return `None` when absent; `count_existing_issues(pid, [101, 102, 999]) == 2` on the `seeded` fixture; `count_ok_classifications` counts only `status` NULL or `'ok'` rows for the given run key; `has_classification` before and after a save; `run_readonly` on 60 rows returns 50 rows; `run_readonly("INSERT INTO projects(owner, repo) VALUES ('a','b')", 50)` raises `InexError` with `code == "sql_error"`; `reset()` leaves `projects` empty with the schema present; `clear()` empties every table and resets `sqlite_sequence`.

The `paths with spaces` test needs the parent folder created: `Store` creates missing parent directories.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_store.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'inex'`.

- [ ] **Step 3: Implement `inex/errors.py`, `inex/store/schema.py` and `Store`.** `TABLES` carries today's full definitions (the five status columns, `package_name`, `version_id`, `peer_count`, `resolved_version`, `root_dep`, `UNIQUE(issue_id, model, prompt_version, temperature)`). `ADDED_COLUMNS` carries the ten columns from today's `ALTER` list. `INDEXES` carries the three `idx_vdeps_*` indexes and `CREATE UNIQUE INDEX IF NOT EXISTS ux_classifications_run ON classifications(issue_id, model, prompt_version, temperature)`. `ensure_schema` adds a column only when `PRAGMA table_info` lacks it. `Store.__init__` creates the parent directory, opens the read-write connection with `check_same_thread=False` and `row_factory = sqlite3.Row`, runs `ensure_schema`, and opens the read-only connection with `sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)`, which keeps plain tuples (no row factory). No public `cursor` or `connection` attribute; every method uses its own cursor; every write holds `self._write_lock`. The rule that failed rows never count stays as the module constant `SUCCEEDED = "(c.status IS NULL OR c.status = 'ok')"`.

- [ ] **Step 4: Switch every caller** to `from inex.store.database import Store` and the new method names. The raw call sites map as: `classify.py` project lookups and `dependencies.py` project lookup to `project_id`; `collect.py` existing-issue checks to `issue_id`; `langchain_tools.py` list preview to `project_id` plus `count_existing_issues`, import count to `count_ok_classifications`; `seeder.py` to `has_classification`; `sqlQuery.py` functions take the store as first argument (`runSql(store, query)`, `exportQuery(store, query, fmt, filename)`) and use `run_readonly`/`fetch_readonly`; `eval.py` to `close()`; `wipe.py` and `flush.py` to `Store(DB_PATH).reset()` and `.clear()`. `classify.py` passes `classified_at=` instead of `classifiedat=`. Update `tests/conftest.py` (`db` fixture builds `Store`), `tests/test_export.py` (uses a `Store` on a temp file instead of patching `ro_conn`) and `tests/test_concurrency.py`.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0, all tests pass.

- [ ] **Step 6: Verify no raw SQL remains outside the store.** `grep -rn "\.cursor\b\|\.connection\b\|sqlite3" --include=*.py tools *.py` Expected: no matches except `import sqlite3` in `eval.py` for its checkpointer (removed in Task 9).

- [ ] **Step 7: Commit** `git add -A inex tools tests seeder.py eval.py wipe.py flush.py import_list.py`; `git commit -m "Move all SQL into a Store with a single schema"`.

---

### Task 3: Settings and validators

**Files:**
- Create: `inex/config.py`, `inex/validators.py`, `tests/test_config.py`, `tests/test_validators.py`
- Modify: `tests/conftest.py` (move `ENV_VARS` here so Task 11 can share it)

**Interfaces:**
- Consumes: `InexError`.
- Produces:
  - `inex.config.REPO_ROOT: Path` (`Path(__file__).resolve().parents[1]`), `inex.config.Settings` (frozen dataclass, fields exactly as the spec's Settings table), `inex.config.load_settings(env_file: Path | None = Path(".env"), **overrides) -> Settings`.
  - `inex.validators`: `parse_repo(text) -> tuple[str, str]`, `parse_start(text) -> str | None`, `parse_end(text) -> str | None`, `parse_order(text) -> str`, `parse_count(value) -> int | None`, `parse_filename(text) -> str | None`, `parse_format(text) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
ENV_VARS = ["DATABASE_URL", "GITHUB_TOKEN", "INEX_ORCHESTRATOR", "INEX_CLASSIFIER", "INEX_TEMPERATURE",
            "INEX_WORKERS", "INEX_RETRIES", "INEX_PROFILE", "OLLAMA_BASE", "INEX_NUM_CTX"]

@pytest.fixture
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)

def test_defaults(clean_env):
    s = load_settings(env_file=None)
    assert s.database == Path("db/database.db")
    assert s.classifier == "google_genai:gemini-2.5-pro"
    assert s.orchestrator == "google_genai:gemini-2.5-flash"
    assert (s.temperature, s.classify_workers, s.classify_retries, s.num_ctx) == (0.2, 8, 5, 131072)
    assert s.ollama_base == "http://localhost:11434"

def test_bundled_files_resolve_from_the_repo_root_not_the_cwd(clean_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(env_file=None)
    assert s.system_prompt == REPO_ROOT / "prompts" / "system_prompt.md"
    assert s.profile == REPO_ROOT / "profiles" / "inex.yaml"
    assert s.logging_config == REPO_ROOT / "loggingConfigs" / "config.json"
    assert s.system_prompt.is_file()

def test_environment_overrides_defaults(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_WORKERS", "3")
    monkeypatch.setenv("DATABASE_URL", "db/other.db")
    s = load_settings(env_file=None)
    assert (s.classify_workers, s.database) == (3, Path("db/other.db"))

def test_keyword_overrides_beat_environment(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_CLASSIFIER", "qwen3:30b")
    assert load_settings(env_file=None, classifier="x:y").classifier == "x:y"

def test_none_override_keeps_the_environment_value(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_CLASSIFIER", "qwen3:30b")
    assert load_settings(env_file=None, classifier=None).classifier == "qwen3:30b"

def test_malformed_number_is_an_invalid_setting(clean_env, monkeypatch):
    monkeypatch.setenv("INEX_WORKERS", "eight")
    with pytest.raises(InexError) as e:
        load_settings(env_file=None)
    assert e.value.code == "invalid_setting"

def test_loading_settings_creates_nothing(clean_env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_settings(env_file=tmp_path / ".env")
    assert list(tmp_path.iterdir()) == []
```

`test_bundled_files_resolve...` asserts `system_prompt.is_file()` only; `profile` exists after Task 12.

Validators, table-driven with literal values:

```python
@pytest.mark.parametrize("text, expected", [("facebook/react", ("facebook", "react")),
                                            (" facebook/react/ ", ("facebook", "react"))])
def test_parse_repo(text, expected): assert parse_repo(text) == expected

@pytest.mark.parametrize("text", ["react", "a/b/c", "/react", "facebook/", "", None])
def test_parse_repo_rejects(text): ...  # raises InexError code "invalid_input"

def test_dates():
    assert parse_start("2020-01-05") == "2020-01-05"
    assert parse_end("2020-01-31") == "2020-01-31T23:59:59Z"
    assert parse_end("2020-01-31T10:00:00Z") == "2020-01-31T10:00:00Z"
    assert parse_start(None) is None and parse_end(None) is None

@pytest.mark.parametrize("text", ["2020-1-5", "31/01/2020", "yesterday"])
def test_bad_dates_rejected(text): ...  # parse_start and parse_end raise invalid_input
```

Also: `parse_order(None) == "desc"`, `parse_order("ASC") == "asc"`, `parse_order("up")` raises; `parse_count("5") == 5`, `parse_count(None) is None`, `parse_count(0)`, `parse_count(-1)` and `parse_count("x")` raise; `parse_filename("results") == "results"`, `parse_filename(None) is None`, `parse_filename("../x")`, `parse_filename("a/b")`, `parse_filename("a\\b")` and `parse_filename(str(tmp_path / "abs"))` raise; `parse_format(None) == "csv"`, `parse_format("JSON") == "json"`, `parse_format("xml")` raises.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_config.py tests/test_validators.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `inex/config.py`.** `load_settings` calls `load_dotenv(env_file, override=False)` only when `env_file` is given and exists, reads each variable in the spec's Settings table, casts numbers (a failed cast raises `InexError("invalid_setting", f"{name} must be a number, got {value!r}")`), then applies keyword overrides whose value is not `None`. Bundled defaults are `REPO_ROOT / "prompts" / "system_prompt.md"`, `REPO_ROOT / "profiles" / "inex.yaml"`, `REPO_ROOT / "loggingConfigs" / "config.json"`; data defaults are the relative paths in the spec table.

- [ ] **Step 4: Implement `inex/validators.py`.** Dates are checked with `date.fromisoformat` for `YYYY-MM-DD` (exactly 10 characters) and `datetime.fromisoformat` (after replacing a trailing `Z` with `+00:00`) for full timestamps; full timestamps are returned unchanged. A bare end date gets `END_OF_DAY = "T23:59:59Z"` appended. Every rejection raises `InexError("invalid_input", <message naming the field and the expected form>)`.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add inex/config.py inex/validators.py tests/test_config.py tests/test_validators.py`; `git commit -m "Add Settings and shared input validators"`.

---

### Task 4: npm client and linkage service

**Files:**
- Create: `inex/clients/__init__.py`, `inex/services/__init__.py` (empty)
- Move: `git mv tools/core/npm_registry.py inex/clients/npm.py`; `git mv tools/core/dependencies.py inex/services/linkage.py`
- Modify: `tools/langchain_tools.py`, `tests/test_linkage.py`

**Interfaces:**
- Consumes: `Store`, `InexError`.
- Produces:
  - `inex.clients.npm.NpmClient(cache_dir: Path)` with `package_document(name: str) -> dict | None` (cache then registry; owns its `requests.Session`; `REGISTRY_BASE = "https://registry.npmjs.org"`, `HTTP_TIMEOUT_SECONDS = 30`).
  - `inex.services.linkage`: `parse_timestamp(value) -> datetime | None`, `is_prerelease(version) -> bool`, `sort_versions_desc(versions: list[str]) -> list[str]`, `live_version_at(issue_created_at, versions_with_times) -> tuple[str, str] | None`, `LinkResult` (frozen dataclass: `package_name, issue_versions_snapshotted, slice_versions_snapshotted, linked_issues, already_linked, unlinkable_pre_release`), `LinkageService(store, npm)` with `link(owner, repo, package, *, start=None, end=None, version_range=None, last_n_versions=None, version_start=None, version_end=None) -> LinkResult`.

- [ ] **Step 1: Write the failing tests.** Update `tests/test_linkage.py` imports to `parse_timestamp`, `is_prerelease`, `sort_versions_desc`, `live_version_at` (the `pick` fixture calls `live_version_at` directly; assertions unchanged). Add `tests/test_linkage_service.py`:

```python
class FakeNpm:
    def __init__(self, doc): self.doc, self.asked = doc, []
    def package_document(self, name):
        self.asked.append(name); return self.doc

DOC = {"versions": {"1.0.0": {"dependencies": {"a": "^1"}, "devDependencies": {"b": "^2"}},
                    "2.0.0": {"dependencies": {"a": "^1", "c": "^3"}}},
       "time": {"1.0.0": "2023-01-15T00:00:00.000Z", "2.0.0": "2023-02-15T00:00:00.000Z"}}

def test_links_each_classified_issue_to_the_live_release(seeded_classified):
    store, pid, ids = seeded_classified   # issues created 2023-01-01, 2023-02-01, 2023-03-01, all classified ok
    result = LinkageService(store, FakeNpm(DOC)).link("acme", "widget", "widget")
    assert result == LinkResult(package_name="widget", issue_versions_snapshotted=2, slice_versions_snapshotted=0,
                                linked_issues=2, already_linked=0, unlinkable_pre_release=1)
```

`seeded_classified` is a new fixture in `tests/conftest.py` built on `seeded` that saves an `ok` classification for each issue. Also: `link` on an unknown project raises `project_not_found`; on a project with no `ok` classifications raises `no_classifications`; with `FakeNpm(None)` raises `npm_package_not_found`; `version_range="9.x"` raises `no_versions_match_range`; a second `link` call reports `already_linked=2, linked_issues=0`.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_linkage.py tests/test_linkage_service.py -v`. Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** `NpmClient` replaces `fetchFull` (same cache file naming: `name.replace("/", "__") + ".json"`). `LinkageService.link` keeps `snapshot_for_classified`'s logic; every `{"status": "error", "code": X}` return becomes `raise InexError(X, <message>)`; `resolve_package_name` becomes a private method using `store.package_name`/`set_package_name` and `npm.package_document`. `sort_versions_desc` and the timestamp helpers move here from the client because they are rules.

- [ ] **Step 4: Update `tools/langchain_tools.py`** so `snapshot_dependencies` builds `LinkageService(db, NpmClient(Path(".npm_doc_cache")))` and returns `{"status": "ok", **asdict(result)}`, or `{"status": "error", "code": e.code, "message": e.message}` on `InexError`. This wiring is temporary until Task 9.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add -A inex tools tests`; `git commit -m "Move dependency linkage into LinkageService and an npm client"`.

---

### Task 5: Collection service

**Files:**
- Move: `git mv tools/helpers/collect.py inex/services/collection.py`
- Modify: `ruff.toml` (add `"inex/services/collection.py" = ["N"]`), `tools/langchain_tools.py`, `import_list.py`, `tests/test_collect.py`

**Interfaces:**
- Consumes: `Store`, `InexError`.
- Produces: `inex.services.collection`: `CountResult(owner, repo, issue_count)`, `CollectResult(collected, skipped)`, `SpecificResult(project_id, issue_ids, collected, skipped, failed)` (frozen dataclasses; `issue_ids` a tuple), `CollectionService(store, github_token: str | None)` with `count(owner, repo) -> CountResult`, `collect(owner, repo, *, count=None, start=None, end=None, order="desc") -> CollectResult`, `collect_specific(owner, repo, numbers: list[int]) -> SpecificResult`. Internal camelCase methods (`get`, `collectIssue`, `countIssues` logic) keep their names.

- [ ] **Step 1: Write the failing tests.** In `tests/test_collect.py`, the `collector` fixture becomes `CollectionService(db, "test-token")` with the same `get`/`collectIssue` monkeypatches; the three direction tests call `collector.collect("acme", "widget", end="2020-03-31T23:59:59Z", order="asc")` etc. with unchanged order assertions. Add:

```python
def test_collect_reports_counts_and_skips_on_rerun(collector, db):
    assert collector.collect("acme", "widget") == CollectResult(collected=5, skipped=0)
    assert collector.collect("acme", "widget") == CollectResult(collected=0, skipped=5)

def test_count_returns_a_result(graphql, search_refuses, db):
    graphql({"data": {"repository": {"issues": {"totalCount": 14779}}}})
    assert CollectionService(db, "t").count("facebook", "react") == CountResult("facebook", "react", 14779)

def test_missing_repository_raises(graphql, search_refuses, db): ...  # code "repo_not_found"

def test_missing_token_raises_before_any_request(graphql, db):
    sent = graphql({})
    with pytest.raises(InexError) as e:
        CollectionService(db, None).count("facebook", "react")
    assert e.value.code == "github_token_missing" and sent == []
```

Delete `TestCountIssuesTool` from this file (its replacement lands in Task 9).

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_collect.py -v`. Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** Rename `Collect` to `CollectionService`, inject `store` and `github_token` (remove `from config import GITHUB_TOKEN`), raise `github_token_missing` in `count`, `collect` and `collect_specific` when the token is falsy, and replace prose returns with the dataclasses. Name the numbers: `PER_PAGE = 100`, `REQUEST_TIMEOUT_SECONDS = 30`, `CLOSE_EVENT_WINDOW_SECONDS = 86400`, `REFERENCE_WINDOW_SECONDS = 604800`, `MAX_COMMIT_FILES = 20`.

- [ ] **Step 4: Update callers.** `tools/langchain_tools.py` `count_issues`, `collect_all` and `import_issue_list` use the service and convert results to dicts as in Task 4 Step 4; `import_list.py` keeps working through `import_issue_list`.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add -A inex tools tests ruff.toml import_list.py`; `git commit -m "Move issue collection into CollectionService"`.

---

### Task 6: Classification service

**Files:**
- Move: `git mv tools/helpers/classify.py inex/services/classification.py`
- Modify: `ruff.toml` (add `"inex/services/classification.py" = ["N"]`), `tools/langchain_tools.py`, `tests/conftest.py`, `tests/test_classify.py`, `tests/test_concurrency.py`

**Interfaces:**
- Consumes: `Store`, `InexError`.
- Produces: `inex.services.classification`: `ClassifyPreview(owner, repo, would_classify, total_remaining, model, prompt_version, earliest_created, latest_created, sample)` (`sample` a tuple of `{"issue_number", "title"}` dicts), `ClassifyResult(owner, repo, classified, failed)` (`failed` a dict status to count), `ClassificationService(store, guide_path: Path, model: str, temperature: float, *, workers: int = 1, retries: int = 5, ollama_base: str = "http://localhost:11434")` with `preview(owner, repo, *, count=None, start=None, end=None, order="desc") -> ClassifyPreview`, `classify(owner, repo, *, count=None, start=None, end=None, order="desc") -> ClassifyResult`, `classify_ids(owner, repo, issue_ids) -> ClassifyResult`. Internals (`callModel`, `callOllama`, `_isRetryable`, `validateResult`, `formatIssueData`, `_prepareIssue`, `_classifyRows`, `useStructured`, `prompt_version`) keep their names; `_classifyRows(owner, repo, rows)` uses `self.store`.

- [ ] **Step 1: Write the failing tests.** Fixtures in `conftest.py` construct `ClassificationService(None, PROMPT, "qwen3:30b", 0.2, workers=1)`; `test_concurrency.make` passes the recording store as the first argument and calls `cl._classifyRows("acme", "widget", rows)`. Replace the two `_summarise` tests with:

```python
def test_classify_reports_failures_by_status(seeded):
    db, pid, ids = seeded
    outcomes = iter([_ok(), ParsedResult(status="api_error", error="boom"), _ok()])
    cl = ClassificationService(db, PROMPT, "qwen3:30b", 0.2)
    cl.classifyOne = lambda issue: next(outcomes)
    assert cl.classify("acme", "widget") == ClassifyResult("acme", "widget", classified=2, failed={"api_error": 1})

def test_unknown_project_raises(db): ...   # classify and preview raise code "project_not_found"

def test_ollama_base_is_used(monkeypatch):
    seen = []
    class EmptyResponse:
        def raise_for_status(self): pass
        def json(self): return {"response": ""}
    monkeypatch.setattr(mod.requests, "post", lambda url, **k: seen.append(url) or EmptyResponse())
    cl = ClassificationService(None, PROMPT, "qwen3:30b", 0.2, retries=1, ollama_base="http://example:1234")
    assert cl.callModel("x").status == "empty_response"
    assert seen == ["http://example:1234/api/generate"]

def test_preview_counts_backlog_and_caps_the_batch(seeded): ...
    # preview(count=2) -> would_classify == 2, total_remaining == 3, len(sample) == 2
```

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_classify.py tests/test_concurrency.py -v`. Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** Rename `Classify` to `ClassificationService` with the constructor above (the `retries` argument still sets the `retry_attempts` attribute the tests use); public methods replace `classifyAll`, `classifyByIds`, `previewClassification`; project lookup uses `store.project_id` and raises `project_not_found`; `_summarise` is deleted and `ClassifyResult` is built from the tally (`classified = tally.get("ok", 0)`, `failed` = the other entries). The Ollama URL is `f"{ollama_base}/api/generate"`. Name the numbers: `CONTEXT_WINDOW_TOKENS = 30000`, `MAX_OUTPUT_TOKENS = 25000`, `MODEL_TIMEOUT_SECONDS = 600`, `CLOUD_SDK_RETRIES = 3`, `PROB_SUM_TOLERANCE = 0.02`, `PROMPT_HASH_CHARS = 12`, `RETRY_WAIT_INITIAL_SECONDS = 4`, `RETRY_WAIT_MAX_SECONDS = 120`, `RETRY_WAIT_JITTER_SECONDS = 4`, `MAX_PR_BODY_CHARS = 15000`, `MAX_COMMIT_MESSAGE_CHARS = 2000`, `MAX_MAINTAINER_COMMENT_CHARS = 20000`, `MAX_OTHER_COMMENT_CHARS = 8000`, `MAX_OTHER_COMMENTS = 50`, `MAX_BODY_CHARS = 80000`, `PROGRESS_EVERY = 25`. The rendered prompt text must not change.

- [ ] **Step 4: Update `tools/langchain_tools.py`** (`classify_all`, `preview_classification`, `import_issue_list`) to the new service and dict conversion.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add -A inex tools tests ruff.toml`; `git commit -m "Move classification into ClassificationService"`.

---

### Task 7: Query, import and maintenance services

**Files:**
- Move: `git mv tools/helpers/sqlQuery.py inex/services/queries.py`; `git mv tools/helpers/issueList.py inex/services/imports.py`; `git mv tests/test_export.py tests/test_queries.py`
- Create: `inex/services/maintenance.py`, `tests/test_imports.py`
- Delete: `seeder.py` (its logic moves to `ImportService.load_jsonl`)
- Modify: `tools/langchain_tools.py`, `import_list.py`

**Interfaces:**
- Consumes: `Store`, `CollectionService.collect_specific`, `ClassificationService.classify_ids`, `InexError`.
- Produces:
  - `inex.services.queries`: `MAX_QUERY_ROWS = 50`, `QueryResult(columns, rows, truncated)` (`rows` a list of lists), `ExportResult(path: str, rows_exported, columns, format)`, `QueryService(store, exports_dir: Path)` with `max_rows: int`, `run(sql) -> QueryResult`, `export(sql, fmt="csv", filename=None) -> ExportResult`.
  - `inex.services.imports`: `parse_issue_list(csv_path, column=None) -> tuple[list[tuple[str, str, int]], list[str]]`, `ListPreview(total_issues, distinct_repos, already_in_db, to_collect, repos, unparseable_count, unparseable_sample)`, `RepoImport(repo, collected, skipped, failed, classified)`, `ImportResult(collected, skipped, failed, classified, repos)`, `LoadResult(files, issues, classifications)`, `ImportService(store, collection, classification)` with `preview_list(csv_path, column=None) -> ListPreview`, `import_list(csv_path, column=None, classify=True) -> ImportResult`, `load_jsonl(path, model, prompt_version, temperature) -> LoadResult`.
  - `inex.services.maintenance`: `DatabaseResult(path: str)`, `MaintenanceService(store)` with `init() -> DatabaseResult`, `reset() -> DatabaseResult`, `clear() -> DatabaseResult`.

- [ ] **Step 1: Write the failing tests.** `tests/test_queries.py` (exports go to `tmp_path / "exports"`; keep the three existing export cases with `InexError` code `invalid_filename` and nothing written), plus: `run` over 60 rows returns 50 rows and `truncated is True`; `run("DELETE FROM t")` raises `not_a_select`; `run("SELECT 1; SELECT 2")` raises `multiple_statements`; `run("SELECT nope FROM t")` raises `sql_error`; `export(..., fmt="xml")` raises `invalid_format`; `QueryService(store, d).max_rows == 50`.

`tests/test_imports.py`:

```python
def write(path, text): path.write_bytes(text.encode("utf-8"))

def test_excel_csv_parses_like_a_clean_one(tmp_path):
    clean, excel = tmp_path / "clean.csv", tmp_path / "excel.csv"
    write(clean, "ref\nacme/widget#1\nacme/other#2\n")
    write(excel, "\ufeffref\r\n  acme/widget#1  \r\nacme/other#2\r\n")
    assert parse_issue_list(excel) == parse_issue_list(clean) == ([("acme", "widget", 1), ("acme", "other", 2)], [])

def test_preview_list(seeded, tmp_path):
    db, pid, ids = seeded
    write(tmp_path / "l.csv", "ref\nacme/widget#101\nacme/widget#900\nzed/z#1\nnot a ref\n")
    p = ImportService(db, None, None).preview_list(tmp_path / "l.csv")
    assert (p.total_issues, p.distinct_repos, p.already_in_db, p.to_collect, p.unparseable_count) == (3, 2, 1, 2, 1)
```

Also: `import_list` with a fake collection (returns `SpecificResult`) and a fake classification (records `classify_ids` calls and returns a `ClassifyResult`) totals per repo; `classify=False` never calls the fake classification; a missing file raises `file_not_found`; a CSV with no valid rows raises `no_valid_entries`; `load_jsonl` on a file with two issue lines, one carrying `"classification": "Intrinsic"`, returns `LoadResult(files=1, issues=2, classifications=1)`, and loading it again returns `classifications=0`; `load_jsonl` on a directory finds nested `*.jsonl` files.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_queries.py tests/test_imports.py -v`. Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement.** `QueryService` keeps today's SELECT-only and single-statement checks and the filename check (now `InexError`s), writes exports under `exports_dir` (created on first export), and returns `path` as a string. `ImportService.import_list` reuses the logic of today's `import_issue_list` tool; `load_jsonl` reuses `seeder.py`'s loop, calls `store.save_classification(issue_id, line, model, prompt_version, temperature, line.get("classified_at") or now)`, and skips the classification when `store.has_classification(issue_id)`. `MaintenanceService.init` returns `DatabaseResult(str(store.path))` (the store already ensured the schema).

- [ ] **Step 4: Update `tools/langchain_tools.py`** (`run_sql`, `export_data`, `preview_issue_list`, `import_issue_list`) to the services.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add -A inex tools tests seeder.py import_list.py`; `git commit -m "Add query, import and maintenance services"`.

---

### Task 8: Composition root

**Files:**
- Create: `inex/app.py`, `tests/test_app.py`
- Modify: `tests/conftest.py` (add a `settings` fixture)

**Interfaces:**
- Consumes: every service, `Store`, `NpmClient`, `Settings`.
- Produces: `inex.app.App` (dataclass: `settings, collection, classification, imports, linkage, queries, maintenance`, plus `profile` from Task 13) with `close() -> None`; `inex.app.build_app(settings: Settings) -> App`. Until Task 13, `build_app` passes `REPO_ROOT / "prompts" / "classification_prompt.md"` as `guide_path`.

- [ ] **Step 1: Write the failing tests.**

```python
@pytest.fixture
def settings(tmp_path):
    return load_settings(env_file=None, database=tmp_path / "t.db", exports_dir=tmp_path / "exports",
                         npm_cache_dir=tmp_path / "npm", checkpoints=tmp_path / "c.db", github_token="t")

def test_build_app_wires_one_store(settings):
    app = build_app(settings)
    assert settings.database.is_file()
    assert app.queries.max_rows == 50
    assert app.classification.store is app.collection.store is app.imports.store
    app.close()

def test_services_receive_settings(settings):
    app = build_app(settings)
    assert (app.classification.model_config["model_name"], app.classification.workers) == (settings.classifier, settings.classify_workers)
    app.close()

def test_unknown_project_flows_through_as_inex_error(settings):
    app = build_app(settings)
    with pytest.raises(InexError) as e:
        app.classification.preview("nope", "nope")
    assert e.value.code == "project_not_found"
    app.close()
```

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_app.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'inex.app'`.

- [ ] **Step 3: Implement `build_app`** constructing one `Store(settings.database)`, `NpmClient(settings.npm_cache_dir)`, and each service from the matching settings fields. `App.close` closes the store.

- [ ] **Step 4: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 5: Commit** `git add inex/app.py tests/test_app.py tests/conftest.py`; `git commit -m "Add build_app composition root"`.

---

### Task 9: Agent front end and routing eval

**Files:**
- Move: `git mv tools/langchain_tools.py inex/agent/tools.py`; `git mv agent.py inex/agent/chat.py`; `git mv eval.py evals/routing.py`
- Create: `inex/agent/__init__.py` (empty), `tests/test_agent_tools.py`
- Modify: `ruff.toml` (drop the `eval.py` entry)

**Interfaces:**
- Consumes: `App`, validators, `InexError`.
- Produces:
  - `inex.agent.tools.TOOL_NAMES: tuple[str, ...]` (the nine names), `inex.agent.tools.build_tools(app: App) -> list`.
  - `inex.agent.chat.build_agent(app: App, checkpointer)`, `inex.agent.chat.run_chat(app: App) -> None`.
  - Tool results: `{"status": "ok", **asdict(result)}` or `{"status": "error", "code": str, "message": str}`; unexpected exceptions are logged with `logger.exception` and return code `internal_error` with message `"Unexpected failure; see the log for details."`.

- [ ] **Step 1: Write the failing tests** in `tests/test_agent_tools.py` with an `App` built by `build_app(settings)` and then `dataclasses.replace(app, collection=FakeCollection(), classification=FakeClassification(), linkage=FakeLinkage())`:

```python
ARGS = {"count_issues": {"repo_name"},
        "collect_all": {"repo_name", "count", "start_date", "end_date", "direction"},
        "classify_all": {"repo_name", "count", "start_date", "end_date", "direction"},
        "preview_classification": {"repo_name", "count", "start_date", "end_date", "direction"},
        "snapshot_dependencies": {"repo_name", "npm_package", "start_date", "end_date", "version_range",
                                  "last_n_versions", "version_start", "version_end"},
        "run_sql": {"query"}, "export_data": {"query", "format", "filename"},
        "preview_issue_list": {"csv_path", "column"}, "import_issue_list": {"csv_path", "column", "classify"}}

def test_tool_names_and_arguments(app):
    tools = {t.name: t for t in build_tools(app)}
    assert set(tools) == set(ARGS)
    assert {n: set(t.args) for n, t in tools.items()} == ARGS

def test_success_is_a_status_dict(app):
    out = tool(app, "count_issues").invoke({"repo_name": "facebook/react"})
    assert out == {"status": "ok", "owner": "facebook", "repo": "react", "issue_count": 14779}

def test_service_error_becomes_an_error_dict(app):   # FakeCollection.count raises InexError("repo_not_found", "nope/nope was not found on GitHub.")
    assert tool(app, "count_issues").invoke({"repo_name": "nope/nope"}) == {"status": "error", "code": "repo_not_found", "message": "nope/nope was not found on GitHub."}

def test_bad_input_never_reaches_the_service(app):
    out = tool(app, "collect_all").invoke({"repo_name": "react"})
    assert out["code"] == "invalid_input" and app.collection.calls == []

def test_unexpected_failure_is_internal_error(app, caplog): ...   # FakeLinkage.link raises RuntimeError -> code "internal_error", traceback in caplog

def test_run_sql_description_states_the_real_cap(app):
    assert "capped at 50 rows" in tool(app, "run_sql").description
```

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_agent_tools.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `build_tools`.** Each tool is a closure over `app`; inputs go through the validators; one wrapper does all result and error conversion. Descriptions are today's text with the normalised argument names; `run_sql`'s cap comes from `app.queries.max_rows`; `describe_schema` is gone; no module-level objects.

- [ ] **Step 4: Implement `inex/agent/chat.py`.** `build_agent(app, checkpointer)` builds the orchestrator (`ollama:` prefix via `ChatOllama(model, temperature, num_ctx)`, otherwise `init_chat_model`), reads `app.settings.system_prompt`, and calls `create_agent` with `build_tools(app)`. `run_chat(app)` opens `SqliteSaver.from_conn_string(str(app.settings.checkpoints))` in a `with` block and runs the `You:` loop (`q` quits), creating the checkpoints folder first.

- [ ] **Step 5: Rework `evals/routing.py`** to build its agent with `build_agent(app, checkpointer)` where `app` comes from `build_app(load_settings(database=<temp seeded db>))` with fake collection, classification and linkage services returning the existing mock values as result dataclasses, and `SqliteSaver.from_conn_string(":memory:")`. The five `patch(...)` calls go away. Verify it imports: `./venv/Scripts/python.exe -c "import evals.routing"` Expected: no output.

- [ ] **Step 6: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 7: Commit** `git add -A inex evals tests ruff.toml tools agent.py eval.py`; `git commit -m "Rebuild the agent tools and chat on build_app"`.

---

### Task 10: CLI and removal of the old layout

**Files:**
- Create: `inex/cli.py`, `inex/__main__.py`, `tests/test_cli.py`
- Delete: `tools/` (remaining files), `config.py`, `wipe.py`, `flush.py`, `import_list.py`
- Modify: `ruff.toml` (remove `tools/**`, `config.py`, `seeder.py`, `import_list.py` entries)

**Interfaces:**
- Consumes: `load_settings`, `build_app`, validators, `run_chat`, `InexError`.
- Produces: `inex.cli.build_parser() -> argparse.ArgumentParser`; `inex.cli.main(argv: list[str] | None = None, build=build_app) -> int`. Exit codes `0`, `1`, `2` (argparse), `130` (Ctrl+C).

Output copy (exact):

| Command | Summary line |
|---|---|
| `count` | `{owner}/{repo} has {issue_count} issues.` |
| `collect` | `Collected {collected} issues from {owner}/{repo} ({skipped} already in the database).` |
| `classify` | `Classified {classified} issues from {owner}/{repo}.` plus, when `failed` is non-empty, ` {total} failed and were not labelled ({status}={n}, ...); re-run to retry them.` with statuses sorted |
| `classify --preview` | `{would_classify} of {total_remaining} unclassified issues in {owner}/{repo} would be classified with {model} ({prompt_version}).` |
| `link` | `Linked {linked_issues} issues in {owner}/{repo} to {package_name} releases; {unlinkable_pre_release} predate the first release.` |
| `import --preview` | `{total_issues} issues across {distinct_repos} repositories: {already_in_db} already in the database, {to_collect} to collect, {unparseable_count} unreadable rows.` |
| `import` | `Collected {collected}, skipped {skipped}, failed {failed}, classified {classified}.` |
| `load` | `Loaded {issues} issues and {classifications} classifications from {files} files.` |
| `query` | tab-separated header and rows, then `(truncated at {max_rows} rows)` when truncated |
| `export` | `Exported {rows_exported} rows to {path}.` |
| `db init` / `reset` / `clear` | `Database ready at {path}.` / `Database reset at {path}.` / `Database cleared at {path}.` |
| `InexError` | stderr `error: {message}` |
| unexpected | stderr `error: unexpected failure; see logs/tool.log` |
| Ctrl+C | stderr `interrupted` |

- [ ] **Step 1: Write the failing tests** in `tests/test_cli.py`. The `cli` fixture changes into `tmp_path` (so no real `.env` is loaded and logs land in the temp folder) and calls `main(argv, build=...)` with a build function that appends the `Settings` it receives to a `built` list and returns `build_app(settings)` with the Task 9 fakes swapped in (move those fakes to `tests/fakes.py`):

```python
def test_count_prints_the_summary(cli, capsys):
    assert cli(["count", "facebook/react"]) == 0
    assert capsys.readouterr().out == "facebook/react has 14779 issues.\n"

def test_json_output_is_valid_json_for_every_command(cli, capsys, tmp_path):
    for argv in (["count", "facebook/react"], ["export", "SELECT 1 AS x", "--name", "r"], ["db", "init"]):
        assert cli(["--json", *argv]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == "ok"

def test_json_error_shape(cli, capsys):
    assert cli(["--json", "count", "nope/nope"]) == 1
    assert json.loads(capsys.readouterr().out) == {"status": "error", "code": "repo_not_found", "message": "nope/nope was not found on GitHub."}

def test_invalid_input_exits_1_with_message(cli, capsys):
    assert cli(["collect", "react"]) == 1
    assert capsys.readouterr().err.startswith("error: ")

def test_preview_flag_calls_preview_not_classify(cli, app_spy): ...
def test_db_reset_requires_yes(cli): assert cli(["db", "reset"]) == 2
def test_db_flag_reaches_settings(cli, built): cli(["--db", "x/y.db", "db", "init"]); assert built[0].database == Path("x/y.db")
def test_ctrl_c_exits_130(cli, capsys): ...   # fake collect raises KeyboardInterrupt -> 130, stderr "interrupted\n", no "Traceback"
def test_unexpected_error_exits_1_and_logs(cli, capsys, caplog): ...
def test_load_requires_model_and_prompt_version(cli): assert cli(["load", "x.jsonl"]) == 2
```

Also run the module entry point in a subprocess: `[sys.executable, "-m", "inex", "--help"]` exits 0 and its output names all ten commands (`count`, `collect`, `classify`, `link`, `import`, `load`, `query`, `export`, `chat`, `db`).

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_cli.py -v`. Expected: FAIL with `ModuleNotFoundError: No module named 'inex.cli'`.

- [ ] **Step 3: Implement `inex/cli.py` and `inex/__main__.py`.** Global flags `--db`, `--classifier`, `--profile`, `--json` become `load_settings` overrides. `main` creates the log folder and applies `logging.config.dictConfig` from `settings.logging_config` when that file exists, validates, calls one service, prints the summary or `{"status": "ok", **asdict(result)}` with `json.dumps(..., default=str)`, and maps `InexError` to exit 1, `KeyboardInterrupt` to 130, any other exception to `logger.exception` plus exit 1. `db reset` and `db clear` declare `--yes` as `required=True`. `load` declares `--model` and `--prompt-version` as required and `--temperature` defaulting to `settings.temperature`. `chat` calls `run_chat(app)`. `__main__.py` is `raise SystemExit(main())`.

- [ ] **Step 4: Delete the old layout** with `git rm -r tools config.py wipe.py flush.py import_list.py` and remove their `ruff.toml` entries. Verify: `grep -rn "from config import\|from tools\." --include=*.py .` (excluding `venv`) Expected: no matches.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Smoke test against a scratch database.** `./venv/Scripts/python.exe -m inex --db "$TMP/inex-smoke.db" db init` Expected: `Database ready at ...`. `./venv/Scripts/python.exe -m inex --db "$TMP/inex-smoke.db" query "SELECT COUNT(*) FROM issues"` Expected: a header line and `0`.

- [ ] **Step 7: Commit** `git add -A inex tests ruff.toml` and the deletions; `git commit -m "Add the inex command line and remove the old layout"`.

---

### Task 11: Architecture tests

**Files:**
- Create: `tests/test_architecture.py`

**Interfaces:**
- Produces: module-level data `LAYER_IMPORTS: dict[str, set[str]]`, `THIRD_PARTY_HOMES: dict[str, set[str]]`, `EXCEPTIONS: dict[str, set[str]]`, and pure checkers `layer_violations(module_path: Path, source: str) -> list[str]`, `environment_violations(module_path: Path, source: str) -> list[str]`, `comment_violations(path: Path, source: str) -> list[str]`.

Data (exact):

```python
LAYER_IMPORTS = {
    "__main__": {"cli"}, "cli": {"app", "validators", "errors", "config", "agent.chat"},
    "agent": {"app", "validators", "errors", "config", "agent"},
    "app": {"config", "services", "store", "clients", "errors"}, "validators": {"errors"},
    "services": {"store", "clients", "errors", "services"}, "store": {"errors", "store"},
    "clients": {"errors", "clients"}, "config": {"errors"}, "errors": set(),
}
THIRD_PARTY_HOMES = {"sqlite3": {"store"}, "requests": {"clients"}, "langchain": {"agent"},
                     "langgraph": {"agent"}, "dotenv": {"config"}}
EXCEPTIONS = {"inex/services/collection.py": {"requests"},
              "inex/services/classification.py": {"requests", "langchain"}}
```

A module's layer is its first path part under `inex/` (`inex/agent/tools.py` is `agent`; `inex/cli.py` is `cli`). Any top-level import name starting with `langchain` counts as `langchain`. `cli`'s `agent.chat` entry allows `inex.agent.chat` only.

- [ ] **Step 1: Write the tests.** Each checker gets one synthetic violation test and one real-repo test:

```python
def test_layer_checker_catches_sqlite_in_a_service():
    assert layer_violations(Path("inex/services/x.py"), "import sqlite3\n")

def test_layer_checker_catches_store_importing_services():
    assert layer_violations(Path("inex/store/x.py"), "from inex.services.queries import QueryService\n")

def test_layer_checker_honours_the_exception_list():
    assert layer_violations(Path("inex/services/collection.py"), "import requests\n") == []

def test_repository_respects_the_layers():
    assert [v for p in inex_modules() for v in layer_violations(p, p.read_text(encoding="utf-8"))] == []

def test_environment_checker_catches_getenv():
    assert environment_violations(Path("inex/services/x.py"), "import os\nos.getenv('X')\n")

def test_only_config_reads_the_environment(): ...    # real repo, expect []

def test_importing_inex_creates_nothing(tmp_path):
    code = ("import importlib, pkgutil, inex\n"
            "[importlib.import_module(m.name) for m in pkgutil.walk_packages(inex.__path__, 'inex.')"
            " if m.name != 'inex.__main__']")
    env = {k: v for k, v in os.environ.items() if k not in ENV_VARS} | {"PYTHONPATH": str(REPO)}
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, check=True)
    assert list(tmp_path.iterdir()) == []

def test_comment_checker_catches_comments_and_docstrings():
    assert comment_violations(Path("x.py"), "x = 1  # note\n")
    assert comment_violations(Path("x.py"), 'def f():\n    """doc"""\n')
    assert comment_violations(Path("x.py"), "import os  # noqa: F401\n") == []

@pytest.mark.skip(reason="enabled in Task 14")
def test_no_comments_or_docstrings_anywhere(): ...   # inex/, tests/, evals/, check.py; expect []
```

`ENV_VARS` is shared with `tests/test_config.py` through `tests/conftest.py`.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests/test_architecture.py -v`. Expected: the synthetic tests FAIL with `NameError` for the missing checkers.

- [ ] **Step 3: Implement the checkers** with `ast` (imports, `os.environ`/`os.getenv` attribute access, `from os import environ, getenv`, any `dotenv` import) and `tokenize` (comment tokens not matching `^#\s*noqa`), plus `ast.get_docstring` on modules, classes and functions.

- [ ] **Step 4: Run** `./venv/Scripts/python.exe -m pytest tests/test_architecture.py -v`. Expected: PASS except the skipped test. Any real-repo violation is fixed in the offending module, not in the data.

- [ ] **Step 5: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 6: Commit** `git add tests/test_architecture.py tests/conftest.py` plus any fixed modules; `git commit -m "Enforce layers, environment access and import side effects"`.

---

### Task 12: Profile loading and golden fixtures

**Files:**
- Create: `inex/services/profiles.py`, `profiles/inex.yaml`, `profiles/example-severity.yaml`, `profiles/example-severity_guide.md`, `tests/test_profiles.py`, `tests/fixtures/inex_schema.json`, `tests/fixtures/inex_prompt_ollama.txt`, `tests/fixtures/inex_prompt_cloud.txt`, `tests/fixtures/sample_issue.json`
- Move: `git mv prompts/classification_prompt.md profiles/inex_guide.md`
- Modify: `inex/app.py` (guide path), `tests/conftest.py` (`PROMPT`)

**Interfaces:**
- Consumes: `InexError`.
- Produces: `inex.services.profiles`: `Label(name, description, slug)`, `ReasoningStep(field, title, description)`, `Profile(name, version, description, guide_path, guide_text, labels, reasoning, confidence, raw_bytes)` (frozen dataclasses; tuples for collections), `slugify(name) -> str`, `load_profile(path: Path) -> Profile`, `build_schema(profile) -> type[BaseModel]`, `match_label(profile, text) -> str | None` (the canonical label named after `**Final Answer:**`), `match_probabilities(profile, text) -> dict[str, float]` (one entry per `- <label>: <number>` line naming a profile label), `count_word(n) -> str`.

- [ ] **Step 1: Capture today's behaviour as fixtures.** First delete the docstring on `ClassificationResult` in `inex/services/classification.py`: Pydantic sends it to Gemini as the schema's `description`, so it is developer rationale leaking into every request, and Task 14 removes it anyway (its content goes to `docs/architecture.md`). This is the one intended change to what Gemini receives. Then, with the current classifier code, write `ClassificationResult.model_json_schema()` to `tests/fixtures/inex_schema.json` via `json.dumps(schema, indent=2)`, and the full prompt (`base_prompt + "\n" + formatIssueData(issue)`) for the issue in `tests/fixtures/sample_issue.json` under a `qwen3:30b` classifier and a `google_genai:gemini-2.5-pro` classifier, to the two prompt fixtures. `sample_issue.json` is the `ISSUE` dict used by the formatting tests in `tests/test_classify.py`, extended with a merged closing PR, one `MEMBER` comment and one `NONE` comment. Commit them first: `git add inex/services/classification.py tests/fixtures`; `git commit -m "Capture classifier schema and prompts as golden fixtures"`.

- [ ] **Step 2: Write the failing tests** in `tests/test_profiles.py`:

```python
INEX = REPO_ROOT / "profiles" / "inex.yaml"

def test_golden_schema_is_byte_identical():
    generated = json.dumps(build_schema(load_profile(INEX)).model_json_schema(), indent=2)
    assert generated == (FIXTURES / "inex_schema.json").read_text(encoding="utf-8")

def test_reasoning_fields_precede_probabilities_and_answer():
    fields = list(build_schema(load_profile(INEX)).model_fields)
    assert fields == ["definition_check", "maintainer_signal", "pr_commit_evidence", "temporal_signals",
                      "contract_rule", "information_completeness", "decision", "confidence",
                      "probabilities", "final_answer"]

@pytest.mark.parametrize("name, slug", [("Not a Bug", "not_a_bug"), ("Intrinsic", "intrinsic"), ("  P1 - Critical ", "p1_critical")])
def test_slugify(name, slug): assert slugify(name) == slug

@pytest.mark.parametrize("text, label", [("**Final Answer:** Not a Bug", "Not a Bug"),
                                         ("**Final Answer:** not-a-bug", "Not a Bug"),
                                         ("**Final Answer:**  NOT A BUG", "Not a Bug"),
                                         ("**Final Answer:** Extrinsic", "Extrinsic"),
                                         ("no answer here", None)])
def test_match_label(text, label): assert match_label(load_profile(INEX), text) == label

def test_match_probabilities():
    text = "- Intrinsic: 0.9\n- Not a Bug: 0.05\n- Extrinsic: .05\n- Bogus: 0.3"
    assert match_probabilities(load_profile(INEX), text) == {"Intrinsic": 0.9, "Not a Bug": 0.05, "Extrinsic": 0.05}

def test_example_profile_loads_with_three_labels():
    assert [l.name for l in load_profile(REPO_ROOT / "profiles" / "example-severity.yaml").labels] == ["Critical", "Major", "Minor"]
```

Load failures, each raising `InexError` with `code == "profile_invalid"` and the offending name in the message, from small YAML files written to `tmp_path`: fewer than two labels; duplicate label names; colliding slugs (`Not a Bug` and `not-a-bug`, both named in the message); no reasoning steps; duplicate field; a non-identifier field (`2bad`); a field named `probabilities`, `final_answer` or `confidence`; missing guide file; `confidence: []`; invalid YAML. Also `count_word(4) == "four"` and `count_word(11) == "11"`.

- [ ] **Step 3: Run** `./venv/Scripts/python.exe -m pytest tests/test_profiles.py -v`. Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write `profiles/inex.yaml`** with `name: inex`, `version: 1`, `guide: inex_guide.md`, the four labels and descriptions from the profiles spec, `confidence: [High, Medium, Low]`, and the seven reasoning steps with today's template titles and descriptions verbatim:

| field | title | description |
|---|---|---|
| definition_check | Definition Check | Identify which category definitions match the signals |
| maintainer_signal | Maintainer Signal | Note any authoritative maintainer comments about root cause |
| pr_commit_evidence | PR/Commit Evidence | If closing PR exists, describe what it changed and why |
| temporal_signals | Temporal Signals | Check for version changes, upgrades, "works on X fails on Y" patterns |
| contract_rule | Contract Rule | If API usage involved, determine who violated the contract |
| information_completeness | Information Completeness | Assess if there's enough detail to classify confidently |
| decision | Decision | State your classification and the primary evidence supporting it |

Write `example-severity.yaml` and its guide with three labels (`Critical`, `Major`, `Minor`) and three reasoning steps of its own (`impact`, `workaround`, `scope`).

- [ ] **Step 5: Implement `inex/services/profiles.py`.** `build_schema` uses `create_model` with the fixed model names `ClassificationResult` and `_Probabilities`, probability fields `float` with `Field(ge=0.0, le=1.0)` and no descriptions, reasoning fields `str`, then `confidence` as `Literal[...]` when configured, `probabilities`, and `final_answer` as `Literal[label names]`. `match_label` builds one case-insensitive pattern anchored on `\*\*Final\s*Answer:\*\*\s*` whose alternatives are the label names, regex-escaped, with internal whitespace relaxed to `[\s-]*`, sorted longest first; the match maps back to the canonical name by slug. `match_probabilities` does the same for lines of the form `-\s*<label>:\s*<number>`. `NUMBER_WORDS` covers 2 to 10.

- [ ] **Step 6: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0.

- [ ] **Step 7: Commit** `git add -A inex profiles prompts tests`; `git commit -m "Add classification profiles with golden schema tests"`.

---

### Task 13: Classification and chat driven by profiles

**Files:**
- Modify: `inex/services/classification.py`, `inex/app.py`, `inex/agent/chat.py`, `prompts/system_prompt.md`, `tests/conftest.py`, `tests/test_classify.py`, `tests/test_concurrency.py`, `tests/test_profiles.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `Profile`, `load_profile`, `build_schema`, `match_label`, `match_probabilities`, `count_word`.
- Produces: `ClassificationService(store, profile: Profile, model, temperature, *, workers=1, retries=5, ollama_base=...)`; `prompt_version == f"{profile.name}@{sha256(profile.raw_bytes + profile.guide_text.encode()).hexdigest()[:12]}+t{TEMPLATE_VERSION}"`; `App.profile: Profile`; `inex.agent.chat.render_system_prompt(template: str, profile) -> str`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_inex_prompts_are_unchanged(inex_profile):
    issue = json.loads((FIXTURES / "sample_issue.json").read_text(encoding="utf-8"))
    for model, fixture in (("qwen3:30b", "inex_prompt_ollama.txt"), ("google_genai:gemini-2.5-pro", "inex_prompt_cloud.txt")):
        cl = ClassificationService(None, inex_profile, model, 0.2)
        assert cl.base_prompt + "\n" + cl.formatIssueData(issue) == (FIXTURES / fixture).read_text(encoding="utf-8")

def test_prompt_version_names_the_profile(inex_profile):
    v = ClassificationService(None, inex_profile, "qwen3:30b", 0.2).prompt_version
    assert v.startswith("inex@") and v.endswith("+t2")

def test_validation_uses_a_three_label_profile(severity_profile):
    cl = ClassificationService(None, severity_profile, "qwen3:30b", 0.2)
    ok = cl.validateResult(ParsedResult(label="Major", probs={"Critical": 0.1, "Major": 0.8, "Minor": 0.1}))
    assert ok.status == "ok"
    assert cl.validateResult(ParsedResult(label="Intrinsic", probs={})).status == "invalid_result"

def test_a_bad_profile_fails_at_build_time(settings_with_bad_profile):
    with pytest.raises(InexError) as e:
        build_app(settings_with_bad_profile)
    assert e.value.code == "profile_invalid"

def test_system_prompt_lists_the_profile_labels(inex_profile):
    out = render_system_prompt("before\n{{TAXONOMY}}\nafter", inex_profile)
    assert "  - Not a Bug: Not a defect in production code." in out and "{{TAXONOMY}}" not in out
```

New fixtures in `tests/conftest.py`: `inex_profile` (loads `profiles/inex.yaml`), `severity_profile` (loads `profiles/example-severity.yaml`), `settings_with_bad_profile` (the Task 8 `settings` with `profile` pointing at a `tmp_path` YAML that has one label). Update existing tests: the `prompt_version` tests now expect the `inex@` form; classifier fixtures pass `inex_profile`; the Gemini conversion test in `test_classify.py` runs against `build_schema` of both profiles.

- [ ] **Step 2: Run** `./venv/Scripts/python.exe -m pytest tests -k "prompt or profile or validation or system_prompt or bad_profile" -v`. Expected: FAIL.

- [ ] **Step 3: Implement.** Delete the module-level `ClassificationResult` and `_Probabilities`; `Labels`, `LABEL_MAP`, `PROB_FIELDS`, `extractLabel`, `extractProbabilities` and the response schema derive from the profile; the non-structured task block renders steps as `**{title}** - {description}` and the probability and final-answer lines from the labels; the structured task says `The {count_word(n)} probabilities must sum to 1.0.`. `build_app` loads `settings.profile` and sets `App.profile`. `render_system_prompt` replaces `{{TAXONOMY}}` with one `  - {name}: {description}` line per label. In `prompts/system_prompt.md`, replace the label list under "A CLASSIFICATION labels an issue" with `{{TAXONOMY}}` and reword the analysis patterns without naming specific labels ("the classification breakdown", "dependency counts by classification").

- [ ] **Step 4: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0, including both golden tests.

- [ ] **Step 5: Commit** `git add -A inex prompts tests`; `git commit -m "Drive classification and the system prompt from profiles"`.

---

### Task 14: Remove comments and docstrings; architecture document

**Files:**
- Create: `docs/architecture.md`
- Modify: every `.py` file under `inex/`, `tests/`, `evals/`, and `check.py`; `tests/test_architecture.py` (remove the skip)

**Interfaces:**
- Produces: `docs/architecture.md` with the sections: The shape (layers diagram and table), Where does new code go, Standards, Exceptions (the two files, what they may import, any per-file lint ignores from Task 1, and that both end after 2026-11-10), Things that are not obvious, Commands, Decisions still open, Where these rules come from.

- [ ] **Step 1: Enable the check.** Remove the `skip` marker from `test_no_comments_or_docstrings_anywhere`. Run `./venv/Scripts/python.exe -m pytest tests/test_architecture.py::test_no_comments_or_docstrings_anywhere -v`. Expected: FAIL listing every remaining comment and docstring.

- [ ] **Step 2: Write `docs/architecture.md`.** "Things that are not obvious" must cover, at minimum, every rationale in the spec's Documentation section plus any other removed comment whose reason is not visible in the code (review the Step 1 failure list one entry at a time). "Decisions still open": packaging, splitting the two excepted files, the GitHub Action.

- [ ] **Step 3: Remove every comment and docstring** in the listed files. Behaviour must not change.

- [ ] **Step 4: Run** `./venv/Scripts/python.exe check.py`. Expected: exit 0 with no skipped architecture test.

- [ ] **Step 5: Commit** `git add -A docs inex tests evals check.py`; `git commit -m "Remove comments and docstrings; add the architecture document"`.

---

### Task 15: Project docs and final verification

**Files:**
- Create: `CLAUDE.md`, `.env.example`
- Modify: `README.md`, `requirements.txt` (no change needed for PyYAML 6.0.3; confirm it is listed)

- [ ] **Step 1: Write `.env.example`** listing every variable in the spec's Settings table with its default (empty for `GITHUB_TOKEN`), plus `GOOGLE_API_KEY=`, `OPENAI_API_KEY=`, `ANTHROPIC_API_KEY=`, `LANGCHAIN_TRACING=`, `LANGCHAIN_API_KEY=`, `LANGCHAIN_PROJECT=`.

- [ ] **Step 2: Write `CLAUDE.md`**: what the project is; project rules (a failed classification never stores a label and every count filters `status IS NULL OR status = 'ok'`; `db/InExTool.db` is frozen, so point the tool at a copy; run `python check.py` before calling work finished; `docs/architecture.md` is the source of truth and is updated in the same change; no comments or docstrings); commands; a pointer to `docs/architecture.md`.

- [ ] **Step 3: Rewrite `README.md`** around `python -m inex`: install (`requirements.txt`, `requirements-dev.txt`), configure (`.env` from `.env.example`, model options formerly commented in `config.py`), the command table, chat as one front end, profiles (how to add one), and the note that existing classification rows read as unclassified under profile-based prompt versions.

- [ ] **Step 4: Verify the whole branch.**
  - `./venv/Scripts/python.exe check.py` Expected: exit 0.
  - `./venv/Scripts/python.exe -m inex --help` Expected: the ten commands listed.
  - `cp db/InExTool.db "$TMP/InExTool.copy.db"` then `./venv/Scripts/python.exe -m inex --db "$TMP/InExTool.copy.db" query "SELECT model, COUNT(*) FROM classifications GROUP BY model"` Expected: `qwen3:30b` and `71640`; `git status --short db/` Expected: no change to `db/InExTool.db`.
  - Record for the user: the routing evaluation (`./venv/Scripts/python.exe -m evals.routing`) needs LangSmith and an orchestrator key and was not run unless those are configured.

- [ ] **Step 5: Commit** `git add CLAUDE.md .env.example README.md`; `git commit -m "Document the inex package and its rules"`.
