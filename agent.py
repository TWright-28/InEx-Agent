from langchain_ollama import ChatOllama
from config import orchestrator, temperature
from langchain.agents import create_agent
from tools.langchain_tools import get_stats, collect_all, classify_all
from langgraph.checkpoint.sqlite import SqliteSaver
import logging
import logging.config
import json
import os
import uuid
import sqlite3

with open("loggingConfigs/config.json") as f:
    logging.config.dictConfig(json.load(f))


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
    tools= [get_stats],
    system_prompt="You are a helpful assistant for analyzing and classifying GitHub bug reports.  You can query a database of classified bug reports from open source projects, you can also collect bug reports from a github repository and classify bug reports",
    checkpointer= checkpointer,
) 
thread_id = uuid.uuid4()
config = {"configureable": {"thread_id": thread_id}}


print(f"InEx Bug Agent - Thread {thread_id[:8]} - type 'q' to quit")

while True: 
    q = input("You: ")
    if q=="q":
        break
    inputs = {"messages": [{"role": "user", "content": q}]}
    results = agent.invoke(inputs, config=config)
    
    print(f"Agent: {results['messages'][-1].content}")
    
    