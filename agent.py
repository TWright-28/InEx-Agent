from langchain_ollama import ChatOllama
from config import orchestrator, temperature
from langchain.agents import create_agent
from tools.langchain_tools import get_stats

llm = ChatOllama(
    model= orchestrator,
    temperature= temperature,
)
agent = create_agent(
    model=llm,
    tools= [get_stats],
    system_prompt="You are a helpful assistant for analyzing and classifying GitHub bug reports.  You can query a database of classified bug reports from open source projects.",
    
) 
print("InEx Bug Agent - type 'q' to quit")

while True: 
    q = input("You: ")
    if q=="q":
        break
    inputs = {"messages": [{"role": "user", "content": q}]}
    results = agent.invoke(inputs)
    
    print(f"Agent: {results['messages'][-1].content}")
    
    