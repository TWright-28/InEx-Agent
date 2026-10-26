import uuid

from langchain.agents import create_agent
from langgraph.checkpoint.sqlite import SqliteSaver

from inex.agent.tools import build_tools

QUIT = "q"
TAXONOMY_PLACEHOLDER = "{{TAXONOMY}}"
THREAD_LABEL_CHARS = 8


def render_system_prompt(template, profile):
    taxonomy = "\n".join(f"  - {label.name}: {label.description}" for label in profile.labels)
    return template.replace(TAXONOMY_PLACEHOLDER, taxonomy)


def build_llm(settings):
    provider, _, model_name = settings.orchestrator.partition(":")
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model_name, temperature=settings.temperature, num_ctx=settings.num_ctx
        )
    from langchain.chat_models import init_chat_model

    return init_chat_model(settings.orchestrator, temperature=settings.temperature)


def build_agent(app, checkpointer):
    return create_agent(
        model=build_llm(app.settings),
        tools=build_tools(app),
        system_prompt=render_system_prompt(
            app.settings.system_prompt.read_text(encoding="utf-8"), app.profile
        ),
        checkpointer=checkpointer,
    )


def run_chat(app) -> None:
    app.settings.checkpoints.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(app.settings.checkpoints)) as checkpointer:
        agent = build_agent(app, checkpointer)
        thread_id = str(uuid.uuid4())
        config = {"configurable": {"thread_id": thread_id}}
        print(f"InEx Bug Agent - Thread {thread_id[:THREAD_LABEL_CHARS]} - type '{QUIT}' to quit")
        while True:
            question = input("You: ")
            if question.strip() == QUIT:
                return
            result = agent.invoke(
                {"messages": [{"role": "user", "content": question}]}, config=config
            )
            print(f"Agent: {result['messages'][-1].content}")
