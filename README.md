# InEx Bug Agent

An agentic, conversational tool for collecting, classifying, and analyzing GitHub bug reports from the npm ecosystem. It exists to study a single research question:

> **Do projects with more — or different kinds of — dependencies tend to produce more _Extrinsic_ bugs?**

You talk to the tool in plain language ("collect issues from `yeoman/yeoman-test`", "classify them", "what's the Intrinsic/Extrinsic breakdown?"). An LLM orchestrator decides which tools to call, runs them against a local SQLite database, and reasons over the results.

---

## Concepts

The database is organized around five record types:

| Record             | What it is                                                                 |
| ------------------ | -------------------------------------------------------------------------- |
| **Project**        | A GitHub repository (`owner/repo`), optionally with an npm package name.   |
| **Issue**          | A single GitHub bug report belonging to a project.                         |
| **Classification** | A label on an issue: `Intrinsic`, `Extrinsic`, `Not-a-Bug`, or `Unknown`.  |
| **Version**        | One published npm release of a project, with the dependencies it declared. |
| **Dependency**     | A `direct`, `peer`, or `dev` dependency belonging to a version.            |

**The three classification labels:**

- **Intrinsic** — root cause is _inside_ the repo (a logic error, missing validation, regression from its own code).
- **Extrinsic** — root cause is _outside_ the repo (a dependency, runtime, or toolchain change the repo had to adapt to).
- **Not-a-Bug** — not a defect in production code (usage questions, docs, feature requests, test/CI issues, user misconfiguration).
- **Unknown** — insufficient information to classify confidently.

Each classified issue is linked to the npm version that was the latest release at the time the issue was filed, so dependency counts can be compared against bug type.

---

## How it works

```
You (natural language)
        │
        ▼
  Orchestrator LLM  ──────────────►  Tools  ──────────►  SQLite DB
 (config.orchestrator)                                  (db/*.db)
        │
        ├─ count_issues / collect_all      ── GitHub API
        ├─ preview_classification / classify_all ── Classifier LLM (local Ollama)
        ├─ snapshot_dependencies           ── npm registry
        └─ run_sql / export_data           ── read-only analysis
```

The **orchestrator** is the model you chat with; it routes requests to tools. The **classifier** is a separate, locally-run model (via Ollama) that labels each issue. The two are configured independently in [config.py](config.py).

### The standard workflow

1. **`collect_all`** — pull issues from a GitHub repo into the database.
2. **`classify_all`** — label each collected issue (one LLM call per issue; slow).
3. **`snapshot_dependencies`** — fetch npm data, find the version live when each issue was filed, and link issues to versions.

After step 3 the data is ready for analysis via `run_sql` or `export_data`.

The agent confirms before expensive work: unbounded collection on a large repo (`count_issues` ≥ 1000) and any `classify_all` run (after showing a `preview_classification`) require user confirmation.

### Available tools

| Tool                     | Purpose                                                                     |
| ------------------------ | --------------------------------------------------------------------------- |
| `count_issues`           | Count a repo's issues without collecting them.                              |
| `collect_all`            | Collect issues from a repo (filter by count / date / direction).            |
| `preview_classification` | Show what `classify_all` _would_ classify — count, date span, sample. Fast. |
| `classify_all`           | Classify unclassified issues. Resumable in batches.                         |
| `snapshot_dependencies`  | Fetch npm versions + dependencies and link issues to them.                  |
| `run_sql`                | Run a read-only `SELECT` for analysis.                                      |
| `export_data`            | Export a `SELECT` to CSV/JSON in `exports/`.                                |

---

## Setup

### Prerequisites

- **Python 3.11+**
- **[Ollama](https://ollama.com/)** running locally, for the classifier model (and optionally the orchestrator).
- A **GitHub personal access token** (for collecting issues).
- Optionally a cloud LLM API key (OpenAI / Anthropic / Google) if you don't want to run the orchestrator locally.

### Install

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1          # PowerShell
pip install -r requirements.txt
```

Pull the classifier model with Ollama (default is `qwen2.5:7b-instruct`):

```powershell
ollama pull qwen2.5:7b-instruct
```

### Configure

Create a `.env` file in the project root:

```ini
GITHUB_TOKEN=ghp_your_token_here
DATABASE_URL=db/database.db

# Only needed if the orchestrator uses a cloud provider:
GOOGLE_API_KEY=...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Optional: LangSmith tracing / evaluation
LANGCHAIN_TRACING=true
LANGCHAIN_API_KEY=...
LANGCHAIN_PROJECT=...
```

Then choose your models in [config.py](config.py):

```python
# Orchestrator — the model you chat with. "provider:model"
orchestrator = "ollama:gpt-oss:20B"
# orchestrator = "openai:gpt-4o"
# orchestrator = "anthropic:claude-sonnet-4-6"
# orchestrator = "google_genai:gemini-2.5-flash"

# Classifier — always runs locally via Ollama
classifier = "qwen2.5:7b-instruct"
```

### Initialize the database

```powershell
python wipe.py     # drops and rebuilds the schema (destructive)
```

---

## Usage

Start the interactive agent:

```powershell
python agent.py
```

You'll get a prompt. Type requests in plain language; type `q` to quit.

```
InEx Bug Agent - Thread a1b2c3d4 - type 'q' to quit
You: collect the 50 most recent issues from yeoman/yeoman-test
You: classify them
You: get the dependency snapshot for yeoman/yeoman-test using the npm package yeoman-test
You: what's the Intrinsic/Extrinsic breakdown, and how do dependency counts differ between them?
You: export the classification data to a csv
```

Conversation state is checkpointed in `db/checkpoints.db`, so the agent remembers context across turns within a session.

### Loading existing data

If you already have collected/classified issues as JSONL (see [data/](data/)), seed them directly:

```powershell
python seeder.py data/                     # load every .jsonl in a directory
python seeder.py data/yeoman_merged.jsonl  # or a single file
```

---

## Analysis patterns

The system is tuned for these questions (the agent will run patterns 1–2 automatically when you ask it to "analyse" a project):

1. **Classification breakdown** — counts of Intrinsic / Extrinsic / Not-a-Bug / Unknown.
2. **Dependency counts by classification** — avg direct/peer/dev deps for Intrinsic vs Extrinsic issues _(the core research question)_.
3. **Issue-level detail** — each issue with its classification, version, and dependency counts.
4. **Cross-project comparison** — Extrinsic count and percentage per project.
5. **Modeled Extrinsic odds** (Wright et al.) — per-dependency odds ratio of 1.011; for a version with _N_ direct deps, `odds_increase_pct = (1.011^N − 1) × 100`. Reported as a modeled estimate.

---

## GitHub Action: live issue classification

[.github/workflows/issue-classify.yml](.github/workflows/issue-classify.yml) runs an AI classification whenever an issue is opened or closed in the repo, posting the result as a comment. New issues get an initial classification (title/body/labels only); closed issues get a final one using full context (maintainer comments + closing PR). It calls Groq and requires a `GROQ_API_KEY` repo secret, and only runs for `OWNER`/`MEMBER`/`COLLABORATOR` authors to protect API credits.

---

## Database schema

```
projects(id, owner, repo, package_name, added_at)
issues(id, project_id, issue_number, title, body, state, created_at, version_id, ...)
classifications(id, issue_id, classification, model, prompt_version, temperature, classified_at, ...)
versions(id, project_id, package_name, version, published_at, direct_count, peer_count, dev_count, snapshotted_at, ...)
version_dependencies(version_id, dep_name, dep_kind, dep_version_range, depth, ...)
```

**Key joins:** `issues.project_id → projects.id`, `classifications.issue_id → issues.id`, `issues.version_id → versions.id`, `version_dependencies.version_id → versions.id`.

---

## Project layout

```
agent.py                  Interactive CLI entry point
config.py                 Model selection, paths, env loading
eval.py                   LangSmith evaluation of agent tool-routing
seeder.py                 Load issues/classifications from JSONL
wipe.py                   Drop & rebuild the database schema (destructive)
flush.py                  Delete all rows, keep the schema
prompts/
  system_prompt.md        Orchestrator instructions
  classification_prompt.md Classifier guide (Intrinsic/Extrinsic/Not-a-Bug/Unknown)
tools/
  langchain_tools.py      The seven LangChain @tool definitions
  helpers/                collect, classify, database, sqlQuery
  core/                   dependencies, npm_registry, analysis
data/                     Pre-collected issue datasets (JSONL)
exports/                  Generated CSV/JSON exports
db/                       SQLite databases & checkpoints
```

---

## Maintenance scripts

| Script            | Effect                                                                        |
| ----------------- | ----------------------------------------------------------------------------- |
| `python wipe.py`  | **Destructive.** Drops all tables and recreates the schema.                   |
| `python flush.py` | Deletes all rows but keeps the schema.                                        |
| `python eval.py`  | Runs the LangSmith tool-routing evaluation suite (requires LangSmith config). |
