from tools.lib.database import InExTool
from tools.lib.collect import Collect
from tools.lib.classify import Classify
from config import DB_PATH, GITHUB_TOKEN, temperature, max_tokens, classifier, classifyPrompt
from langchain.tools import tool
db = InExTool(DB_PATH)
cl = Classify(classifier, temperature, classifyPrompt)

@tool('get_stats', description="Collect the stats about classification distributions from the database", return_direct=False)
def get_stats() -> str:
    results = db.get_stats()
    output = ""
    for label, count in results:
        output += f"{label}: {count}\n"
    return output

@tool('count_issues', description="Count the total number of issues from a github repo without collecting them. Use this before Collect_all to check if the repo is large. Input should be owner/repo. Note: counts above 1000 are reported as '1000+' due to GitHub API limits.")
def count_issues(repoName: str) -> str:
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    count = collector.countIssues(owner, repo)
    if count >= 1000: 
        return f"{owner}/{repo} has 1000+ issues (exact count unavailable due to GitHub API limit)."
    return f"{owner}/{repo} has {count} issues."

@tool('collect_all', description="Collect all the issues from a github repo and save to the database, Input should be in owner/repo format")
def collect_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    collector = Collect(GITHUB_TOKEN)
    return collector.collectAll(owner, repo, db)

@tool('classify_all', description="Classify all the issues from a github repo in our database and classify them")
def classify_all(repoName: str) -> str: 
    owner, repo = repoName.split("/")
    return cl.classifyAll(owner, repo, db)