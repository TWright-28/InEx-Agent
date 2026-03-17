import sqlite3
from config import DB_PATH


class InExTool:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path)
        self.cursor = self.connection.cursor()
        self._create_tables()

    def _create_tables(self):
        
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL, 
            repo TEXT NOT NULL,
            added_at TEXT,
            UNIQUE(owner, repo)
            ) """)
        
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS issues(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            project_id INTEGER NOT NULL,
            issue_number INTEGER NOT NULL, 
            github_id INTEGER, 
            url TEXT, 
            title TEXT, 
            body TEXT, 
            state TEXT, 
            state_reason TEXT, 
            locked INTEGER, 
            created_at TEXT, 
            closed_at TEXT, 
            updated_at TEXT, 
            comments_counts TEXT, 
            raw_data TEXT,
            FOREIGN KEY (project_id) REFERENCES projects (id), 
            UNIQUE(project_id, issue_number)
            ) """)

        self.cursor.execute("""CREATE TABLE IF NOT EXISTS classifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            issue_id INTEGER NOT NULL, 
            classification TEXT, 
            classification_probabilities TEXT, 
            classification_raw_response TEXT, 
            model TEXT, 
            prompt_version TEXT, 
            temperature REAL, 
            classified_at TEXT, 
            FOREIGN KEY (issue_id) REFERENCES issues(id)
        )""")
        
        self.connection.commit()