import json
import sys
import glob
from tools.lib.database import InExTool
from config import DB_PATH

target = sys.argv[1] if len(sys.argv) > 1 else None

if not target:
    print("Usage: python load_jsonl.py <path_to_jsonl_or_directory>")
    sys.exit(1)

import os
if os.path.isdir(target):
    files = glob.glob(os.path.join(target, "**", "*.jsonl"), recursive=True)
else:
    files = [target]

if not files:
    print(f"No .jsonl files found in {target}")
    sys.exit(1)

db = InExTool(DB_PATH)

total_loaded = 0
total_classified = 0

for filepath in files:
    print(f"\nLoading {filepath}...")
    loaded = 0
    classified = 0

    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  Skipping bad line: {e}")
                continue

            owner = data.get("owner")
            repo = data.get("repo")
            project_id = db.save_project(owner, repo)

            issue_id = db.save_issue(project_id, data)

            if data.get("classification"):
                db.cursor.execute(
                    "SELECT COUNT(*) FROM classifications WHERE issue_id = ?", (issue_id,)
                )
                if db.cursor.fetchone()[0] == 0:
                    db.save_classification(issue_id, data)
                    classified += 1

            loaded += 1

            if loaded % 100 == 0:
                print(f"  {loaded} issues...")

    print(f"  Done: {loaded} issues, {classified} classified.")
    total_loaded += loaded
    total_classified += classified

print(f"\nTotal: {total_loaded} issues loaded, {total_classified} classified across {len(files)} files.")
