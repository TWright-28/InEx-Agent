from tools.database import InExTool
from config import DB_PATH
from langchain.tools import tool

db = InExTool(DB_PATH)

@tool('get_stats', description="Collect the stats about classification distributions from the database", return_direct=False)
def get_stats() -> str:
    results = db.get_stats()
    output = ""
    for label, count in results:
        output += f"{label}: {count}\n"
    return output
