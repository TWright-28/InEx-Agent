"""Shared fixtures.

DATABASE_URL must be set before anything imports config, because config reads it
at import time and tools.helpers.sqlQuery opens a read-only connection on import.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SCRATCH = Path(tempfile.gettempdir()) / "icsme_tests"
_SCRATCH.mkdir(exist_ok=True)
os.environ.setdefault("DATABASE_URL", str(_SCRATCH / "import_guard.db"))
# The read-only connection cannot create the file, so it must already exist.
sqlite3.connect(os.environ["DATABASE_URL"]).close()

PROMPT = str(ROOT / "prompts" / "classification_prompt.md")

FULL_PROBS = {"Intrinsic": 0.90, "Extrinsic": 0.03, "Not a Bug": 0.04, "Unknown": 0.03}


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def db(db_path):
    from tools.helpers.database import InExTool
    return InExTool(db_path)


@pytest.fixture
def seeded(db):
    """A project with three issues and no classifications."""
    pid = db.save_project("acme", "widget")
    ids = []
    for n in (101, 102, 103):
        ids.append(db.save_issue(pid, {
            "number": n, "title": f"issue {n}", "body": "body",
            "state": "closed", "state_reason": "completed",
            "created_at": f"2023-0{n - 100}-01T00:00:00Z",
        }))
    return db, pid, ids


@pytest.fixture
def classifier():
    """Ollama-path classifier: never reaches the network in these tests."""
    from tools.helpers.classify import Classify
    cl = Classify("qwen3:30b", 0.2, PROMPT, workers=1)
    cl.retry_wait = lambda *a, **k: 0
    return cl


@pytest.fixture
def cloud_classifier():
    """Cloud-path classifier: constructed only, no request is ever issued."""
    from tools.helpers.classify import Classify
    os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used")
    cl = Classify("google_genai:gemini-2.5-pro", 0.2, PROMPT, workers=1)
    cl.retry_wait = lambda *a, **k: 0
    return cl


def row_for(issue_id, number=42, state_reason=None, raw_data=None):
    """A row shaped like getUnclassifiedInWindow returns."""
    return (issue_id, number, "a title", "a body", "closed", state_reason,
            "2023-01-01T00:00:00Z", "2023-02-01T00:00:00Z", raw_data)
