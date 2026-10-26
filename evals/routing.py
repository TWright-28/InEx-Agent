import sqlite3
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import Client

from inex.agent.chat import build_agent
from inex.app import build_app
from inex.config import load_settings
from inex.services.classification import ClassifyPreview, ClassifyResult
from inex.services.collection import CollectResult, CountResult
from inex.services.imports import ImportService
from inex.services.linkage import LinkResult
from inex.store.database import Store

DATASET_NAME = "InEx-Agent-v5"
DEFAULT_COUNT = 45
EVAL_DIR = Path(tempfile.gettempdir()) / "inex_eval"
EVAL_DB = EVAL_DIR / "eval_test.db"

SEED_SQL = """
INSERT OR IGNORE INTO projects(id, owner, repo, package_name, added_at)
VALUES (1, 'yeoman', 'yeoman-test', 'yeoman-test', '2024-01-01T00:00:00');

INSERT OR IGNORE INTO issues(id, project_id, issue_number, title, body, state, created_at, version_id)
VALUES
    (1, 1, 101, 'Test runner fails on async', 'Fails with async/await patterns', 'closed', '2023-06-01T10:00:00Z', 1),
    (2, 1, 102, 'Breaks after sinon upgrade', 'After upgrading sinon 14 to 15 it breaks', 'closed', '2023-08-01T10:00:00Z', 2),
    (3, 1, 103, 'How do I mock ES modules?', 'Usage question about mocking', 'closed', '2023-09-01T10:00:00Z', 2);

INSERT OR IGNORE INTO classifications(id, issue_id, classification, model, prompt_version, temperature, classified_at)
VALUES
    (1, 1, 'Intrinsic', 'qwen3:30b', 'prompts/classification_prompt.md', 0.2, '2024-01-01T00:00:00'),
    (2, 2, 'Extrinsic', 'qwen3:30b', 'prompts/classification_prompt.md', 0.2, '2024-01-01T00:00:00'),
    (3, 3, 'Not a Bug', 'qwen3:30b', 'prompts/classification_prompt.md', 0.2, '2024-01-01T00:00:00');

INSERT OR IGNORE INTO versions(id, project_id, package_name, version, published_at,
                                direct_count, peer_count, dev_count, raw_manifest, snapshotted_at)
VALUES
    (1, 1, 'yeoman-test', '8.0.0', '2023-05-01T00:00:00Z', 9, 2, 15, '{}', '2024-01-01T00:00:00'),
    (2, 1, 'yeoman-test', '9.0.0', '2023-07-01T00:00:00Z', 7, 3, 14, '{}', '2024-01-01T00:00:00');

INSERT OR IGNORE INTO version_dependencies(version_id, dep_name, dep_kind, dep_version_range, depth)
VALUES
    (1, 'sinon',  'direct', '^14.0.0', 0),
    (1, 'mocha',  'direct', '^10.0.0', 0),
    (1, 'lodash', 'direct', '^4.17.0', 0),
    (2, 'sinon',  'direct', '^15.0.0', 0),
    (2, 'mocha',  'direct', '^10.0.0', 0);
"""


class FakeCollection:
    def __init__(self):
        self.issue_count = DEFAULT_COUNT

    def count(self, owner, repo):
        return CountResult(owner, repo, self.issue_count)

    def collect(self, owner, repo, **kwargs):
        return CollectResult(collected=10, skipped=0)


class FakeClassification:
    def preview(self, owner, repo, **kwargs):
        return ClassifyPreview(
            owner,
            repo,
            would_classify=10,
            total_remaining=10,
            model="qwen3:30b",
            prompt_version="inex@eval+t2",
            earliest_created="2023-01-01T00:00:00Z",
            latest_created="2023-12-01T00:00:00Z",
            sample=({"issue_number": 101, "title": "Test runner fails on async"},),
        )

    def classify(self, owner, repo, **kwargs):
        return ClassifyResult(owner, repo, classified=10, failed={})


class FakeLinkage:
    def link(self, owner, repo, package, **kwargs):
        return LinkResult(package, 3, 0, 3, 0, 0)


def seed_test_db():
    if EVAL_DB.exists():
        EVAL_DB.unlink()
    Store(EVAL_DB).close()
    connection = sqlite3.connect(EVAL_DB)
    connection.executescript(SEED_SQL)
    connection.commit()
    connection.close()


def build_eval_app(settings=None):
    if settings is None:
        settings = load_settings(database=EVAL_DB, exports_dir=EVAL_DIR / "exports")
    collection, classification = FakeCollection(), FakeClassification()
    app = replace(
        build_app(settings),
        collection=collection,
        classification=classification,
        linkage=FakeLinkage(),
    )
    return replace(app, imports=ImportService(app.queries.store, collection, classification))


_STATE = {}


def eval_agent():
    if "agent" not in _STATE:
        _STATE["app"] = build_eval_app()
        _STATE["saver"] = SqliteSaver.from_conn_string(":memory:")
        _STATE["agent"] = build_agent(_STATE["app"], _STATE["saver"].__enter__())
    return _STATE["app"], _STATE["agent"]


EXAMPLES = [
    {
        "inputs": {"request": "collect issues from yeoman/yeoman-test", "count_issues_returns": 45},
        "outputs": {
            "expected_tool": "collect_all",
            "before": "count_issues",
            "must_not_call": None,
        },
    },
    {
        "inputs": {
            "request": "get the dependency snapshot for yeoman/yeoman-test "
            "using the npm package yeoman-test"
        },
        "outputs": {
            "expected_tool": "snapshot_dependencies",
            "before": None,
            "must_not_call": None,
        },
    },
    {
        "inputs": {"turns": ["classify the issues for yeoman/yeoman-test", "yes, go ahead"]},
        "outputs": {
            "expected_tool": "classify_all",
            "before": "preview_classification",
            "must_not_call": None,
        },
    },
    {
        "inputs": {
            "turns": ["classify the issues for yeoman/yeoman-test", "no, don't classify them"]
        },
        "outputs": {
            "expected_tool": "preview_classification",
            "before": None,
            "must_not_call": "classify_all",
        },
    },
    {
        "inputs": {
            "request": "how many issues does yeoman-test have and "
            "what is the classification breakdown?"
        },
        "outputs": {"expected_tool": "run_sql", "before": None, "must_not_call": None},
    },
    {
        "inputs": {"request": "export the classification data for yeoman-test to a csv file"},
        "outputs": {"expected_tool": "export_data", "before": None, "must_not_call": None},
    },
    {
        "inputs": {
            "request": "how many issues does yeoman/yeoman-test have?",
            "count_issues_returns": 45,
        },
        "outputs": {
            "expected_tool": "count_issues",
            "before": None,
            "must_not_call": "collect_all",
        },
    },
    {
        "inputs": {
            "request": "show me what would be classified for yeoman/yeoman-test "
            "without actually classifying anything"
        },
        "outputs": {
            "expected_tool": "preview_classification",
            "before": None,
            "must_not_call": "classify_all",
        },
    },
    {
        "inputs": {"request": "collect the 50 most recent issues from yeoman/yeoman-test"},
        "outputs": {
            "expected_tool": "collect_all",
            "before": None,
            "must_not_call": "count_issues",
        },
    },
    {
        "inputs": {
            "request": "collect issues from yeoman/yeoman-test",
            "count_issues_returns": 5000,
        },
        "outputs": {
            "expected_tool": "count_issues",
            "before": None,
            "must_not_call": "collect_all",
        },
    },
]


def ensure_dataset(client: Client):
    if client.has_dataset(dataset_name=DATASET_NAME):
        print(f"Dataset '{DATASET_NAME}' already exists - reusing.")
        return
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Request -> expected tool call (with ordering and negative "
        "constraints) for InEx-Bug-Agent orchestration validation. "
        "Covers all 7 tool functions, standalone count, standalone "
        "preview, bounded collection, multi-turn confirm/decline, and "
        "the large-repo collect confirmation gate.",
    )
    client.create_examples(dataset_id=dataset.id, examples=EXAMPLES)
    print(f"Created dataset '{DATASET_NAME}' with {len(EXAMPLES)} examples.")


def tool_call_sequence(messages):
    seq = []
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            seq.append(tc["name"])
    return seq


def target(inputs: dict) -> dict:
    app, agent = eval_agent()
    app.collection.issue_count = inputs.get("count_issues_returns", DEFAULT_COUNT)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    all_tool_calls = []
    seen = 0
    for turn in inputs.get("turns") or [inputs["request"]]:
        result = agent.invoke({"messages": [{"role": "user", "content": turn}]}, config=config)
        all_tool_calls.extend(tool_call_sequence(result["messages"][seen:]))
        seen = len(result["messages"])
    return {"tool_calls": all_tool_calls}


def correct_tool(outputs: dict, reference_outputs: dict) -> dict:
    calls = outputs.get("tool_calls", [])
    return {
        "key": "correct_tool",
        "score": reference_outputs["expected_tool"] in calls,
    }


def correct_ordering(outputs: dict, reference_outputs: dict) -> dict:
    before = reference_outputs.get("before")
    if before is None:
        return {"key": "correct_ordering", "score": True}

    calls = outputs.get("tool_calls", [])
    expected = reference_outputs["expected_tool"]
    if before not in calls or expected not in calls:
        return {"key": "correct_ordering", "score": False}
    return {
        "key": "correct_ordering",
        "score": calls.index(before) < calls.index(expected),
    }


def must_not_appear(outputs: dict, reference_outputs: dict) -> dict:
    blocked = reference_outputs.get("must_not_call")
    if blocked is None:
        return {"key": "must_not_appear", "score": True}
    return {"key": "must_not_appear", "score": blocked not in outputs.get("tool_calls", [])}


def main():
    seed_test_db()
    client = Client()
    ensure_dataset(client)
    results = client.evaluate(
        target,
        data=DATASET_NAME,
        evaluators=[correct_tool, correct_ordering, must_not_appear],
        experiment_prefix="inex-agent-routing",
        max_concurrency=1,
    )
    print(results)


if __name__ == "__main__":
    main()
