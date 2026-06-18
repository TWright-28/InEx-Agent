import csv
import re

ISSUE_REF_RE = re.compile(r"^\s*([^/\s#]+)/([^/\s#]+)#(\d+)\s*$")


def parseIssueListCsv(csv_path, column=None):
    """Parse a CSV with an 'owner/repo#number' column into (owner, repo, number) tuples.

    Returns (entries, unparseable_rows).
    """
    entries = []
    unparseable = []
    with open(csv_path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        col = column or reader.fieldnames[0]
        for row in reader:
            raw = (row.get(col) or "").strip()
            if not raw:
                continue
            m = ISSUE_REF_RE.match(raw)
            if not m:
                unparseable.append(raw)
                continue
            owner, repo, num = m.group(1), m.group(2), int(m.group(3))
            entries.append((owner, repo, num))
    return entries, unparseable
