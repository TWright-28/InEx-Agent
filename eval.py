import os
import sqlite3
import uuid
from unittest.mock import patch

from config import orchestrator, temperature, systemPrompt
from langchain_ollama import ChatOllama
from langchain.agents import create_agent
from langgraph.checkpoint.sqlite import SqliteSaver
from langsmith import Client

from db_summary import build_db_summary

os.environ["DATABASE_URL"] = "db/eval_test.db"

DATASET_NAME = "InEx-Agent-v1"

MOCK_COLLECT  = "Collected 10 issues from yeoman/yeoman-test (skipped 0 already in DB)."
MOCK_CLASSIFY = "Classified 10 issues from yeoman/yeoman-test."
MOCK_PREVIEW  = {
    "status": "ok", "owner": "yeoman", "repo": "yeoman-test",
    "would_classify": 10,
    "earliest_created": "2023-01-01T00:00:00Z",
    "latest_created":   "2023-12-01T00:00:00Z",
    "sample": [{"issue_number": 101, "title": "Test runner fails on async"}],
}
MOCK_SNAPSHOT = {
    "status": "ok", "package_name": "yeoman-test",
    "owner": "yeoman", "repo": "yeoman-test",
    "issue_versions_snapshotted": 3, "slice_versions_snapshotted": 0,
    "linked_issues": 3, "already_linked": 0, "unlinkable_pre_release": 0,
    "window": {"start": None, "end": None, "version_range": None,
               "last_n_versions": None, "version_start": None, "version_end": None},
}

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


def seed_test_db():
    from tools.helpers.database import InExTool
    db = InExTool("db/eval_test.db")
    db.connection.close()
    conn = sqlite3.connect("db/eval_test.db")
    conn.executescript(SEED_SQL)
    conn.commit()
    conn.close()
    print("Test DB seeded.")

EXAMPLES = [
    {
        "inputs":  {"request": "collect issues from yeoman/yeoman-test"},
        "outputs": {"expected_tool": "collect_all", "before": "count_issues"},
    },
    {
        "inputs":  {"request": "get the dependency snapshot for yeoman/yeoman-test "
                               "using the npm package yeoman-test"},
        "outputs": {"expected_tool": "snapshot_dependencies", "before": None},
    },
    {
        "inputs":  {"request": "classify the issues for yeoman/yeoman-test"},
        "outputs": {"expected_tool": "classify_all", "before": "preview_classification"},
    },
    {
        "inputs":  {"request": "how many issues does yeoman-test have and "
                               "what is the classification breakdown?"},
        "outputs": {"expected_tool": "run_sql", "before": None},
    },
    {
        "inputs":  {"request": "export the classification data for yeoman-test to a csv file"},
        "outputs": {"expected_tool": "export_data", "before": None},
    },
]


def ensure_dataset(client: Client):
    if client.has_dataset(dataset_name=DATASET_NAME):
        print(f"Dataset '{DATASET_NAME}' already exists - reusing.")
        return
    dataset = client.create_dataset(
        dataset_name=DATASET_NAME,
        description="Request -> expected tool call (with ordering) "
                    "for InEx-Bug-Agent orchestration validation.",
    )
    client.create_examples(dataset_id=dataset.id, examples=EXAMPLES)
    print(f"Created dataset '{DATASET_NAME}' with {len(EXAMPLES)} examples.")


def build_agent():
    from tools.langchain_tools import (
        collect_all, count_issues, classify_all, preview_classification,
        snapshot_dependencies, run_sql, describe_schema, export_data, db,
    )
    with open(systemPrompt, "r", encoding="utf-8") as f:
        base_prompt = f.read()
    system_prompt = base_prompt + "\n\n" + build_db_summary(db)

    llm = ChatOllama(model=orchestrator, temperature=temperature)
    mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
    checkpointer = SqliteSaver(mem_conn)

    return create_agent(
        model=llm,
        tools=[collect_all, count_issues, classify_all, preview_classification,
               snapshot_dependencies, run_sql, describe_schema, export_data],
        system_prompt=system_prompt,
        checkpointer=checkpointer,
    )


_AGENT = None 


def tool_call_sequence(messages):
    """Ordered names of tools the agent actually invoked."""
    seq = []
    for m in messages:
        for tc in getattr(m, "tool_calls", None) or []:
            seq.append(tc["name"])
    return seq


def target(inputs: dict) -> dict:
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent()

    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    agent_inputs = {"messages": [{"role": "user", "content": inputs["request"]}]}

    patches = [
        patch("tools.helpers.collect.Collect.countIssues", return_value=45),
        patch("tools.helpers.collect.Collect.collectAll", return_value=MOCK_COLLECT),
        patch("tools.helpers.classify.Classify.classifyAll", return_value=MOCK_CLASSIFY),
        patch("tools.helpers.classify.Classify.previewClassification", return_value=MOCK_PREVIEW),
        patch("tools.core.dependencies.DependencySnapshotter.snapshot_for_classified",
              return_value=MOCK_SNAPSHOT),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        result = _AGENT.invoke(agent_inputs, config=config)

    return {"tool_calls": tool_call_sequence(result["messages"])}


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

def main():
    seed_test_db()
    client = Client()
    ensure_dataset(client)

    results = client.evaluate(
        target,
        data=DATASET_NAME,
        evaluators=[correct_tool, correct_ordering],
        experiment_prefix="inex-agent-routing",
        max_concurrency=1,  # one Ollama instance; keep serial
    )
    print(results)


if __name__ == "__main__":
    main()