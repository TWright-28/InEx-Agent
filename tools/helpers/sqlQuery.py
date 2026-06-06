import csv
import json
import os
import sqlite3
from datetime import datetime
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

    lines = [" | ".join(cols)]
    lines.append("-" * len(lines[0]))
    for r in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in r))
    if len(rows) >= MAX_SQL_ROWS:
        lines.append(f"(truncated at {MAX_SQL_ROWS} rows)")
    return "\n".join(lines)


def exportQuery(query: str, fmt: str = "csv", filename: str = None) -> dict:
    q = query.strip().rstrip(";").strip()
    low = q.lower()

    if not (low.startswith("select") or low.startswith("with")):
        return {"status": "error", "code": "not_a_select", "message": "Only SELECT queries are allowed."}
    if ";" in q:
        return {"status": "error", "code": "multiple_statements", "message": "Only one statement allowed."}
    if fmt not in ("csv", "json"):
        return {"status": "error", "code": "invalid_format", "message": "format must be 'csv' or 'json'."}

    try:
        cur = ro_conn.cursor()
        cur.execute(q)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchall()
    except sqlite3.Error as e:
        return {"status": "error", "code": "sql_error", "message": str(e)}

    os.makedirs("exports", exist_ok=True)
    if not filename:
        filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{fmt}"
    elif not filename.endswith(f".{fmt}"):
        filename = f"{filename}.{fmt}"
    path = os.path.join("exports", filename)

    if fmt == "csv":
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols)
            writer.writerows(rows)
    else:
        with open(path, "w", encoding="utf-8") as f:
            json.dump([dict(zip(cols, r)) for r in rows], f, indent=2)

    return {
        "status": "ok",
        "file": path,
        "rows_exported": len(rows),
        "columns": cols,
        "format": fmt,
    }