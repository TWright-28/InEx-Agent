import csv
import json
import os
from datetime import datetime

from inex.errors import InexError

# optional, can modify. setting to 50 since we in CLI
MAX_SQL_ROWS = 50


def describeSchema(store) -> dict:
    tables = {}
    for (name,) in store.fetch_readonly(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )[1]:
        columns = store.fetch_readonly(f"PRAGMA table_info({name})")[1]
        tables[name] = [{"name": c[1], "type": c[2]} for c in columns]
    return {"status": "ok", "tables": tables}


def runSql(store, query: str) -> dict:
    q = query.strip().rstrip(";").strip()
    low = q.lower()

    if not (low.startswith("select") or low.startswith("with")):
        return {
            "status": "error",
            "code": "not_a_select",
            "message": "Only SELECT queries are allowed.",
        }
    if ";" in q:
        return {
            "status": "error",
            "code": "multiple_statements",
            "message": "Only one statement allowed.",
        }
    if "limit" not in low:
        q = f"{q} LIMIT {MAX_SQL_ROWS}"

    try:
        cols, rows = store.run_readonly(q, MAX_SQL_ROWS)
    except InexError as e:
        return {"status": "error", "code": e.code, "message": e.message}

    lines = [" | ".join(cols)]
    lines.append("-" * len(lines[0]))
    for r in rows:
        lines.append(" | ".join("" if v is None else str(v) for v in r))
    if len(rows) >= MAX_SQL_ROWS:
        lines.append(f"(truncated at {MAX_SQL_ROWS} rows)")
    return "\n".join(lines)


def exportQuery(store, query: str, fmt: str = "csv", filename: str = None) -> dict:
    q = query.strip().rstrip(";").strip()
    low = q.lower()

    if not (low.startswith("select") or low.startswith("with")):
        return {
            "status": "error",
            "code": "not_a_select",
            "message": "Only SELECT queries are allowed.",
        }
    if ";" in q:
        return {
            "status": "error",
            "code": "multiple_statements",
            "message": "Only one statement allowed.",
        }
    if fmt not in ("csv", "json"):
        return {
            "status": "error",
            "code": "invalid_format",
            "message": "format must be 'csv' or 'json'.",
        }
    if filename and (os.path.basename(filename) != filename or "/" in filename or "\\" in filename):
        return {
            "status": "error",
            "code": "invalid_filename",
            "message": "filename must be a plain file name; exports are always written to exports/.",
        }

    try:
        cols, rows = store.fetch_readonly(q)
    except InexError as e:
        return {"status": "error", "code": e.code, "message": e.message}

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
            json.dump([dict(zip(cols, r, strict=False)) for r in rows], f, indent=2)

    return {
        "status": "ok",
        "file": path,
        "rows_exported": len(rows),
        "columns": cols,
        "format": fmt,
    }
