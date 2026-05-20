import sqlite3
from config import DB_PATH

#optional, can modify. setting to 50 since we in CLI
MAX_SQL_ROWS = 50

ro_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True,check_same_thread=False)


def describeSchema() -> dict:
    cur = ro_conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {}
    for (name,) in cur.fetchall():
        cur.execute(f"PRAGMA table_info({name})")
        tables[name] = [{"name": c[1], "type": c[2]} for c in cur.fetchall()]
    return {"status": "ok", "tables": tables}


def runSql(query: str) -> dict:
    q = query.strip().rstrip(";").strip()
    low = q.lower()

    if not (low.startswith("select") or low.startswith("with")):
        return {"status": "error", "code": "not_a_select", "message": "Only SELECT queries are allowed."}
    if ";" in q:
        return {"status": "error", "code": "multiple_statements", "message": "Only one statement allowed."}
    if "limit" not in low:
        q = f"{q} LIMIT {MAX_SQL_ROWS}"

    try:
        cur = ro_conn.cursor()
        cur.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_SQL_ROWS)
    except sqlite3.Error as e:
        return {"status": "error", "code": "sql_error", "message": str(e)}

    return {
        "status": "ok",
        "columns": cols,
        "rows": [dict(zip(cols, r)) for r in rows],
        "row_count": len(rows),
        "truncated": len(rows) >= MAX_SQL_ROWS,
    }