# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

InEx: a tool for collecting GitHub issues from npm projects, classifying each one with an LLM against a configurable taxonomy (the default profile labels root causes as Intrinsic, Extrinsic, Not a Bug or Unknown), and linking each issue to the npm release that was live when it was filed. One Python package, `inex/`, with a command line (`python -m inex`) and a chat agent (`python -m inex chat`) over the same services. It is the artifact for an MSR 2027 Data and Tool Showcase paper.

## Project rules

- **A failed classification is never a label.** A failed attempt is stored with a NULL `classification` and a non-ok `status`. Every query that counts or groups classifications filters `status IS NULL OR status = 'ok'`.
- **`db/InExTool.db` is frozen.** It is the 71,640-issue research corpus. Opening it with the tool adds columns and an index, so point the tool at a copy and never commit a changed copy.
- **No comments or docstrings** in any Python file, tests included. Put reasoning in `docs/architecture.md`.
- **Layer rules are enforced** by `tests/test_architecture.py`. `inex/services/collection.py` and `inex/services/classification.py` are excepted until they are split; do not add to that list.
- Run `python check.py` before calling work finished.
- `docs/architecture.md` is the source of truth for layers, standards and the reasoning behind non-obvious code. If a change alters a rule there, update the doc in the same change.

## Commands

```bash
python check.py                                   # format check + lint + tests; the gate for every change
python -m pytest tests/test_store.py              # one file
python -m pytest -k "golden"                      # tests by name
python -m ruff format .                           # reformat
python -m inex --help                             # the command line
python -m inex --db db/scratch.db db init         # a scratch database
python -m evals.routing                           # agent routing evaluation (needs LangSmith and an orchestrator key)
```

Use the virtualenv's interpreter (`./venv/Scripts/python.exe` on this machine).

## Architecture

```
cli.py, agent/  ->  app.py (build_app)  ->  services/  ->  store/ (SQLite), clients/ (npm)
```

- Front ends check input with `validators.py`, call one service, and format the result. Services return frozen dataclasses and raise `InexError`.
- `config.py` is the only reader of environment variables; nothing runs at import time.
- Classification profiles live in `profiles/` (YAML plus guide). The golden tests in `tests/test_profile_classification.py` and `tests/test_profiles.py` pin the InEx profile's prompts and response schema byte-for-byte; a change that breaks them changes what the model sees.

See `docs/architecture.md` for the full rules and the reasoning behind them.
