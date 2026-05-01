import json, re
from pathlib import Path
import requests
import argparse

class Classify:
    
    def __init__(self, model, temperature, promptPath):
    
        self.model_config = {
                "model_name": model,
                "temperature": temperature,
                "context_window": 30000,
            }
        self.Labels = ["Intrinsic", "Extrinsic", "Not a Bug", "Unknown"]
        self.LABEL_MAP = {l.lower(): l for l in self.Labels}
            
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

        return f"""---
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

    def classifyAll(self, owner, repo, db):
        db.cursor.execute("SELECT id from projects where owner = ? and repo = ?", (owner, repo))
        row = db.cursor.fetchone()
        if not row: 
            return f"Project {owner}/{repo} not found in database."
        projectId = row[0]
        
        db.cursor.execute("SELECT i.id, i.issue_number, i.title, i.body, i.state, i.state_reason, i.created_at, i.closed_at, i.raw_data FROM issues i LEFT JOIN classifications c on c.issue_id = i.id where i.project_id = ? and c.id IS NULL", (projectId,))
        unclassified = db.cursor.fetchall()
        if not unclassified:
            return f"No unclassified issues found for {owner}/{repo}."

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

            print(f"  Classifying #{number}: {title[:60]}...")

            response = self.callOllama(self.base_prompt + "\n" + self.formatIssueData(issue), self.model_config)
            label = self.extractLabel(response) or "Unknown"
            probs = self.extractProbabilities(response) or {}

            db.save_classification(issue_id, {
                "classification": label,
                "classification_probabilities": probs,
                "classification_raw_response": response,
                "model": self.model_config["model"],
                "prompt_version": self.base_prompt,
                "temperature": self.model_config["temperature"],
                "classified_at": datetime.now().isoformat(), 
                
            })
            classified += 1

        return f"Classified {classified} issues from {owner}/{repo}."
        

    # parser = argparse.ArgumentParser(description='bug classifier')
    # parser.add_argument('--input', type=str, required=True)
    # parser.add_argument('--prompt', type=str, required=True)
    # parser.add_argument('--model', type=str, default='qwen3:32b')
    # parser.add_argument('--output', type=str, default='classified_issues.jsonl')
    # args = parser.parse_args()


    # modelConfig = self.models[args.model].copy()

    # with open(args.prompt, 'r', encoding='utf-8') as f:
    #     basePrompt = f.read().strip()

    # issues = []
    # with open(args.input, 'r', encoding='utf-8') as f:
    #     for line in f:
    #         if line.strip():
    #             issues.append(json.loads(line))

    # processed = set()
    # if Path(args.output).exists():
    #     with open(args.output, 'r', encoding='utf-8') as f:
    #         for line in f:
    #             if line.strip():
    #                 r = json.loads(line)
    #                 processed.add((r['owner'], r['repo'], r['number']))

    # classifiedCount = 0

    # for i, issue in enumerate(issues, 1):
    #     key = (issue['owner'], issue['repo'], issue.get('number'))

    #     if key in processed:
    #         continue

    #     print(f"[{i}/{len(issues)}] #{key[2]} ({key[0]}/{key[1]}): {issue.get('title', '')[:60]}")


    #     rawResponse = callOllama(basePrompt + "\n" + formatIssueData(issue), modelConfig)
    #     label = extractLabel(rawResponse) or "Unknown"
    #     probs = extractProbabilities(rawResponse) or {}
    #     classifiedCount += 1

    #     issue['classification'] = label
    #     issue['classification_probabilities'] = probs
    #     issue['classification_raw_response'] = rawResponse

    #     with open(args.output, 'a', encoding='utf-8') as f:
    #         f.write(json.dumps(issue, ensure_ascii=False) + '\n')

    #     processed.add(key)
    # print(f"\n classified: {classifiedCount} issues")


