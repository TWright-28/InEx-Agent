"""Collect + classify a CSV list of 'owner/repo#number' issues into a separate DB.

Usage:
    python import_list.py issues.csv
    python import_list.py issues.csv --no-classify
    python import_list.py issues.csv --db db/icsme_issues.db --column resolved_issues
"""
import os
import sys
import json
import time

# Route everything into a separate DB so the real one is never touched.
# Set BEFORE importing config/tools (DB_PATH is read at import time).
args = sys.argv[1:]
db_path = "db/icsme_issues.db"
if "--db" in args:
    i = args.index("--db")
    db_path = args[i + 1]
    del args[i:i + 2]
os.environ["DATABASE_URL"] = db_path

column = None
if "--column" in args:
    i = args.index("--column")
    column = args[i + 1]
    del args[i:i + 2]

classify = "--no-classify" not in args
args = [a for a in args if a != "--no-classify"]

csv_path = args[0] if args else "issues.csv"

# Create the schema/file before read-only consumers (sqlQuery) import.
from tools.helpers.database import InExTool
InExTool(db_path)

from tools.langchain_tools import import_issue_list

print(f"DB:        {db_path}")
print(f"CSV:       {csv_path}")
print(f"Classify:  {classify}")
print("-" * 40)

tool_input = {"csv_path": csv_path, "classify": classify}
if column:
    tool_input["column"] = column

t = time.time()
result = import_issue_list.invoke(tool_input)
print(f"\nElapsed: {round((time.time() - t) / 60, 1)} min")
print(json.dumps(result, indent=2))
