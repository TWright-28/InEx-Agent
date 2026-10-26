# InEx

A tool for collecting, classifying and analysing GitHub bug reports from the npm ecosystem.
It was built to study one research question:

> **Do projects with more, or different kinds of, dependencies tend to produce more bugs whose root cause lies outside the project?**

InEx collects issues together with the evidence that explains them (maintainer comments,
the closing pull request or commit), classifies each issue with an LLM against a
configurable taxonomy, and links each issue to the npm release that was live when it was
filed. Everything is available from a command line, and the same operations are available
to a chat agent.

---

## Concepts

The database holds five kinds of record:

| Record | What it is |
|---|---|
| **Project** | A GitHub repository (`owner/repo`), optionally with an npm package name. |
| **Issue** | A single GitHub issue, with its comments, closing PR or commit, and timing metrics. |
| **Classification** | One label for an issue from the active profile, plus the model's reasoning, probabilities and run key (model, prompt version, temperature). |
| **Version** | One published npm release of a project, with the dependencies it declared. |
| **Dependency** | A `direct`, `peer` or `dev` dependency of a version. |

### Classification profiles

A profile (`profiles/*.yaml`) names the labels, the reasoning steps the model must work
through, and the guide it reads. The default profile, `profiles/inex.yaml`, is the InEx
taxonomy:

- **Intrinsic**: the root cause is inside the repository.
- **Extrinsic**: the root cause is outside the repository (a dependency, runtime or toolchain change).
- **Not a Bug**: not a defect in production code.
- **Unknown**: not enough information to classify.

`profiles/example-severity.yaml` shows a different taxonomy (Critical / Major / Minor).
To add your own, copy a profile and its guide, change the labels and reasoning steps, and
select it with `--profile` or `INEX_PROFILE`. A broken profile is rejected when the tool
starts, before any work.

Each run is identified by its model, temperature and prompt version
(`<profile>@<hash of the profile and guide>+t<template version>`), so results from
different profiles, models or prompt revisions sit side by side and never overwrite each
other. Classifications made before profiles existed carry a path-based prompt version and
show as unclassified under the new scheme.

A failed model call is never stored as a label: it is recorded with its status and retried
on the next run.

---

## Setup

### Prerequisites

- **Python 3.11+**
- A **GitHub personal access token** for collecting issues.
- A model: either a cloud API key (Google, OpenAI or Anthropic) or
  **[Ollama](https://ollama.com/)** running locally.

### Install

```bash
python -m venv venv
./venv/Scripts/activate          # Windows; use venv/bin/activate elsewhere
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, for development
```

### Configure

Copy `.env.example` to `.env` and fill in what you need. Every setting can also be passed
on the command line (`--db`, `--classifier`, `--profile`).

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `db/database.db` | SQLite database file |
| `GITHUB_TOKEN` | none | Required for `count`, `collect` and `import` |
| `INEX_CLASSIFIER` | `google_genai:gemini-2.5-pro` | Model that classifies issues |
| `INEX_ORCHESTRATOR` | `google_genai:gemini-2.5-flash` | Model behind the chat agent |
| `INEX_TEMPERATURE` | `0.2` | Sampling temperature for both |
| `INEX_WORKERS` | `8` | Issues classified in parallel (use 1 for a local Ollama model) |
| `INEX_RETRIES` | `5` | Attempts per issue before it is recorded as failed |
| `INEX_PROFILE` | `profiles/inex.yaml` | Classification profile |
| `OLLAMA_BASE` | `http://localhost:11434` | Ollama server |
| `INEX_NUM_CTX` | `131072` | Context window for an Ollama orchestrator |

Model names are `provider:model` for cloud models and a bare Ollama tag for local ones:

| Example | Runs on |
|---|---|
| `google_genai:gemini-2.5-pro`, `google_genai:gemini-2.5-flash` | Google (`GOOGLE_API_KEY`) |
| `openai:gpt-4o` | OpenAI (`OPENAI_API_KEY`) |
| `anthropic:claude-sonnet-4-6` | Anthropic (`ANTHROPIC_API_KEY`) |
| `qwen3:30b`, `qwen2.5:7b-instruct` | Ollama, locally (`ollama pull qwen3:30b` first) |
| `ollama:gpt-oss:20B` | Ollama, as the chat orchestrator |

---

## Usage

```bash
python -m inex [--db PATH] [--classifier MODEL] [--profile PATH] [--json] COMMAND
```

| Command | What it does |
|---|---|
| `count OWNER/REPO` | Count a repository's issues on GitHub without collecting them |
| `collect OWNER/REPO [--count N] [--since DATE] [--until DATE] [--oldest-first]` | Collect issues and their evidence into the database |
| `classify OWNER/REPO [same window options] [--preview]` | Classify collected issues; `--preview` shows what would be classified |
| `link OWNER/REPO --package NAME [--since DATE] [--until DATE]` | Link each classified issue to the npm release live when it was filed |
| `import LIST.csv [--column NAME] [--collect-only] [--preview]` | Collect and classify a CSV of `owner/repo#number` references |
| `load FILE_OR_DIR --model M --prompt-version P [--temperature T]` | Load issues and labels from JSONL files |
| `query "SELECT ..."` | Run a read-only query (first 50 rows) |
| `export "SELECT ..." [--format csv\|json] [--name NAME]` | Export a query to `exports/` |
| `chat` | Talk to the agent |
| `db init` / `db reset --yes` / `db clear --yes` | Create, rebuild or empty the database |

`--json` prints every result as JSON, for scripts and notebooks. Errors exit with code 1,
usage errors with 2, and Ctrl+C with 130.

A typical session:

```bash
python -m inex count yeoman/yeoman-test
python -m inex collect yeoman/yeoman-test --count 50
python -m inex classify yeoman/yeoman-test --preview
python -m inex classify yeoman/yeoman-test
python -m inex link yeoman/yeoman-test --package yeoman-test
python -m inex query "SELECT model, prompt_version, classification, COUNT(*) FROM classifications WHERE status IS NULL OR status = 'ok' GROUP BY model, prompt_version, classification"
```

Always filter out failed attempts (`status IS NULL OR status = 'ok'`) and group or filter by
`model` and `prompt_version`: an issue classified by two models or two prompt revisions has
two rows.

### The chat agent

`python -m inex chat` starts a conversation with an orchestrator model that has the same
operations as tools. It counts a repository before an open-ended collection, previews
before classifying, and asks for confirmation before slow work. Conversation history is
kept in `db/checkpoints.db`.

---

## Analysis patterns

The agent runs patterns 1 and 2 when asked to "analyse" a project, and offers the rest:

1. **Classification breakdown**: the count of each label.
2. **Dependency counts by classification**: average direct, peer and dev dependencies per label (the core research question).
3. **Issue-level detail**: each issue with its classification, version and dependency counts.
4. **Cross-project comparison**: the count and share of each label per project.
5. **Modeled odds (InEx profile only)**: per-dependency odds ratio of 1.011 for a dependency-caused bug (Wright et al.); for a version with N direct dependencies, `odds_increase_pct = (1.011^N - 1) * 100`, reported as a modeled estimate.

---

## Database schema

```
projects(id, owner, repo, package_name, added_at)
issues(id, project_id, issue_number, title, body, state, state_reason, created_at, closed_at, version_id, raw_data, ...)
classifications(id, issue_id, classification, classification_probabilities, model, prompt_version, temperature,
                classified_at, status, error, validation_flags, argmax_label, attempts)
versions(id, project_id, package_name, version, published_at, direct_count, peer_count, dev_count, ...)
version_dependencies(version_id, dep_name, dep_kind, dep_version_range, ...)
```

Every query that counts classifications should filter `status IS NULL OR status = 'ok'`:
failed attempts are rows too. Opening an older database adds any missing columns and
indexes without changing rows; keep archived databases as they are and work on a copy.

---

## Project layout

```
inex/
  cli.py, __main__.py   Command line (python -m inex)
  agent/                Chat agent: tools and orchestrator
  app.py                build_app: wires the store, clients and services
  config.py             Settings, read from .env and flags
  validators.py         Input checks shared by both front ends
  errors.py             InexError
  services/             collection, classification, profiles, linkage, imports, queries, maintenance
  store/                SQLite schema and every query
  clients/              npm registry client
profiles/               Classification profiles and their guides
prompts/                The chat agent's system prompt
evals/                  Agent routing evaluation (LangSmith)
tests/                  Test suite
docs/architecture.md    Layers, standards, and the reasoning behind non-obvious code
check.py                Format check, lint and tests
```

---

## Development

```bash
python check.py
```

runs the format check, lint and the test suite; it is the gate for every change. The rules
it enforces (layering, no environment access outside `config.py`, nothing at import time,
no comments) are described in [docs/architecture.md](docs/architecture.md).

## GitHub Action

[.github/workflows/issue-classify.yml](.github/workflows/issue-classify.yml) classifies
issues as they are opened or closed and posts the result as a comment. It calls Groq with
its own three-label prompt and does not use profiles yet; see "Decisions still open" in
`docs/architecture.md`.
