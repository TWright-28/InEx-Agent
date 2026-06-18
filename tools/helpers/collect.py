import json
import time
import requests
from datetime import datetime, timezone
from config import GITHUB_TOKEN
import logging
logger = logging.getLogger(__name__)

class Collect: 
    def __init__(self, token):

        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json"
        }
    
    def countIssues(self, owner, repo):
        url = f"https://api.github.com/search/issues?q=repo:{owner}/{repo}+is:issue&per_page=1"
        data = self.get(url).json()
        return data.get("total_count", 0)
     
        
    def parseTs(self, s):
        if not s:
            return None
        return datetime.fromisoformat(s.replace("Z", "+00:00"))

    def get(self, url, hdrs= None):
        if hdrs is None:
            hdrs = self.headers
        while True:
            r = requests.get(url, headers=hdrs, timeout=30)
            if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
                reset = int(r.headers.get("X-RateLimit-Reset", 0))
                wait = max(0, reset - int(time.time()) + 1)
                logger.warning("Github Rate limit reached, sleeping for %d seconds", wait)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r

    def fetch(self, url):
        return self.get(url, self.headers).json()

    def fetchPaginated(self, url, extra_headers=None):
        hdrs = {**self.headers, **(extra_headers or {})}
        items = []
        page = 1
        while True:
            sep = "&" if "?" in url else "?"
            data = self.get(f"{url}{sep}per_page=100&page={page}", hdrs).json()
            if not data:
                return items
            items.extend(data)
            if len(data) < 100:
                return items
            page += 1

    def fetchPrDetails(self, owner, repo, prNum):
        try:
            pr = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/pulls/{prNum}")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                return None
            raise
        try:
            revs = self.fetchPaginated(f"https://api.github.com/repos/{owner}/{repo}/pulls/{prNum}/reviews")
        except:
            revs = []

        reviewers = set()
        revStates = {"approved": 0, "changes_requested": 0, "commented": 0, "dismissed": 0}

        for rv in revs:
            uname = rv.get("user", {}).get("login")
            if uname:
                reviewers.add(uname)
            st = rv.get("state", "").lower()
            if st in revStates:
                revStates[st] += 1

        prAuthor = pr.get("user") or {}
        mergedBy = pr.get("merged_by") or {}

        return {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "html_url": pr.get("html_url"),
            "merged": bool(pr.get("merged_at")),
            "merged_at": pr.get("merged_at"),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "closed_at": pr.get("closed_at"),
            "state": pr.get("state"),
            "body": pr.get("body"),
            "author": {
                "username": prAuthor.get("login"),
                "id": prAuthor.get("id"),
            },
            "merged_by": {
                "username": mergedBy.get("login"),
                "id": mergedBy.get("id"),
            } if mergedBy else None,
            "commits": pr.get("commits"),
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "total_changes": (pr.get("additions") or 0) + (pr.get("deletions") or 0),
            "changed_files": pr.get("changed_files"),
            "review_comments": pr.get("review_comments"),
            "comments": pr.get("comments"),
            "unique_reviewers": len(reviewers),
            "reviewer_usernames": sorted(list(reviewers)),
            "total_reviews": len(revs),
            "approved_count": revStates["approved"],
            "changes_requested_count": revStates["changes_requested"],
            "commented_count": revStates["commented"],
            "head_ref": pr.get("head", {}).get("ref"),
            "base_ref": pr.get("base", {}).get("ref"),
            "head_sha": pr.get("head", {}).get("sha"),
            "merge_commit_sha": pr.get("merge_commit_sha")
        }

    def fetchCommitDetails(self, owner, repo, sha):
        c = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}")
        stats = c.get("stats", {})
        auth = c.get("commit", {}).get("author", {})
        comm = c.get("commit", {}).get("committer", {})

        return {
            "sha": c.get("sha"),
            "message": c.get("commit", {}).get("message"),
            "html_url": c.get("html_url"),
            "author": {"date": auth.get("date")},
            "committer": {"date": comm.get("date")},
            "github_author": {
                "username": c.get("author", {}).get("login"),
                "id": c.get("author", {}).get("id")
            } if c.get("author") else None,
            "github_committer": {
                "username": c.get("committer", {}).get("login"),
                "id": c.get("committer", {}).get("id")
            } if c.get("committer") else None,
            "additions": stats.get("additions"),
            "deletions": stats.get("deletions"),
            "total_changes": stats.get("total"),
            "changed_files": len(c.get("files", [])),
            "files": [
                {
                    "filename": f.get("filename"),
                    "status": f.get("status"),
                    "additions": f.get("additions"),
                    "deletions": f.get("deletions"),
                    "changes": f.get("changes")
                }
                for f in c.get("files", [])[:20]
            ]
        }

    def calcTimestamps(self, createdAt, closedAt, comments):
        m = {"time_to_close_seconds": None, "time_to_first_response_seconds": None, "time_open_seconds": None}

        if createdAt and closedAt:
            m["time_to_close_seconds"] = int((closedAt - createdAt).total_seconds())

        if createdAt and comments:
            firstComment = min(comments, key=lambda c: c.get("created_at", "9999"))
            fcTime = self.parseTs(firstComment.get("created_at"))
            if fcTime:
                if createdAt.tzinfo and not fcTime.tzinfo:
                    fcTime = fcTime.replace(tzinfo=timezone.utc)
                elif not createdAt.tzinfo and fcTime.tzinfo:
                    createdAt = createdAt.replace(tzinfo=timezone.utc)
                m["time_to_first_response_seconds"] = int((fcTime - createdAt).total_seconds())

        if createdAt:
            endTime = closedAt if closedAt else datetime.now(timezone.utc)
            if not createdAt.tzinfo:
                createdAt = createdAt.replace(tzinfo=timezone.utc)
            m["time_open_seconds"] = int((endTime - createdAt).total_seconds())

        return m

    def calcParticipants(self, authorLogin, comments):
        parts = set()
        maints = set()

        if authorLogin:
            parts.add(authorLogin)

        for c in comments:
            uname = c.get("user", {}).get("login")
            assoc = c.get("author_association", "")
            if uname:
                parts.add(uname)
                if assoc in ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"]:
                    maints.add(uname)

        return {
            "unique_participants": len(parts),
            "unique_maintainers": len(maints),
            "maintainer_involved": len(maints) > 0
        }

    def calcReopenMetrics(self, state, createdAt, closedAt, events):
        m = {
            "was_reopened": False,
            "reopen_count": 0,
            "time_to_reopen_seconds": None,
            "final_resolution_time_seconds": None,
            "reopen_timestamps": []
        }

        closedEvts = [e for e in events if e.get("event") == "closed"]
        reopenedEvts = [e for e in events if e.get("event") == "reopened"]

        if not reopenedEvts:
            return m

        m["was_reopened"] = True
        m["reopen_count"] = len(reopenedEvts)
        m["reopen_timestamps"] = [e.get("created_at") for e in reopenedEvts]

        if closedEvts and reopenedEvts:
            firstClose = min(closedEvts, key=lambda e: e.get("created_at", ""))
            firstReopen = min(reopenedEvts, key=lambda e: e.get("created_at", ""))
            fcTime = self.parseTs(firstClose.get("created_at"))
            frTime = self.parseTs(firstReopen.get("created_at"))
            if fcTime and frTime:
                m["time_to_reopen_seconds"] = int((frTime - fcTime).total_seconds())

        if state == "closed" and createdAt and closedAt:
            m["final_resolution_time_seconds"] = int((closedAt - createdAt).total_seconds())

        return m

    def tryPr(self, owner, repo, prNum, createdTime, closedTime, window):
        prM = self.fetchPrDetails(owner, repo, prNum)
        if not prM or not prM.get("merged"):
            return None
        prMergedTime = self.parseTs(prM.get("merged_at"))
        if not prMergedTime or not createdTime or not closedTime:
            return None
        if prMergedTime < createdTime:
            return None
        if abs((prMergedTime - closedTime).total_seconds()) > window:
            return None
        return prM

    def findClosingMethod(self, owner, repo, num, createdAt, closedAt, events):
        if not closedAt:
            return None, None

        issueCreatedTime = self.parseTs(createdAt)
        issueClosedTime = self.parseTs(closedAt)

        closedEvts = [e for e in events if e.get("event") == "closed"]
        if not closedEvts:
            return None, None

        closedEvt = max(closedEvts, key=lambda e: e.get("created_at", ""))

        src = closedEvt.get("source", {})
        if src.get("type") == "issue":
            prNum = src.get("issue", {}).get("number")
            if prNum:
                prM = self.tryPr(owner, repo, prNum, issueCreatedTime, issueClosedTime, 86400)
                if prM:
                    return prM, None

        crossRefs = [e for e in events if e.get("event") == "cross-referenced"]
        for ref in sorted(crossRefs, key=lambda e: e.get("created_at", ""), reverse=True):
            refSrc = ref.get("source", {})
            if refSrc.get("type") == "issue":
                prNum = refSrc.get("issue", {}).get("number")
                if prNum:
                    prM = self.tryPr(owner, repo, prNum, issueCreatedTime, issueClosedTime, 604800)
                    if prM:
                        return prM, None

        refEvts = [e for e in events if e.get("event") == "referenced"]
        for ref in sorted(refEvts, key=lambda e: e.get("created_at", ""), reverse=True):
            refSrc = ref.get("source", {})
            if refSrc.get("type") == "issue":
                prNum = refSrc.get("issue", {}).get("number")
                if prNum:
                    prM = self.tryPr(owner, repo, prNum, issueCreatedTime, issueClosedTime, 604800)
                    if prM:
                        return prM, None

            commitId = ref.get("commit_id")
            if commitId:
                try:
                    prs = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/commits/{commitId}/pulls")
                    if prs:
                        prM = self.tryPr(owner, repo, prs[0].get("number"), issueCreatedTime, issueClosedTime, 604800)
                        if prM:
                            return prM, None
                except:
                    continue

        commitSha = closedEvt.get("commit_id")
        commitUrl = closedEvt.get("commit_url")

        if not commitSha and commitUrl:
            parts = commitUrl.rstrip('/').split('/')
            if len(parts) > 0:
                commitSha = parts[-1]

        if commitSha:
            try:
                prs = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/commits/{commitSha}/pulls")
                if prs:
                    prM = self.tryPr(owner, repo, prs[0].get("number"), issueCreatedTime, issueClosedTime, 604800)
                    if prM:
                        return prM, None
                else:
                    try:
                        return None, self.fetchCommitDetails(owner, repo, commitSha)
                    except requests.exceptions.HTTPError:
                        pass

            except:
                pass

        return None, None

    def buildComments(self, comments, format_ts=False):
        if not comments:
            return ""
        blocks = []
        for c in sorted(comments, key=lambda x: x.get("created_at", "")):
            ts = c.get("created_at", "Unknown")
            auth = c.get("author", {})
            uname = auth.get("username", "unknown")
            assoc = auth.get("author_association", "NONE")
            body = c.get("body", "")
            if format_ts:
                tsObj = self.parseTs(ts)
                ts = tsObj.strftime("%Y-%m-%d %H:%MZ") if tsObj else "0000-00-00 00:00Z"
            blocks.append(f"[{ts}] [{assoc}] {uname}:\n{body}")
        return "\n\n---\n\n".join(blocks)

    def collectIssue(self, owner, repo, num):
        issue = self.fetch(f"https://api.github.com/repos/{owner}/{repo}/issues/{num}")

        if issue.get("pull_request"):
            return None

        rawComments = self.fetchPaginated(f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/comments")
        timeline = self.fetchPaginated(
            f"https://api.github.com/repos/{owner}/{repo}/issues/{num}/timeline",
            {"Accept": "application/vnd.github.mockingbird-preview+json"}
        )

        commentsData = []
        maintComments = []

        for c in rawComments:
            obj = {
                "id": c.get("id"),
                "created_at": c.get("created_at"),
                "updated_at": c.get("updated_at"),
                "author": {
                    "username": c.get("user", {}).get("login"),
                    "id": c.get("user", {}).get("id"),
                    "author_association": c.get("author_association")
                },
                "body": c.get("body")
            }
            commentsData.append(obj)
            assoc = c.get("author_association", "")
            if assoc in ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"]:
                maintComments.append(obj)

        createdAt = self.parseTs(issue.get("created_at"))
        closedAt = self.parseTs(issue.get("closed_at"))
        authorLogin = issue.get("user", {}).get("login")

        tMetrics = self.calcTimestamps(createdAt, closedAt, rawComments)
        pMetrics = self.calcParticipants(authorLogin, rawComments)
        rMetrics = self.calcReopenMetrics(issue.get("state"), createdAt, closedAt, timeline)

        closingPr = None
        closingCommit = None

        if issue.get("state") == "closed":
            closingPr, closingCommit = self.findClosingMethod(owner, repo, num, issue.get("created_at"), issue.get("closed_at"),timeline)

        commentsMd = self.buildComments(commentsData)
        commentsText = self.buildComments(commentsData, format_ts=True)

        labels = [{"name": l.get("name"), "description": l.get("description"), "color": l.get("color")} for l in issue.get("labels", [])]
        assignees = [{"username": u.get("login"), "id": u.get("id")} for u in issue.get("assignees", [])]

        ms = issue.get("milestone")
        milestone = {"number": ms.get("number"), "title": ms.get("title"), "state": ms.get("state"), "due_on": ms.get("due_on")} if ms else None

        closedBy = issue.get("closed_by")

        return {
            "owner": owner,
            "repo": repo,
            "number": issue.get("number"),
            "id": issue.get("id"),
            "url": issue.get("html_url"),
            "title": issue.get("title"),
            "body": issue.get("body") or "",
            "state": issue.get("state"),
            "state_reason": issue.get("state_reason"),
            "locked": issue.get("locked"),
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "closed_at": issue.get("closed_at"),
            "timestamp_metrics": tMetrics,
            "participant_metrics": pMetrics,
            "reopen_metrics": rMetrics,
            "author": {
                "username": issue.get("user", {}).get("login"),
                "id": issue.get("user", {}).get("id"),
                "author_association": issue.get("author_association")
            },
            "closed_by": {"username": closedBy.get("login"), "id": closedBy.get("id")} if closedBy else None,
            "labels": labels,
            "assignees": assignees,
            "milestone": milestone,
            "comments_count": issue.get("comments"),
            "comments": commentsData,
            "comments_md": commentsMd,
            "comments_text": commentsText,
            "maintainer_comments": maintComments,
            "closing_pr": closingPr,
            "closing_commit": closingCommit,
        }

    def collectSpecific(self, owner, repo, numbers, db):
        logger.info("Collecting %d specific issues for %s/%s", len(numbers), owner, repo)
        projectId = db.save_project(owner, repo)
        collected, skipped, failed = 0, 0, 0
        issueIds = []

        for num in numbers:
            db.cursor.execute("SELECT id FROM issues WHERE project_id = ? AND issue_number = ?", (projectId, num))
            row = db.cursor.fetchone()
            if row:
                issueIds.append(row[0])
                skipped += 1
                continue

            try:
                issueData = self.collectIssue(owner, repo, num)
            except requests.exceptions.HTTPError as e:
                logger.warning("Failed to fetch %s/%s#%d: %s", owner, repo, num, e)
                failed += 1
                continue

            if issueData is None:
                failed += 1
                continue

            issueId = db.save_issue(projectId, issueData)
            issueIds.append(issueId)
            collected += 1

            if collected % 10 == 0:
                logger.info("  Collected %d/%d for %s/%s", collected, len(numbers), owner, repo)

        logger.info("Finished specific collection for %s/%s: %d collected, %d skipped, %d failed",
                     owner, repo, collected, skipped, failed)
        return projectId, issueIds, collected, skipped, failed

    def collectAll(self, owner, repo, db, max_issues=None, start_date=None, end_date=None, direction="desc"):
        logger.info("Starting collection for %s\%s", owner, repo)
        projectId = db.save_project(owner, repo)
        collected = 0
        skipped = 0
        pg = 1

        if direction not in ("desc", "asc"):
                direction = "desc"
        
        while True:
            issuesUrl = f"https://api.github.com/repos/{owner}/{repo}/issues?state=all&sort=created&direction=desc"
            issues = self.get(f"{issuesUrl}&per_page=100&page={pg}").json()

            for issue in issues:
                if issue.get("pull_request"):
                    continue
                
                created = issue.get("created_at")
                
                if direction == "desc":
                    if end_date and created and created > end_date:
                        continue 
                    if start_date and created and created < start_date:
                        logger.info("Reached start_date boundary, stopping.")
                        return self._finish(owner, repo, collected, skipped)
                else:

                    if start_date and created and created < start_date:
                        continue 
                    if end_date and created and created > end_date:
                        logger.info("Reached end_date boundary, stopping.")
                        return self._finish(owner, repo, collected, skipped)

                num = issue.get("number")
                db.cursor.execute("SELECT id FROM issues WHERE project_id = ? AND issue_number = ?", (projectId, num))
                if db.cursor.fetchone():
                    skipped += 1
                    continue

                issueData = self.collectIssue(owner, repo, num)
                if issueData is None:
                    continue

                db.save_issue(projectId, issueData)
                collected += 1

                if collected % 10 == 0:
                    logger.info("  Collected %d issues (skipped %d)",collected, skipped)

                if max_issues and collected >= max_issues:
                    logger.info("Reached max_issues cap.")
                    return self._finish(owner, repo, collected, skipped)

            if len(issues) < 100:
                break
            pg += 1

        return self._finish(owner, repo, collected, skipped)
    
    def _finish(self, owner, repo, collected, skipped):
        logger.info("Finished collection for %s/%s: %d collected, %d skipped",
                    owner, repo, collected, skipped)
        return f"Collected {collected} issues from {owner}/{repo} (skipped {skipped} already in DB)."