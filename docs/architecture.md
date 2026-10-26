# Architecture and standards

How InEx is put together, and the rules that keep it that way. Most rules are checked by
`python check.py`, so breaking one fails the check instead of relying on someone
remembering it. This document is the source of truth: a change that alters a rule updates
this file in the same commit.

## The shape

InEx is one Python package, `inex/`, with two front ends over the same services: a
command line (`python -m inex`) and a chat agent (`python -m inex chat`). A request travels
down this list and the answer travels back up. A layer may only import from layers below it.

```
cli.py, agent/   Front ends. Check input, call one service, format the reply.
app.py           build_app(settings): builds the store, the clients and every service.
services/        Every rule and decision. No SQL, no HTTP, no LangChain.
store/           SQLite. The only code that runs SQL.
clients/         Outside APIs (the npm registry). Return the API's data lightly tidied.
```

Around those:

- `config.py` is the only file that reads environment variables. `load_settings()` reads
  `.env` when an entry point asks; nothing happens at import.
- `errors.py` defines `InexError(code, message, **details)`, the one error type services
  raise. Only `cli.py` and the wrapper in `agent/tools.py` turn an error into output.
- `validators.py` checks user input once, at the front end, for both front ends.
- `profiles/` holds classification profiles: a YAML file naming the labels and reasoning
  steps, and the guide it points at. `prompts/system_prompt.md` is the agent's prompt.

### Import rules

| Layer | May import (first party) | May not import |
|---|---|---|
| `cli` | `app`, `validators`, `errors`, `config`, `agent/chat` | `store`, `clients`, `services` |
| `agent/` | `app`, `validators`, `errors`, `config` | `cli`, `store`, `clients`, `services` |
| `app` | `config`, `services`, `store`, `clients`, `errors` | `cli`, `agent` |
| `validators` | `errors` | everything else |
| `services/` | `store`, `clients`, `errors`, other `services` | `cli`, `agent`, `app` |
| `store/` | `errors` | everything else |
| `clients/` | `errors` | everything else |
| `config`, `errors` | `errors` only | everything else |

Across all of `inex/`: `sqlite3` only in `store/`, `requests` only in `clients/`,
`langchain*` and `langgraph` only in `agent/`, `dotenv` and `os.environ`/`os.getenv` only in
`config.py`. Relative imports are not allowed.

### Where does new code go?

| You are adding... | Put it in... |
|---|---|
| A command | `cli.py`, calling a service method |
| An agent tool | `agent/tools.py`, calling a service method |
| A rule, a calculation, a decision | `services/` |
| A check on what the user typed | `validators.py` |
| A SQL query | `store/database.py` |
| A table or column | `store/schema.py` (`TABLES`, and `ADDED_COLUMNS` for existing databases) |
| A call to another website's API | `clients/` |
| A setting or secret | `config.py` and `.env.example` |
| A taxonomy | `profiles/`: a YAML profile and its guide |

## Exceptions until after 2026-11-10

Two files still make their own outside calls. They are listed in `EXCEPTIONS` in
`tests/test_architecture.py`, and that list is how you know the split is done.

| File | May also import | Ends when |
|---|---|---|
| `services/collection.py` | `requests` | GitHub REST and GraphQL move to `clients/github.py` |
| `services/classification.py` | `requests`, `langchain*` | Model backends move to `clients/`; prompt rendering, validation and the batch runner become separate modules |

Ruff's naming rules (`N`) are switched off for those two files, whose internal methods are
still camelCase, and `N802` is off for `tests/test_concurrency.py`, whose test subclasses
override those methods. All three entries in `ruff.toml` go when the files are split.

## Standards

**Errors.** Raise `InexError` with a stable snake_case `code` and a message written for the
person reading it. Never catch an error just to log it. Unexpected errors are logged in full
and the user sees a generic message. The one deliberate exception: a failed classification
is recorded with its status, not raised, so one bad issue cannot stop a batch.

**Validation.** Input is checked once, in `validators.py`, before any work starts.

**Naming.** PEP 8: snake_case functions, methods and variables; PascalCase classes. The
internals of the two excepted files follow it when they are split.

**No unexplained numbers.** Name them as constants at the top of the module.

**No comments or docstrings** in any Python file, tests included. Reasoning that is not
visible in the code goes in "Things that are not obvious" below. `# noqa` is the only
permitted comment.

**No work at import time.** Importing a module never opens a file, a connection or a
client. Entry points build everything through `build_app`.

**Tests.** `pytest` runs with `--disable-socket`, so a test that reaches the network fails.
Each test sets up its own data. Tests sit in `tests/`, shared fakes in `tests/fakes.py`.

**Formatting and lint.** Ruff decides formatting and catches mistakes. Neither is up for
debate in review.

**Dependencies.** Add one only when the alternative is writing and maintaining a meaningful
amount of code.

## Things that are not obvious

### Classification

- **Field order in the response schema matters.** Gemini emits properties in schema order,
  so the reasoning fields come before `probabilities` and `final_answer`; otherwise the
  model commits to an answer before it reasons. `build_schema` keeps the model names
  `ClassificationResult` and `_Probabilities` and adds no field descriptions, so the InEx
  schema is byte-identical to the hand-written one it replaced
  (`tests/fixtures/inex_schema.json`).
- **What `prompt_version` identifies.** It is
  `<profile>@<sha256(profile text + guide text)[:12]>+t<TEMPLATE_VERSION>`, with line
  endings normalised to LF first so a checkout that converts them (`core.autocrlf`) does
  not change the run key; `.gitattributes` also keeps `profiles/` at LF. The prompt
  actually sent is the guide plus the issue template in `formatIssueData`, so bump
  `TEMPLATE_VERSION` whenever that template changes; hashing only the files would let the
  template drift silently. Rows classified before profiles existed carry a path-based
  version and read as unclassified under the new scheme.
- **A failed attempt is never a label.** `AttemptFailure` carries the status to record if
  retries run out, so a failure is never collapsed into a taxonomy label such as
  `Unknown`. Failed rows are stored with a NULL classification and a non-ok `status`, and
  are retried on the next run because the resume query only counts `status = 'ok'` (or a
  NULL legacy status) as done. Before this rule, 1,063 rows of the 71,640-issue corpus were
  parse failures stored as `Unknown`.
- **Which errors are retried.** Only transient faults: timeouts, connection errors, 429 and
  5xx. Auth, schema and context-overflow errors fail fast. With langchain-google-genai
  4.2.3 a 429 arrives as `ChatGoogleGenerativeAIError` whose `__cause__` is a
  `google.genai.errors.ClientError`; `google.api_core.exceptions.ResourceExhausted` is never
  raised. `google.genai` `ServerError` propagates uncaught by LangChain. Context overflow is
  a `ClientError` subclass and is never worth retrying.
- **One retry controller per call.** A tenacity `Retrying` object carries per-run state, so
  worker threads must not share one.
- **Attempt counts survive hard failures.** When an exception escapes `callModel`, the
  attempt count rides along on the exception so the `attempts` column reports the retries
  that actually happened.
- **The Gemini client is built once.** Double-checked locking guards construction, and the
  batch runner builds it before the thread pool starts so workers do not race to build it.
  `max_retries=3` is explicit because 0 means "use the SDK default of 5"; the timeout is
  explicit because httpx otherwise has none.
- **Structured output settings.** `json_schema` is the default and only native mode in
  langchain-google-genai 4.2.3 (`json_mode` is a deprecated alias, `function_calling` is
  discouraged). `include_raw=True` keeps the raw response for auditing and turns a parse
  failure into a value instead of an exception. If `include_thoughts` is ever enabled the
  content becomes a list of blocks; it is JSON-encoded so the raw-response column stays a
  string.
- **Validation of a result.** Every label in the profile must have a probability: a dict
  was once accepted if even one label matched, which let 379 single-key results through.
  Each value must lie in [0, 1]. A sum off by more than 0.02 is flagged (`prob_sum_off`),
  not failed. When the highest probability disagrees with the stated label, the
  disagreement is recorded in `argmax_label` and never overrides what the model said.
- **Prompt construction.** Issue fields use `or` defaults because the keys often exist with
  value None. An absent closing PR or commit is stated as "None found", because the guide
  leans on PR evidence and an empty string cannot be told apart from missing data. Stored
  `raw_data` is spread before the explicit columns so it cannot override title, body or
  state.
- **Which models are cloud models.** `provider:model` with a provider in `openai`,
  `anthropic`, `google_genai` or `google_vertexai` goes through LangChain with structured
  output. Anything else, including tags with a colon such as `qwen3:30b`, is an Ollama
  model.
- **Ctrl+C stops paid work.** On an interrupt the batch runner cancels every issue that has
  not started; at most one in-flight issue per worker finishes and is saved. A plain
  `with ThreadPoolExecutor` would wait for the whole queue, sending every remaining issue
  to the model.
- **One bad issue cannot stop a batch.** Each issue is classified and saved inside a guard
  that catches every `Exception` (never `KeyboardInterrupt`) and records `api_error`. If the
  save itself fails, the issue stays unclassified and the next run picks it up, which is
  the same contract as a failure.
- **Profile YAML and booleans.** Unquoted `Yes`, `No`, `On` and `Off` are booleans in YAML.
  The loader rejects label names and reasoning fields that are not text instead of turning
  them into `True` and `False`.

### Storage

- **Every count filters failures.** Failed attempts live in `classifications` too, so every
  aggregate filters `status IS NULL OR status = 'ok'`. NULL marks a legacy row from before
  the column existed and counts as done.
- **The unclassified query keeps its run filters in the `ON` clause.** Moving the model,
  prompt-version and temperature predicates to `WHERE` turns the `LEFT JOIN` into an inner
  join and matches nothing.
- **Writes take a lock instead of using WAL.** The read-only connection cannot create the
  shared-memory file WAL needs. A write takes microseconds against a minute-long model
  call, so the lock costs nothing in practice.
- **Upsert, not `INSERT OR REPLACE`.** Replace deletes and reinserts, which changes
  `classifications.id` on every re-save.
- **Older databases get a unique index.** `db/InExTool.db` predates the unique key on
  `classifications(issue_id, model, prompt_version, temperature)`, which the upsert needs.
  Opening any database adds missing columns and `ux_classifications_run`; rows never
  change. Keep the archived corpus byte-for-byte and point the tool at a copy.
- **The upgrade is all or nothing.** A legacy database holding the same run twice cannot
  take the unique index, so it is refused with `duplicate_classifications` before anything
  changes; otherwise the whole upgrade runs in one transaction.
- **The read-only connection uses a file URI** built with `Path.as_uri()`, so paths with
  spaces, parentheses or `#` work.

### Collection

- **GitHub failures have names.** A 404 is `repo_not_found`, 401 and 403 are
  `github_auth_failed`, and a dropped connection is `network_error`. The project row is
  saved only after the first page of issues arrives, so a mistyped repository leaves
  nothing behind.
- **Issue counts use GraphQL.** GitHub search answers 422 for some public repositories,
  facebook/react among them, and the GraphQL count is exact.
- **The list request carries the requested direction.** Ascending collection with an end
  date or a count depends on GitHub returning the oldest issues first.
- **Fix attribution.** A closing PR is taken from the close event when it was merged
  within a day of the close (`CLOSE_EVENT_WINDOW_SECONDS`), otherwise from cross-referenced
  or referenced PRs merged within a week (`REFERENCE_WINDOW_SECONDS`), otherwise from the
  closing commit.

### Linkage

- **An issue links to the release that was live when it was filed:** the most recently
  published stable release at or before the filing date. Semver order is not chronological
  (a 1.9.5 backport can ship after 2.0.0). A prerelease is used only when no stable release
  qualifies, because a prerelease is never npm's `latest` tag and is not what a reporter
  installed. A naive "highest semver at or before the date" rule disagreed with this on 35%
  of a 53,000-issue corpus, almost all of it prereleases.
- **Timestamps are parsed, not compared as strings.** npm reports sub-second precision and
  GitHub does not; as strings `.` sorts before `Z` and a release looked earlier than it
  was. Parsing is cached because the same strings are compared millions of times.

### Front ends

- **Logs never go to stdout.** The CLI moves any logging handler that targets stdout to
  stderr, so `--json` output stays valid JSON.
- **Output is UTF-8.** Issue titles routinely contain arrows, emoji and CJK text. The CLI
  switches stdout and stderr to UTF-8 (unencodable characters are replaced) and log files
  default to UTF-8, so redirected output on Windows does not crash on them.
- **`.env` is read from the current folder.** `.env.example` leaves `INEX_PROFILE` empty
  so the bundled profile is used wherever the tool runs.
- **Agent tools keep their names.** The nine tool names match `prompts/system_prompt.md`
  and the routing evaluation in `evals/routing.py`, whose ten scenarios are documented in
  `agent_routing_validation.tex`.

## Commands

| Command | What it does |
|---|---|
| `python check.py` | Format check, lint and tests: run before calling work finished |
| `python -m pytest` | All tests |
| `python -m ruff format .` | Reformat every file |
| `python -m inex --help` | The command line |
| `python -m inex chat` | The chat agent |
| `python -m evals.routing` | Routing evaluation (needs LangSmith and an orchestrator key) |

## Decisions still open

- **Packaging.** A `pyproject.toml` and an `inex` console script.
- **Splitting the two excepted files**, as described above.
- **The GitHub Action** in `.github/workflows/issue-classify.yml` carries its own
  three-label prompt and is not profile-driven. Align it or remove it.

## Where these rules come from

The layering, configuration, error, validation and test rules are adapted from the project
owner's standards for their Cardline project, translated from Express and React to Python.
The rule against comments and docstrings is the project owner's.
