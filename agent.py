from config import orchestrator, temperature, systemPrompt, num_ctx
from langchain.agents import create_agent
from tools.langchain_tools import collect_all, classify_all, count_issues, preview_classification, snapshot_dependencies, run_sql, export_data
from langgraph.checkpoint.sqlite import SqliteSaver
import logging
import logging.config
import json
import os
import uuid
import sqlite3

with open("loggingConfigs/config.json") as f:
    logging.config.dictConfig(json.load(f))

with open(systemPrompt, "r", encoding="utf-8") as f:
    base_prompt = f.read()

system_prompt = base_prompt


def build_llm():
    provider, _, model_name = orchestrator.partition(":")
    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=model_name, temperature=temperature, num_ctx=num_ctx)
    from langchain.chat_models import init_chat_model
    return init_chat_model(orchestrator, temperature=temperature)


llm = build_llm()

# checkpointer for our orch model to understand prior context
os.makedirs("db", exist_ok=True)
checkpoint_conn = sqlite3.connect("db/checkpoints.db", check_same_thread=False)
checkpointer = SqliteSaver(checkpoint_conn)


agent = create_agent(
    model=llm,
    tools= [collect_all, count_issues, classify_all, preview_classification, snapshot_dependencies, run_sql, export_data,],
    system_prompt=system_prompt,
    checkpointer=checkpointer,
)

thread_id = str(uuid.uuid4())
config = {"configurable": {"thread_id": thread_id}}
print(f"InEx Bug Agent - Thread {thread_id[:8]} - type 'q' to quit")

while True: 
    q = input("You: ")
    if q=="q":
        break
    inputs = {"messages": [{"role": "user", "content": q}]}
    results = agent.invoke(inputs, config=config)

    print(f"Agent: {results['messages'][-1].content}")
    
    