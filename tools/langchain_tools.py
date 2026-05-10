from tools.database import InExTool
from tools.collect import Collect
from tools.classify import Classify
from config import DB_PATH, GITHUB_TOKEN, temperature, max_tokens, classifier, classifyPrompt
from langchain.tools import tool
from langgraph.types import interrupt
db = InExTool(DB_PATH)
cl = Classify(classifier, temperature, classifyPrompt)

ISSUE_THRESHOLD = 1000 

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
    count = collector.countIssues(owner, repo)
    
    if count >= ISSUE_THRESHOLD:
        answer = interrupt({
            "message": f"{owner}/{repo} has {count}{'+' if count >= 1000 else ''} issues. This will take a while. Proceed? (y/n)",
            "owner": owner,
            "repo": repo,
            "count": count,
        })
    if str(answer).strip().lower() not in ("y", "yes"):
        return f"Collection cancelled for {owner}/{repo}."
    
    return collector.collectAll(owner, repo, db)

@tool('classify_all', description="Classify all the issues from a github repo in our database and classify them")
def classify_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    return cl.classifyAll(owner, repo, db)