# Core Seams: Layers, Service Layer, CLI, Store, Profiles

**Status:** proposed. **Date:** 2026-10-26. **Lands before:** the MSR 2027 Data and Tool
Showcase submission (2026-11-10).

## Context

The tool works, but its structure supports "one study's pipeline behind a chat box"
better than "reusable infrastructure", and the paper will describe whatever
architecture exists at submission. Today:

1. **The chat agent is the only way in.** Collect, classify, link and export are
   reachable only through LangChain tool functions, two of which contain their own SQL
   and business logic. `eval.py` rebuilds the agent separately because importing
   `agent.py` starts the chat loop.
2. **Objects are created at import time.** Importing `tools/langchain_tools.py` opens the
   database, builds the classifier and opens an unused read-only connection;
   `tools/helpers/sqlQuery.py` opens another. The database path is read once at import,
   so one process cannot work with two databases. `import_list.py` and the test
   fixtures exist partly to work around this.
3. **SQL is scattered.** 18 call sites outside `tools/helpers/database.py` reach into its
   cursor: 6 in `langchain_tools.py`, 4 in `collect.py`, 4 in `classify.py`, 2 in
   `seeder.py`, 1 in `dependencies.py`, 1 in `eval.py`.
4. **Two classes do too much.** `Classify` (665 lines) holds prompt loading and hashing,
   two model backends, retries, parsing, validation, prompt rendering, threading and
   persistence. `Collect` (538 lines) mixes the HTTP client, paging, fix attribution,
   metrics and the save loop.
5. **The taxonomy is hard-wired** (covered by the classification profiles spec).
6. **Tool results have no consistent shape.** Of the nine tools the agent is given,
   three return prose, five return dicts, and `run_sql` returns a string on success and
   a dict on failure.
7. **It is not a package.** No `pyproject.toml`, five scripts in the repo root, and a
   `helpers/` versus `core/` split with no meaning.

Two scripts are also broken: `seeder.py` calls `save_classification` with 2 of its 6
arguments, and `wipe.py` rebuilds a second copy of the schema that lacks the five
classification status columns.

The standards applied here are the user's, adapted from the Cardline project's
`docs/architecture.md` to Python, plus one rule of their own: no docstrings or comments.

## Goals

1. Every pipeline step is reachable from a CLI and from the agent through the same
   service call.
2. No SQL outside `inex/store/`.
3. Importing any `inex` module creates no files and opens no connections.
4. Only `inex/config.py` reads the environment.
5. Layer rules are enforced by a test, with exceptions limited to two named files.
6. Classification profiles land as specified in
   `2026-10-26-classification-profiles-design.md`, adjusted below, and the InEx golden
   test passes.
7. No comments or docstrings in any Python file, enforced by a test.
8. `python check.py` passes: `ruff format --check`, `ruff check`, `pytest`.
9. Existing databases open without any row changing.

## Non-goals

- **Splitting the internals of `services/collection.py` and `services/classification.py`**
  (moving their GitHub and model calls into `clients/`). Deferred to after 2026-11-10.
- **Packaging** (`pyproject.toml`, `pip install`, an `inex` console script). Deferred.
- Aligning or removing `.github/workflows/issue-classify.yml`.
- Supporting anything other than npm and SQLite.
- Changing classification behaviour under the InEx profile. The golden test guards it.

## Standards

These move into `docs/architecture.md`, which becomes the source of truth.

- **Layers** import only downward (table below). Enforced by a test.
- **Configuration.** `inex/config.py` is the only reader of environment variables and
  the only caller of `load_dotenv`.
- **No work at import time.** Nothing opens a file, a connection or a client when a
  module is imported. Entry points build everything through `build_app`.
- **Errors.** Services raise `InexError` with a message written for the reader. Only the
  CLI and the agent tool wrapper turn an error into output. Nothing catches an error just
  to log it. Unexpected errors are logged in full and the user sees a generic message.
- **Validation.** Input is checked once, at the front end, before any work starts.
- **Naming.** PEP 8: snake_case functions, methods and variables; PascalCase classes. All
  new and moved code follows it now, except the internals of the two excepted files,
  which follow it when they are split. Their public service methods are renamed now.
- **No unexplained numbers.** Truncation budgets and time windows become named
  module-level constants.
- **No docstrings or comments.** Rationale that is not obvious from the code goes in
  `docs/architecture.md` under "Things that are not obvious". Lint suppressions
  (`# noqa`) are the only permitted comments.
- **Tests** never call a real outside service, and each test sets up its own data.
- **Formatting and lint.** Ruff decides formatting and catches mistakes. Neither is up
  for debate in review.
- **Dependencies.** Add one only when the alternative is writing and maintaining a
  meaningful amount of code.
- **Docs.** A change that alters a rule updates `docs/architecture.md` in the same commit.

## Layout

```
inex/
  __main__.py              python -m inex <command>
  cli.py                   front end: commands
  agent/
    tools.py               front end: LangChain tools, built per app
    chat.py                front end: orchestrator construction and the chat loop
  validators.py            input checks shared by both front ends
  app.py                   App and build_app(settings)
  config.py                Settings and load_settings
  errors.py                InexError
  services/
    collection.py          count, collect                     (excepted: calls GitHub)
    classification.py      preview, classify, classify_ids    (excepted: calls models)
    imports.py             preview_list, import_list, load_jsonl
    linkage.py             issue to npm release linkage
    queries.py             read-only SQL and exports
    maintenance.py         init, reset, clear
    profiles.py            profile loading, validation, schema building
  store/
    schema.py              the single schema definition and upgrades
    database.py            Store: every SQL statement
  clients/
    npm.py                 npm registry fetch and cache
profiles/
  inex.yaml
  inex_guide.md            moved from prompts/classification_prompt.md
  example-severity.yaml
  example-severity_guide.md
prompts/
  system_prompt.md
evals/
  routing.py               moved from eval.py
docs/architecture.md
CLAUDE.md
check.py
ruff.toml
requirements-dev.txt
.env.example
```

**Removed:** `tools/` (all of it), root `config.py`, `agent.py`, `eval.py`, `seeder.py`,
`wipe.py`, `flush.py`, `import_list.py`. Files move with `git mv` where a file continues
under a new name, so history follows them.

| Old | New |
|---|---|
| `tools/helpers/collect.py` | `inex/services/collection.py` |
| `tools/helpers/classify.py` | `inex/services/classification.py` |
| `tools/helpers/database.py` | `inex/store/database.py` and `inex/store/schema.py` |
| `tools/helpers/sqlQuery.py` | `inex/services/queries.py` (SQL to the store) |
| `tools/helpers/issueList.py` | `inex/services/imports.py` |
| `tools/core/dependencies.py` | `inex/services/linkage.py` |
| `tools/core/npm_registry.py` | `inex/clients/npm.py` |
| `tools/langchain_tools.py` | `inex/agent/tools.py` |
| `agent.py` | `inex/agent/chat.py` |
| `eval.py` | `evals/routing.py` |
| `seeder.py` | `ImportService.load_jsonl` |
| `wipe.py`, `flush.py` | `MaintenanceService.reset`, `.clear` |
| `import_list.py` | `python -m inex --db PATH import LIST.csv` |
| `config.py` | `inex/config.py` |

## Layer rules

| Layer | May import (first party) | May not import |
|---|---|---|
| `cli` | `app`, `validators`, `errors`, `config`, `agent/chat` | `store`, `clients`, `services` |
| `agent/` | `app`, `validators`, `errors`, `config` | `cli`, `store`, `clients`, `services` |
| `app` | `config`, `services`, `store`, `clients`, `errors` | `cli`, `agent` |
| `validators` | `errors` | everything else |
| `services/` | `store`, `clients`, `errors`, other `services` | `cli`, `agent`, `app` |
| `store/` | `errors` | everything else |
| `clients/` | `errors` | everything else |
| `config`, `errors` | nothing first party | everything |

Third-party and standard library rules, across all of `inex/`:

- `sqlite3`: only `store/`.
- `requests`: only `clients/`.
- `langchain`, `langchain_*`, `langgraph`: only `agent/`.
- `os.environ`, `os.getenv`, `dotenv`: only `config.py`.

**Exception list** (data at the top of `tests/test_architecture.py`, removed when the
files are split after 2026-11-10):

| File | May also import |
|---|---|
| `inex/services/collection.py` | `requests` |
| `inex/services/classification.py` | `requests`, `langchain`, `langchain_*` |

The exception covers imports only. Both files lose all SQL now.

## Settings

```python
@dataclass(frozen=True)
class Settings:
    database: Path
    github_token: str | None
    orchestrator: str
    classifier: str
    temperature: float
    classify_workers: int
    classify_retries: int
    profile: Path
    system_prompt: Path
    exports_dir: Path
    npm_cache_dir: Path
    checkpoints: Path
    logging_config: Path
    ollama_base: str
    num_ctx: int


def load_settings(env_file: Path | None = Path(".env"), **overrides) -> Settings
```

| Field | Environment variable | Default |
|---|---|---|
| `database` | `DATABASE_URL` | `db/database.db` |
| `github_token` | `GITHUB_TOKEN` | none |
| `orchestrator` | `INEX_ORCHESTRATOR` | `google_genai:gemini-2.5-flash` |
| `classifier` | `INEX_CLASSIFIER` | `google_genai:gemini-2.5-pro` |
| `temperature` | `INEX_TEMPERATURE` | `0.2` |
| `classify_workers` | `INEX_WORKERS` | `8` |
| `classify_retries` | `INEX_RETRIES` | `5` |
| `profile` | `INEX_PROFILE` | `profiles/inex.yaml` |
| `system_prompt` | none | `prompts/system_prompt.md` |
| `exports_dir` | none | `exports` |
| `npm_cache_dir` | none | `.npm_doc_cache` |
| `checkpoints` | none | `db/checkpoints.db` |
| `logging_config` | none | `loggingConfigs/config.json` |
| `ollama_base` | `OLLAMA_BASE` | `http://localhost:11434` |
| `num_ctx` | `INEX_NUM_CTX` | `131072` |

Keyword overrides win over the environment; the CLI passes its global flags as
overrides. Provider API keys (`GOOGLE_API_KEY` and others) stay in `.env`; `load_dotenv`
puts them in the process environment where the provider SDKs read them. `.env.example`
lists every variable. Choosing models moves from editing `config.py` to `.env` or flags,
and the model options currently commented out in `config.py` move to the README.

## Composition root

```python
@dataclass
class App:
    settings: Settings
    collection: CollectionService
    classification: ClassificationService
    imports: ImportService
    linkage: LinkageService
    queries: QueryService
    maintenance: MaintenanceService

    def close(self) -> None


def build_app(settings: Settings) -> App
```

`build_app` opens one `Store`, builds the npm client, loads and validates the profile,
and constructs each service with what it needs. The model client is still built on
first use. The CLI, the chat agent, `evals/routing.py` and the tests all start here;
tests and the eval construct `App` with fake services instead of patching class methods.

## Services

Services take plain arguments (owner and repo as separate strings, dates as ISO 8601
strings, `order` as `"asc"` or `"desc"`) and return frozen dataclasses defined in the
same module. They never return prose or error dicts.

**`CollectionService(store, github_token)`** (excepted file)
- `count(owner, repo) -> int`
- `collect(owner, repo, *, count=None, start=None, end=None, order="desc") -> CollectResult(collected, skipped)`
- Raises `github_token_missing` when no token is set and `repo_not_found` when GitHub
  has no such repository.

**`ClassificationService(store, profile, classifier, temperature, workers, retries, ollama_base)`** (excepted file)
- `preview(owner, repo, *, count=None, start=None, end=None, order="desc") -> ClassifyPreview(would_classify, total_remaining, model, prompt_version, earliest_created, latest_created, sample)`
- `classify(owner, repo, *, count=None, start=None, end=None, order="desc") -> ClassifyResult(classified, failed)`
  where `failed` maps status to count.
- `classify_ids(owner, repo, issue_ids) -> ClassifyResult`
- Raises `project_not_found`. A failed classification is still recorded with its
  status and counted in `failed`, never raised.

**`ImportService(store, collection, classification)`**
- `preview_list(csv_path, column=None) -> ListPreview(total_issues, distinct_repos, already_in_db, to_collect, repos, unparseable_count, unparseable_sample)`
- `import_list(csv_path, column=None, classify=True) -> ImportResult(collected, skipped, failed, classified, repos)`
- `load_jsonl(path) -> LoadResult(files, issues, classifications)`, where `path` is a
  file or a directory searched recursively for `*.jsonl`.
- Raises `file_not_found`, `no_valid_entries`.

**`LinkageService(store, npm)`**
- `link(owner, repo, package, *, start=None, end=None, version_range=None, last_n_versions=None, version_start=None, version_end=None) -> LinkResult(package_name, issue_versions_snapshotted, slice_versions_snapshotted, linked_issues, already_linked, unlinkable_pre_release)`
- Raises `project_not_found`, `no_classifications`, `npm_package_not_found`,
  `npm_doc_fetch_failed`, `no_versions_match_range`, `no_classified_issues`.

**`QueryService(store, exports_dir)`**
- `run(sql) -> QueryResult(columns, rows, truncated)`, capped at `MAX_QUERY_ROWS = 50`,
  exposed as `QueryService.max_rows` so the `run_sql` tool description is built from it.
- `export(sql, fmt="csv", filename=None) -> ExportResult(path, rows_exported, columns, format)`
- Raises `not_a_select`, `multiple_statements`, `invalid_format`, `invalid_filename`,
  `sql_error`.

**`MaintenanceService(store)`**
- `init()`, `reset()`, `clear()`.

**`profiles.py`**: `load_profile(path) -> Profile` and `build_schema(profile)`, as in the
profiles spec. Raises `profile_invalid` naming the problem.

## Errors

```python
class InexError(Exception):
    def __init__(self, code: str, message: str, **details)
```

`code` is a stable snake_case identifier; `message` is for the reader; `details` carries
structured context (owner, repo, path). Only two places convert errors to output:
`cli.main` and the agent tool wrapper. Anything that is not an `InexError` is logged with
its traceback and reported as `internal_error` with a generic message.

## Validators

`inex/validators.py`, used by both front ends, each raising
`InexError("invalid_input", ...)`:

- `parse_repo(text) -> (owner, repo)`: exactly one `/`, both parts non-empty.
- `parse_start(text) -> str` and `parse_end(text) -> str`: accept `YYYY-MM-DD` or full
  ISO 8601. A bare end date expands to `T23:59:59Z`.
- `parse_order(text) -> str`: `asc` or `desc`.
- `parse_count(value) -> int`: a positive integer.
- `parse_filename(text) -> str`: a plain file name with no directory parts.
- `parse_format(text) -> str`: `csv` or `json`.

## Store

**`inex/store/schema.py`** holds `TABLES` (the full current definitions, including the
five classification status columns and `UNIQUE(issue_id, model, prompt_version,
temperature)`), `ADDED_COLUMNS` (columns added since older databases were created),
`INDEXES`, and `ensure_schema(connection)`.

`ensure_schema` creates missing tables, adds missing columns, and creates
`ux_classifications_run`, a unique index on `classifications(issue_id, model,
prompt_version, temperature)`. Older databases, including `db/InExTool.db`, have no
unique constraint there, so today's upsert fails on them. Adding the index is safe for
that file: no issue in it has more than one classification row. No row is ever changed.
Because this still modifies the file, the archived copy of `InExTool.db` stays untouched
and the tool is pointed at a working copy.

**`inex/store/database.py`**: `Store(path)` opens a read-write connection with
`row_factory = sqlite3.Row`, runs `ensure_schema`, and opens a separate read-only
connection for `run_readonly`. There is no public cursor. Each method uses its own
cursor; writes hold the store's lock.

The 18 raw call sites become these methods:

| Call site | Method |
|---|---|
| `classify.py` project lookups (2) | `project_id(owner, repo)` |
| `dependencies.py` project lookup | `project_id(owner, repo)` |
| `collect.py` existing-issue checks (2) | `issue_id(project_id, number)` |
| `langchain_tools.py` list preview | `project_id`, `count_existing_issues(project_id, numbers)` |
| `langchain_tools.py` import count | `count_ok_classifications(project_id, numbers, model, prompt_version, temperature)` |
| `seeder.py` classification check | `has_classification(issue_id)` |
| `sqlQuery.py` read-only connection | `run_readonly(sql, limit)` and `fetch_readonly(sql)` |
| `wipe.py`, `flush.py` | `reset()`, `clear()` |
| `eval.py` connection close | `close()` |

Existing methods keep their behaviour under PEP 8 names: `save_project`, `save_issue`,
`save_classification`, `set_package_name`, `package_name`, `project_has_classifications`,
`classified_issue_window`, `version_id`, `save_version`, `save_dependencies`,
`link_issue_to_version`, `unclassified_by_ids`, `count_unclassified`,
`unclassified_in_window`. The rule that failed classification rows never count as
results stays inside the store, in one place.

## Front ends

### CLI

`inex/cli.py` uses `argparse`.

```
python -m inex [--db PATH] [--classifier MODEL] [--profile PATH] [--json] COMMAND

count      OWNER/REPO
collect    OWNER/REPO [--count N] [--since DATE] [--until DATE] [--oldest-first]
classify   OWNER/REPO [--count N] [--since DATE] [--until DATE] [--oldest-first] [--preview]
link       OWNER/REPO --package NAME [--since DATE] [--until DATE]
           [--version-range 3.x] [--last-versions N] [--versions-from DATE] [--versions-to DATE]
import     LIST.csv [--column NAME] [--collect-only] [--preview]
load       FILE_OR_DIR
query      "SELECT ..."
export     "SELECT ..." [--format csv|json] [--name NAME]
chat
db         init | reset --yes | clear --yes
```

- Each command validates its input, makes one service call and prints the result: a
  short summary by default, the result dataclass as JSON with `--json`. With `--json`, an
  `InexError` prints `{"status": "error", "code": ..., "message": ...}` to stdout.
- No interactive prompts. `--preview` shows what would happen. `db reset` and `db clear`
  refuse to run without `--yes`.
- Exit codes: `0` success, `1` an `InexError` (message on stderr) or an unexpected error
  (traceback to the log, generic message on stderr), `2` usage errors.
- Logging is configured inside `main()` from `settings.logging_config`.

### Agent

`inex/agent/tools.py` exposes `build_tools(app) -> list`. The nine tools keep their names
so the system prompt and the routing evaluation stay valid. `describe_schema` is
removed. Arguments are normalised to what `prompts/system_prompt.md` already documents:

| Tool | Arguments |
|---|---|
| `count_issues` | `repo_name` |
| `collect_all` | `repo_name`, `count`, `start_date`, `end_date`, `direction` |
| `classify_all` | `repo_name`, `count`, `start_date`, `end_date`, `direction` |
| `preview_classification` | `repo_name`, `count`, `start_date`, `end_date`, `direction` |
| `snapshot_dependencies` | `repo_name`, `npm_package`, `start_date`, `end_date`, `version_range`, `last_n_versions`, `version_start`, `version_end` |
| `run_sql` | `query` |
| `export_data` | `query`, `format`, `filename` |
| `preview_issue_list` | `csv_path`, `column` |
| `import_issue_list` | `csv_path`, `column`, `classify` |

Every tool returns `{"status": "ok", **asdict(result)}` or
`{"status": "error", "code": ..., "message": ...}`. A single wrapper performs that
conversion for all nine.

`inex/agent/chat.py` exposes `build_agent(app, checkpointer)` and `run_chat(app)`.
`build_agent` builds the orchestrator from `settings.orchestrator`, renders the system
prompt with `{{TAXONOMY}}` filled from the profile, and attaches the tools.
`run_chat` opens the conversation store with
`SqliteSaver.from_conn_string(str(settings.checkpoints))` and runs the loop.
`evals/routing.py` passes an `App` holding fake services and
`SqliteSaver.from_conn_string(":memory:")`.

The confirmation gates (count before an unbounded collect, preview before classify,
preview before an import) stay in the system prompt.

## Profiles

`2026-10-26-classification-profiles-design.md` applies as written, with these changes:

- The loader and schema builder live in `inex/services/profiles.py`, not
  `tools/core/profile.py`.
- The InEx guide moves to `profiles/inex_guide.md` so a profile and its guide sit
  together. This overrides that spec's "the guide file does not move".
- `config.classifyProfile` becomes `Settings.profile` (`INEX_PROFILE`, `--profile`).
- `build_app` loads the profile, so a bad profile raises `profile_invalid` before any
  work starts.
- `inex/agent/chat.py` fills `{{TAXONOMY}}` in the system prompt.
- That spec's statement that the repository has no tests is out of date; its tests add
  to the existing suite.
- PyYAML becomes a direct dependency in `requirements.txt`.

## Enforcement

**`tests/test_architecture.py`**, standard library only:

1. **Layers.** Parse every module under `inex/` with `ast`, resolve each import to a
   first-party layer or a third-party top-level name, and fail on anything the tables
   above forbid, minus the exception list.
2. **Environment.** Fail on `os.environ`, `os.getenv` or any `dotenv` import outside
   `inex/config.py`.
3. **No comments or docstrings.** Tokenize every `.py` file under `inex/`, `tests/`,
   `evals/` and `check.py`; fail on any comment token other than `# noqa` forms, and on
   any module, class or function docstring.
4. **Imports do nothing.** In a subprocess whose working directory is an empty temporary
   folder with no `.env`, import every `inex` module; fail if the folder is no longer
   empty.

**Ruff** in `requirements-dev.txt`, configured by `ruff.toml`: line length 100, rule sets
`E`, `F`, `I`, `B`, `UP`, `N`, with `N` ignored for the two excepted files only. The whole
codebase is formatted once in a commit of its own. Lint findings are fixed in step 1; where
a fix would change behaviour in an excepted file, a per-file ignore is added instead and
listed in `docs/architecture.md` next to the exception list.

**`check.py`** runs `ruff format --check .`, `ruff check .` and `pytest -q`, and exits
non-zero if any fails.

## Documentation

- **`docs/architecture.md`**: the layers and a "where does new code go" table, the
  standards, the exception list and when it ends, commands, decisions still open
  (packaging, the split of the two excepted files, the GitHub Action), and "Things that
  are not obvious", which absorbs the rationale currently held in comments, including:
  - the classifier's response fields are ordered reasoning first because Gemini emits
    properties in schema order;
  - writes are serialised with a lock rather than WAL because the read-only connection
    cannot create WAL's shared-memory file;
  - a NULL classification `status` marks a legacy row and counts as done;
  - the run filters in the unclassified query must stay in the `LEFT JOIN`'s `ON` clause;
  - issue `raw_data` is spread before the explicit columns so it cannot override them;
  - GitHub search returns 422 for some public repositories, so counts use GraphQL;
  - linkage prefers the latest stable release over prereleases and compares parsed
    timestamps, not strings;
  - `prompt_version` hashes the profile, the guide and the template version together.
- **`CLAUDE.md`**: what the project is, project rules (the classification status
  invariant, the frozen corpus, run `python check.py` before finishing, update
  `docs/architecture.md` in the same change, no comments or docstrings), commands, and a
  pointer to the architecture document.
- **`README.md`**: rewritten around `python -m inex`, with chat as one front end, the
  `.env` settings, the model options moved from `config.py`, and the note that existing
  classification rows read as unclassified under profile-based prompt versions.
- **`.env.example`**: every variable from the Settings table.

## Testing

- The existing 91 tests move to the new imports and names with their assertions
  unchanged.
- New tests:
  - the four architecture checks;
  - CLI: `main(argv)` against a temporary database and fake services, asserting output,
    `--json` output and exit codes, including `db reset` refusing without `--yes`;
  - agent tools: each returns a status dict, and an `InexError` becomes an error dict;
  - store: each new method, and opening a database with the pre-status schema, which
    gains the columns and the unique index with every row unchanged;
  - validators;
  - the profile tests from the profiles spec, including the InEx golden schema test.
- No test calls GitHub, npm or a model.

## Order of work

Each step ends with `pytest` green and is its own commit.

1. Ruff, `ruff.toml`, `requirements-dev.txt`, `check.py`, and the one-time formatting
   commit.
2. Store: `schema.py`, `Store` with every SQL statement, callers switched over, upgrade
   test.
3. `config.py`, `errors.py`, `validators.py`, services with result types, `build_app`;
   all import-time objects removed.
4. Front ends: CLI, agent tools and chat, `evals/routing.py`; root scripts and `tools/`
   removed.
5. `tests/test_architecture.py` with the exception list (the comment check is skipped
   until step 7).
6. Profiles.
7. Remove comments and docstrings, moving rationale into `docs/architecture.md`; enable
   the comment check.
8. `docs/architecture.md`, `CLAUDE.md`, `README.md`, `.env.example`.

## Risks

- **Agent behaviour.** Tool results become dicts and argument names change. The routing
  evaluation must be re-run after step 4; it needs LangSmith and an orchestrator key.
- **Dynamic schemas and Gemini.** Carried over from the profiles spec: generated schemas
  must be re-verified against the Gemini converter.
- **Schema upgrade.** Opening an older database adds columns and an index. Point the tool
  at a copy of the frozen corpus.
- **The excepted files** still mix HTTP or model calls with rules until they are split.
- **Comment removal** can lose rationale that is not moved into the docs. Step 7 reviews
  every removed comment against the "Things that are not obvious" section.

## Refinements made while planning

These pin details the sections above left open. Where they differ, this section wins.

1. `CollectionService.count` returns `CountResult(owner, repo, issue_count)` and the
   `MaintenanceService` methods return `DatabaseResult(path)`, so every service call has a
   dataclass result and both front ends convert results the same way.
2. `App` also carries `profile: Profile`; the chat front end reads label names and
   descriptions from it without importing `services`.
3. `config.py` may import `errors`: a malformed numeric setting raises
   `InexError("invalid_setting", ...)`.
4. Settings defaults for bundled files (`profile`, `system_prompt`, `logging_config`)
   resolve from the repository root, so the CLI works from any working directory. Data
   paths (`database`, `exports_dir`, `npm_cache_dir`, `checkpoints`) stay relative to the
   working directory.
5. `ImportService.load_jsonl(path, model, prompt_version, temperature)`: classification
   rows need the run key, so the CLI takes `load PATH --model M --prompt-version P
   [--temperature T]`. `classified_at` comes from the line when present.
6. Until profiles land, `ClassificationService` takes `guide_path` where it later takes
   `profile`.
7. Relative imports are banned (ruff `TID252`) so the layer test resolves every import.
8. `pytest` runs with `--disable-socket` (pytest-socket is already installed), which
   enforces that tests never reach an outside service.
9. The CLI exits `130` on Ctrl+C, after rows already saved stay saved.
10. Profiles: generated models keep the fixed names `ClassificationResult` and
    `_Probabilities`, and the schema carries no field descriptions, so the InEx schema is
    byte-identical to today's once the `ClassificationResult` docstring is removed. That
    docstring is developer rationale that Pydantic currently sends to Gemini as the
    schema's `description`; dropping it is the one intended change to what Gemini
    receives, and its content moves to `docs/architecture.md`. Each reasoning step gains a `title` (for example
    `PR/Commit Evidence`) used by the non-structured prompt template. The golden tests
    cover the schema and both rendered prompts, captured as fixtures from today's code
    before the switch.

## After 2026-11-10

- Split `services/collection.py`: GitHub REST and GraphQL into `clients/github.py`;
  remove its exception.
- Split `services/classification.py`: model backends into `clients/`, and prompt
  rendering, result validation and the batch runner into separate modules; adopt PEP 8
  names; remove its exception.
- Packaging: `pyproject.toml` and an `inex` console script.
- Align or remove the GitHub Action.
