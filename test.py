from tools.lib.database import InExTool
from config import DB_PATH

db = InExTool(DB_PATH)

db.cursor.execute('''
    SELECT p.owner, p.repo, COUNT(i.id) 
    FROM projects p 
    JOIN issues i ON i.project_id = p.id 
    GROUP BY p.owner, p.repo
    ''')
for row in db.cursor.fetchall():
    print(f"  {row[0]}/{row[1]}: {row[2]} issues")

db.cursor.execute('SELECT COUNT(*) FROM issues')
print(f"\nTotal issues: {db.cursor.fetchone()[0]}")

db.cursor.execute('SELECT COUNT(*) FROM classifications')
print(f"Total classifications: {db.cursor.fetchone()[0]}")