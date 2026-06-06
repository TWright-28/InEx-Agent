import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.executescript("""
    DELETE FROM version_dependencies;
    DELETE FROM versions;
    DELETE FROM classifications;
    DELETE FROM issues;
    DELETE FROM projects;
    DELETE FROM sqlite_sequence;
""")

conn.commit()
conn.close()
print("Database flushed.")