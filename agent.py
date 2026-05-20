from langchain_ollama import ChatOllama
from config import orchestrator, temperature, systemPrompt
from langchain.agents import create_agent
from tools.langchain_tools import get_stats, collect_all, classify_all, count_issues, snapshot_dependencies, list_projects, run_sql, describe_schema
from langgraph.checkpoint.sqlite import SqliteSaver
import logging
import logging.config
import json
import os
import uuid
import sqlite3
from langgraph.types import Command

with open("loggingConfigs/config.json") as f:
    logging.config.dictConfig(json.load(f))

with open(systemPrompt, "r", encoding="utf-8") as f:
    system_prompt = f.read()

llm = ChatOllama(
    model= orchestrator,
    temperature= temperature
)

# checkpointer for our orch model to understand prior context
os.makedirs("db", exist_ok=True)
checkpoint_conn = sqlite3.connect("db/checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(checkpoint_conn)


agent = create_agent(
    model=llm,
    tools= [get_stats, collect_all, count_issues, classify_all, snapshot_dependencies, list_projects, run_sql, describe_schema],
    system_prompt=system_prompt,
    checkpointer= checkpointer,
) 
LAST_THREAD_FILE = "db/last_thread.txt"

thread_id = None
if os.path.exists(LAST_THREAD_FILE):
    with open(LAST_THREAD_FILE, "r") as f:
        previous = f.read().strip()
    if previous:
        answer = input(f"Resume previous session [{previous[:8]}]? (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            thread_id = previous

if thread_id is None:
    thread_id = str(uuid.uuid4())

with open(LAST_THREAD_FILE, "w") as f:
    f.write(thread_id)

config = {"configurable": {"thread_id": thread_id}}
print(f"InEx Bug Agent - Thread {thread_id[:8]} - type 'q' to quit")

while True: 
    q = input("You: ")
    if q=="q":
        break
    inputs = {"messages": [{"role": "user", "content": q}]}
    results = agent.invoke(inputs, config=config)

    print(f"Agent: {results['messages'][-1].content}")
    
    