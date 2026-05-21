import json, re
from pathlib import Path
import requests
import argparse
from datetime import datetime
# import tools.helpers.database
import logging
logger = logging.getLogger(__name__)


class Classify:
    
    def __init__(self, model, temperature, promptPath):
    
        self.model_config = {
                "model_name": model,
                "temperature": temperature,
                "context_window": 30000,
            }
        self.Labels = ["Intrinsic", "Extrinsic", "Not a Bug", "Unknown"]
        self.LABEL_MAP = {l.lower(): l for l in self.Labels}
        self.prompt_path = promptPath

        with open(promptPath, 'r', encoding='utf-8') as f:
            self.base_prompt = f.read().strip()

    def getAuthorInfo(self, obj):
        info = obj.get('author', {})
        username = info.get('username', 'Unknown') if info else 'Unknown'
        role = info.get('author_association', 'NONE') if info else 'NONE'
        return username, role

    def extractLabel(self, text):
        if not text: return None

        match = re.search(r"\*\*Final\s*Answer:\*\*\s*(intrinsic|extrinsic|not\s*a\s*bug|unknown)", text, re.IGNORECASE)

        if match:
            raw = match.group(1).lower().strip()
            return self.LABEL_MAP.get(raw)
        return None

    def extractProbabilities(self, text):
        if not text: return None
        matches = re.findall(r"-\s*([a-zA-Z\s]+):\s*([0-9]*\.?[0-9]+)", text)

        probs = {self.LABEL_MAP[name.lower()]: float(val)
                for name, val in matches
                if name.lower() in self.LABEL_MAP}

        return probs if probs else None

    def callOllama(self, prompt, modelConfig):
        payload = {
            "model": modelConfig["model_name"],
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": modelConfig["temperature"],
                "num_predict": 25000,
                "num_ctx": modelConfig["context_window"],
            }
        }

        response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=600)
        response.raise_for_status()
        responseText = response.json().get("response", "")

        return responseText

    def formatComment(self, i, comment, maxLen, alwaysShowRole):
        commentUser, commentRole = self.getAuthorInfo(comment)
        commentBody = comment.get('body', '')
        commentDate = comment.get('created_at', '')

        if len(commentBody) > maxLen:
            commentBody = commentBody[:maxLen] + "\n[...truncated...]"

        roleIndicator = f' [{commentRole}]' if (alwaysShowRole or commentRole == 'CONTRIBUTOR') else ''
        return f"**Comment {i}** by {commentUser}{roleIndicator} ({commentDate}):\n{commentBody}"

    def formatIssueData(self, issue):
        project = f"{issue['owner']}/{issue['repo']}"
        title = issue.get('title', 'No title')
        body = issue.get('body', 'No description provided')

        authorName, authorRole = self.getAuthorInfo(issue)

        labels = issue.get('labels', [])
        if labels:
            labelNames = [l.get('name', '') for l in labels if l.get('name')]
            labelsStr = ', '.join(labelNames) if labelNames else 'None'
        else:
            labelsStr = 'None'

        createdAt = issue.get('created_at', 'Unknown')
        closedAt = issue.get('closed_at', 'Not closed')

        closedByInfo = issue.get('closed_by', {})
        closedBy = closedByInfo.get('username', 'Unknown') if closedByInfo else 'Unknown'

        closingPr = issue.get('closing_pr')
        closingCommit = issue.get('closing_commit')

        prDetails = ""
        if closingPr:
            prTitle = closingPr.get('title', 'No title')
            prBody = closingPr.get('body', '')
            prFiles = closingPr.get('changed_files', 0)
            prMerged = closingPr.get('merged', False)

            prDetails = f"""
**Closing PR Details:**
- Title: {prTitle}
- Merged: {prMerged}
- Files Changed: {prFiles}
- Body: {prBody[:15000] if prBody else 'No description'}{'...' if prBody and len(prBody) > 15000 else ''}
"""

        commitDetails = ""
        if closingCommit:
            commitMsg = closingCommit if isinstance(closingCommit, str) else closingCommit.get('message', 'No message')
            commitDetails = f"\n**Closing Commit:** {commitMsg[:2000]}{'...' if len(commitMsg) > 2000 else ''}"

        commentsList = issue.get('comments', [])
        maintainerComments = []
        regularComments = []

        for comment in commentsList:
            _, commentRole = self.getAuthorInfo(comment)
            if commentRole in ['OWNER', 'MEMBER', 'COLLABORATOR', 'CONTRIBUTOR']:
                maintainerComments.append(comment)
            else:
                regularComments.append(comment)

        formattedComments = []

        for i, comment in enumerate(maintainerComments, 1):
            formattedComments.append(self.formatComment(i, comment, 20000, alwaysShowRole=True))

        for i, comment in enumerate(regularComments[:50], len(maintainerComments) + 1):
            formattedComments.append(self.formatComment(i, comment, 8000, alwaysShowRole=False))

        totalShown = len(maintainerComments) + min(50, len(regularComments))
        if len(commentsList) > totalShown:
            formattedComments.append(f"\n[...{len(commentsList) - totalShown} more comments omitted...]")

        commentsStr = '\n\n'.join(formattedComments) if formattedComments else 'No comments'

        if len(body) > 80000:
            body = body[:80000] + "\n\n[..truncated for length..]"

        return f"""
## Issue Data to Classify

**Project:** {project}
**Issue Number:** #{issue.get('number', 'Unknown')}
**Title:** {title}
**Author:** {authorName} (Role: {authorRole})
**Labels:** {labelsStr}
**Created:** {createdAt}
**Closed:** {closedAt}
**Closed By:** {closedBy}
**Issue Body:**
{body}
{prDetails}{commitDetails}
**Comments & Discussion:** 
{commentsStr}
---
## Your Task

Analyze this issue using the classification guide and reasoning framework provided above.
You must use this structure in your response:

**Reasoning:**
**Definition Check** - Identify which category definitions match the signals
**Maintainer Signal** - Note any authoritative maintainer comments about root cause
**PR/Commit Evidence** - If closing PR exists, describe what it changed and why
**Temporal Signals** - Check for version changes, upgrades, "works on X fails on Y" patterns
**Contract Rule** - If API usage involved, determine who violated the contract
**Information Completeness** - Assess if there's enough detail to classify confidently
**Decision** - State your classification and the primary evidence supporting it
**Confidence:** High/Medium/Low
**Final Answer:** [One of: Intrinsic / Extrinsic / Not a Bug / Unknown]

DO NOT REPEAT RAW FILE PATHS OR FULL STACK TRACE OUTPUTS.
"""

    def classifyAll(self, owner, repo, db, max_issues = None, start = None, end = None, direction = "desc"):
        
        logger.info("Starting classification for %s/%s", owner, repo)

        db.cursor.execute("SELECT id from projects where owner = ? and repo = ?", (owner, repo))
        row = db.cursor.fetchone()
        if not row: 
            return f"Project {owner}/{repo} not found in database."
        projectId = row[0]
        
        unclassified = db.getUnclassifiedInWindow(projectId, start, end, direction, max_issues)
        if not unclassified:
            return f"No unclassified issues found for {owner}/{repo} in the given window."

        classified = 0
        for row in unclassified:
            issue_id, number, title, body, state, state_reason, created_at, closed_at, raw_data_str = row
            raw_data = json.loads(raw_data_str) if raw_data_str else {}

            issue = {
                "owner": owner,
                "repo": repo,
                "number": number,
                "title": title,
                "body": body,
                "state": state,
                "created_at": created_at,
                "closed_at": closed_at,
                **raw_data
            }

            logger.info("Classifying #%d: %s", number, title[:60])

            response = self.callOllama(self.base_prompt + "\n" + self.formatIssueData(issue), self.model_config)
            label = self.extractLabel(response) 
            if label is None:
                logger.warning("Unable to classify #%d: %s", number, title[:60])
                label = "Unknown"


            probs = self.extractProbabilities(response)

            if probs is None: 
                logger.warning("Unable to extract probabilities for #%d: %s", number, title[:60])
                probs = {}


            db.save_classification(issue_id, 
            {
                "classification": label,
                "classification_probabilities": probs,
                "classification_raw_response": response,
            }, 
                model = self.model_config["model_name"],
                prompt= self.prompt_path,
                temp =self.model_config["temperature"],
                classifiedat= datetime.now().isoformat(), 
            )
            
            classified += 1
            
        logger.info("Finished classification for %s/%s: %d issues classified", owner, repo, classified)
        return f"Classified {classified} issues from {owner}/{repo}."
        
    def previewClassification(self, owner, repo, db, max_count=None, start=None, end=None, direction="desc"):
        db.cursor.execute("SELECT id FROM projects WHERE owner=? AND repo=?", (owner, repo))
        row = db.cursor.fetchone()
        
        if not row:
            return {"status": "error", "code": "project_not_found", "owner": owner, "repo": repo}
        
        project_id = row[0] 
        rows = db.getUnclassifiedInWindow(project_id, start, end,direction, max_count)
        if not rows:
            return {"status": "ok", "would_classify": 0, "owner": owner, "repo": repo}
        
        dates = [r[6] for r in rows if r[6]]
        sample = [{"issue_number": r[1], "title": (r[2] or "")[:80]}
                for r in rows[:5]]
        return {
            "status": "ok",
            "owner": owner, "repo": repo,
            "would_classify": len(rows),
            "earliest_created": min(dates) if dates else None,
            "latest_created": max(dates) if dates else None,
            "sample": sample,
        }