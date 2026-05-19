import sqlite3
from config import DB_PATH
from datetime import datetime
import json 

class InExTool:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
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
            comments_count INTEGER, 
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
            FOREIGN KEY (issue_id) REFERENCES issues(id),
            UNIQUE(issue_id, model, prompt_version, temperature)
        )""")
        
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS versions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL, 
            package_name = TEXT NOT NULL, 
            version TEXT NOT NULL,
            published_at TEXT, 
            direct_count INTEGER, 
            dev_count INTEGER,
            transitive_count INTEGER,
            transitive_truncated INTEGER,
            raw_manifest TEXT,
            snapshotted_at TEXT,
            FOREIGN KEY (project_id) REFERENCES projects(id),
            UNIQUE(project_id, version)
            
        )""")
        
        self.cursor.execute("""CREATE TABLE IF NOT EXISTS version_dependencies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            version_id INTEGER NOT NULL,
            dep_name TEXT NOT NULL,
            dep_kind TEXT NOT NULL CHECK(dep_kind IN ('direct','peer','dev','transitive')),
            dep_version_range TEXT,
            depth INTEGER,
            FOREIGN KEY (version_id) REFERENCES versions(id)
        )""")

        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_version ON version_dependencies(version_id)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_name ON version_dependencies(dep_name)")
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_vdeps_kind ON version_dependencies(dep_kind)")
        
        for alter in [
            "ALTER TABLE projects ADD COLUMN package_name TEXT",
            "ALTER TABLE issues ADD COLUMN version_id INTEGER REFERENCES versions(id)",
        ]:
            try:
                self.cursor.execute(alter)
            except sqlite3.OperationalError:
                pass 
        
        self.connection.commit()
        
    def save_project(self, owner, repo):
       
        self.cursor.execute("INSERT OR IGNORE INTO projects(owner, repo, added_at ) VALUES(?,?,?)", (owner, repo, datetime.now().isoformat()))
        
        self.connection.commit()
        
        self.cursor.execute(
            "SELECT id FROM projects WHERE owner = ? AND repo = ?", (owner, repo)
        )
        return self.cursor.fetchone()[0]
    
    def save_issue(self, project_id, issue_data):
        issue_number = issue_data.get("number")
        github_id = issue_data.get("id")
        url = issue_data.get("url")
        title = issue_data.get("title")
        body = issue_data.get("body")
        state= issue_data.get("state") 
        state_reason= issue_data.get("state_reason")  
        locked= issue_data.get("locked")  
        created_at =issue_data.get("created_at")  
        closed_at =issue_data.get("closed_at")  
        updated_at =issue_data.get("updated_at")  
        comments_count =issue_data.get("comments_count")  
        raw_data = json.dumps({
            "timestamp_metrics": issue_data.get("timestamp_metrics"),
            "participant_metrics": issue_data.get("participant_metrics"),
            "reopen_metrics": issue_data.get("reopen_metrics"),
            "author": issue_data.get("author"),
            "closed_by": issue_data.get("closed_by"),
            "labels": issue_data.get("labels"),
            "assignees": issue_data.get("assignees"),
            "milestone": issue_data.get("milestone"),
            "comments": issue_data.get("comments"),
            "comments_md": issue_data.get("comments_md"),
            "comments_text": issue_data.get("comments_text"),
            "maintainer_comments": issue_data.get("maintainer_comments"),
            "closing_pr": issue_data.get("closing_pr"),
            "closing_commit": issue_data.get("closing_commit"),
        })
        
        self.cursor.execute("INSERT OR IGNORE INTO issues(project_id, issue_number, github_id, url, title, body, state, state_reason, locked, created_at, closed_at, updated_at, comments_count, raw_data) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ", (project_id, issue_number, github_id, url, title, body, state, state_reason, locked, created_at, closed_at, updated_at, comments_count, raw_data))
        self.connection.commit()
        
        self.cursor.execute("SELECT id FROM issues WHERE project_id = ? AND issue_number = ?",(project_id, issue_number)
        )
        return self.cursor.fetchone()[0]
        
    def save_classification(self, issue_id, classification_data, model, prompt, temp, classifiedat):
        classification = classification_data.get("classification")
        probabilites = json.dumps(classification_data.get("classification_probabilities"))
        classification_raw = classification_data.get("classification_raw_response")
        
        self.cursor.execute("INSERT OR REPLACE INTO classifications(issue_id, classification, classification_probabilities, classification_raw_response, model, prompt_version, temperature, classified_at) VALUES(?,?,?,?,?, ?, ? ,? )", (issue_id, classification, probabilites, classification_raw, model, prompt, temp, classifiedat))
        
        self.connection.commit()
        
        self.cursor.execute("SELECT id FROM classifications WHERE issue_id = ?", (issue_id,))
        return self.cursor.fetchone()[0]
    
    def get_stats(self):
        self.cursor.execute("SELECT classification, COUNT(id) AS [Number of Classifications] FROM classifications GROUP BY classification")
        return self.cursor.fetchall()
    
    def setPackageName(self, project_id, package_name):
        self.cursor.execute("UPDATE projects SET package_name = ? WHERE id = ?", (package_name, project_id),)
        self.connection.commit()
        
    def getPackageName(self, project_id):
        self.cursor.execute("SELECT package_name FROM projects WHERE id = ?", (project_id,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def projectHasClassifications(self, project_id):
        self.cursor.execute("SELECT 1 from classifications c INNER JOIN issues i on i.id = ci.issue_id WHERE i.project_id = ? LIMIT 1", (project_id,),)
        return self.cursor.fetchone() is not None
        