from tools.database import InExTool
from tools.collect import Collect
from config import DB_PATH, GITHUB_TOKEN
from langchain.tools import tool

db = InExTool(DB_PATH)

@tool('get_stats', description="Collect the stats about classification distributions from the database", return_direct=False)
def get_stats() -> str:
    results = db.get_stats()
    output = ""
    for label, count in results:
        output += f"{label}: {count}\n"
    return output

@tool('collect_all', description="Collect all the issues from a github repo and save to the database, Input should be in owner/repo format")
def collect_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    return collector.collectAll(owner, repo, db)
