import hashlib
import json, re
import logging
import requests
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field
from tenacity import (Retrying, before_sleep_log, retry_if_exception,
                      stop_after_attempt, wait_exponential_jitter)

logger = logging.getLogger(__name__)

# Bump whenever formatIssueData's task template changes. It is folded into
# prompt_version because the prompt actually sent is the guide file PLUS this
# template -- hashing only the file would let the template drift silently.
TEMPLATE_VERSION = "2"


class _Probabilities(BaseModel):
    intrinsic: float = Field(ge=0.0, le=1.0)
    extrinsic: float = Field(ge=0.0, le=1.0)
    not_a_bug: float = Field(ge=0.0, le=1.0)
    unknown: float = Field(ge=0.0, le=1.0)


class ClassificationResult(BaseModel):
    """Field order is load-bearing: Gemini emits properties in schema order, so
    the reasoning fields must precede the probabilities and the final answer."""
    definition_check: str
    maintainer_signal: str
    pr_commit_evidence: str
    temporal_signals: str
    contract_rule: str
    information_completeness: str
    decision: str
    confidence: Literal["High", "Medium", "Low"]
    probabilities: _Probabilities
    final_answer: Literal["Intrinsic", "Extrinsic", "Not a Bug", "Unknown"]


class AttemptFailure(Exception):
    """A classification attempt that produced no usable label.

    Carries the status that should be recorded if retries are exhausted, so a
    failure is never collapsed into the 'Unknown' taxonomy label.
    """

    def __init__(self, status, message, raw_text="", retryable=False):
        super().__init__(message)
        self.status = status
        self.raw_text = raw_text
        self.retryable = retryable


@dataclass
class ParsedResult:
    label: Optional[str] = None
    probs: dict = field(default_factory=dict)
    raw_text: str = ""
    status: str = "ok"
    error: Optional[str] = None
    flags: list = field(default_factory=list)
    argmax_label: Optional[str] = None
    attempts: int = 1

    @property
    def ok(self):
        return self.status == "ok"


class Classify:

    def __init__(self, model, temperature, promptPath):

        self.model_config = {
                "model_name": model,
                "temperature": temperature,
                "context_window": 30000,
            }
        self.Labels = ["Intrinsic", "Extrinsic", "Not a Bug", "Unknown"]
        self.LABEL_MAP = {l.lower(): l for l in self.Labels}
        # probability field name on the schema -> canonical label
        self.PROB_FIELDS = {"intrinsic": "Intrinsic", "extrinsic": "Extrinsic",
                            "not_a_bug": "Not a Bug", "unknown": "Unknown"}
        self.prompt_path = promptPath
        self._llm = None
        self._structured = None

        with open(promptPath, 'r', encoding='utf-8') as f:
            self.base_prompt = f.read().strip()

        # Identify the prompt by content, not by path: the path is constant across
        # every revision, so rows from different prompt generations were previously
        # indistinguishable and collided on the UNIQUE key.
        self.prompt_hash = hashlib.sha256(self.base_prompt.encode("utf-8")).hexdigest()[:12]
        self.prompt_version = f"{self.prompt_path}@{self.prompt_hash}+t{TEMPLATE_VERSION}"

        self._retrying = Retrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=2, max=60, jitter=2),
            retry=retry_if_exception(self._isRetryable),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    # Cloud providers routed through langchain's init_chat_model; anything else
    # (e.g. "qwen3:30b") is treated as a local Ollama tag.
    CLOUD_PROVIDERS = {"openai", "anthropic", "google_genai", "google_vertexai"}

    @property
    def useStructured(self):
        provider, sep, _ = self.model_config["model_name"].partition(":")
        return bool(sep) and provider in self.CLOUD_PROVIDERS

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

    # ---------------- retry policy ----------------

    @staticmethod
    def _isRetryable(exc):
        """Transient faults only. Auth, schema and context-overflow errors fail fast.

        langchain-google-genai 4.2.3 sits on the google-genai SDK, so a 429 arrives
        as ChatGoogleGenerativeAIError with a google.genai.errors.ClientError as
        __cause__ -- google.api_core.exceptions.ResourceExhausted is never raised.
        """
        if isinstance(exc, AttemptFailure):
            return exc.retryable

        if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", None)
            return code == 429 or (code is not None and code >= 500)

        name = type(exc).__name__
        if name in ("TimeoutException", "ConnectTimeout", "ReadTimeout", "TransportError",
                    "ConnectError", "ReadError", "RemoteProtocolError"):
            return True
        # google.genai.errors.ServerError propagates uncaught by langchain.
        if name == "ServerError":
            return True
        # Context overflow is a subclass of ClientError -- never worth retrying.
        if name in ("GoogleContextOverflowError", "ContextOverflowError"):
            return False
        if name in ("ChatGoogleGenerativeAIError", "GoogleGenerativeAIError", "ClientError"):
            code = getattr(getattr(exc, "__cause__", None), "code", None)
            if code in (408, 429):
                return True
            text = str(exc)
            return "429" in text or "RESOURCE_EXHAUSTED" in text
        return False

    # ---------------- model calls ----------------

    def callModel(self, prompt):
        """Run one attempt (with retries) and normalise both paths to ParsedResult."""
        attempts = {"n": 0}

        def attempt():
            attempts["n"] += 1
            if self.useStructured:
                return self.callCloud(prompt)
            return self.callOllama(prompt)

        try:
            result = self._retrying(attempt)
        except AttemptFailure as e:
            result = ParsedResult(raw_text=e.raw_text, status=e.status, error=str(e))
        result.attempts = attempts["n"]
        return result

    def _cloudStructured(self):
        if self._structured is None:
            from langchain.chat_models import init_chat_model
            self._llm = init_chat_model(
                self.model_config["model_name"],
                temperature=self.model_config["temperature"],
                max_retries=3,   # 0 would mean "use the SDK default of 5"
                timeout=600,     # otherwise httpx runs with timeout=None
            )
            # json_schema is the default and the only native mode in 4.2.3;
            # json_mode is a deprecated alias and function_calling is discouraged.
            # include_raw keeps the raw response for auditing and turns a parse
            # failure into a value rather than an exception.
            self._structured = self._llm.with_structured_output(
                ClassificationResult, method="json_schema", include_raw=True)
        return self._structured

    def callCloud(self, prompt):
        out = self._cloudStructured().invoke(prompt)
        raw = out.get("raw")
        parsed = out.get("parsed")
        parse_error = out.get("parsing_error")

        raw_text = getattr(raw, "content", "") or ""
        if not isinstance(raw_text, str):
            # Only happens if include_thoughts is ever enabled, which turns content
            # into a list of blocks. Keep the column a string regardless.
            raw_text = json.dumps(raw_text, default=str)

        finish = (getattr(raw, "response_metadata", None) or {}).get("finish_reason")

        if parsed is None:
            if finish in ("MAX_TOKENS", "SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                raise AttemptFailure("truncated" if finish == "MAX_TOKENS" else "api_error",
                                     f"model stopped early (finish_reason={finish})",
                                     raw_text=raw_text, retryable=False)
            if not raw_text.strip():
                raise AttemptFailure("empty_response", "model returned an empty response",
                                     raw_text=raw_text, retryable=True)
            raise AttemptFailure("parse_error", f"structured output failed: {parse_error}",
                                 raw_text=raw_text, retryable=True)

        probs = {canon: getattr(parsed.probabilities, fieldName)
                 for fieldName, canon in self.PROB_FIELDS.items()}
        return ParsedResult(label=parsed.final_answer, probs=probs,
                            raw_text=raw_text or parsed.model_dump_json())

    def callOllama(self, prompt):
        payload = {
            "model": self.model_config["model_name"],
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.model_config["temperature"],
                "num_predict": 25000,
                "num_ctx": self.model_config["context_window"],
            }
        }

        response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=600)
        response.raise_for_status()
        responseText = response.json().get("response", "")

        if not (responseText or "").strip():
            raise AttemptFailure("empty_response", "model returned an empty response",
                                 raw_text=responseText or "", retryable=True)

        label = self.extractLabel(responseText)
        if label is None:
            raise AttemptFailure("parse_error", "no parsable '**Final Answer:**' in response",
                                 raw_text=responseText, retryable=True)

        return ParsedResult(label=label, probs=self.extractProbabilities(responseText) or {},
                            raw_text=responseText)

    # ---------------- validation ----------------

    def validateResult(self, result):
        """Check a parsed result before it is persisted.

        Returns the result, mutated with flags / argmax_label, and with status set
        to a fatal code if the result cannot be trusted as a label.
        """
        if result.label not in self.Labels:
            result.status, result.error = "invalid_result", f"unknown label {result.label!r}"
            result.label = None
            return result

        probs = result.probs or {}
        missing = set(self.Labels) - set(probs)
        if missing:
            # extractProbabilities returns a dict if even ONE label matched, which
            # is how single-key dicts were previously accepted as valid.
            result.status = "invalid_result"
            result.error = f"probabilities missing {sorted(missing)}"
            result.label = None
            return result

        bad = {k: v for k, v in probs.items()
               if not isinstance(v, (int, float)) or not 0.0 <= float(v) <= 1.0}
        if bad:
            result.status, result.error = "invalid_result", f"probabilities out of range: {bad}"
            result.label = None
            return result

        if abs(sum(probs.values()) - 1.0) > 0.02:
            result.flags.append("prob_sum_off")

        top = max(probs.values())
        winners = [k for k, v in probs.items() if v == top]
        if len(winners) > 1:
            result.flags.append("argmax_tie")
        elif winners[0] != result.label:
            # Record the disagreement; never override what the model actually said.
            result.flags.append("argmax_mismatch")
            result.argmax_label = winners[0]

        return result

    # ---------------- prompt construction ----------------

    def formatComment(self, i, comment, maxLen, alwaysShowRole):
        commentUser, commentRole = self.getAuthorInfo(comment)
        commentBody = comment.get('body') or ''
        commentDate = comment.get('created_at', '')

        if len(commentBody) > maxLen:
            commentBody = commentBody[:maxLen] + "\n[...truncated...]"

        roleIndicator = f' [{commentRole}]' if (alwaysShowRole or commentRole == 'CONTRIBUTOR') else ''
        return f"**Comment {i}** by {commentUser}{roleIndicator} ({commentDate}):\n{commentBody}"

    def formatIssueData(self, issue):
        project = f"{issue['owner']}/{issue['repo']}"
        # `or` rather than a get() default: the key often exists with value None.
        title = issue.get('title') or 'No title'
        body = issue.get('body') or 'No description provided'

        authorName, authorRole = self.getAuthorInfo(issue)

        labels = issue.get('labels', [])
        if labels:
            labelNames = [l.get('name', '') for l in labels if l.get('name')]
            labelsStr = ', '.join(labelNames) if labelNames else 'None'
        else:
            labelsStr = 'None'

        createdAt = issue.get('created_at', 'Unknown')
        closedAt = issue.get('closed_at', 'Not closed')
        stateReason = issue.get('state_reason') or 'Not specified'

        closedByInfo = issue.get('closed_by', {})
        closedBy = closedByInfo.get('username', 'Unknown') if closedByInfo else 'Unknown'

        closingPr = issue.get('closing_pr')
        closingCommit = issue.get('closing_commit')

        # State absence explicitly: the guide leans heavily on PR evidence, and an
        # empty string left the model unable to tell "no PR" from "data missing".
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
        else:
            prDetails = "\n**Closing PR:** None found\n"

        if closingCommit:
            commitMsg = closingCommit if isinstance(closingCommit, str) else closingCommit.get('message', 'No message')
            commitDetails = f"\n**Closing Commit:** {commitMsg[:2000]}{'...' if len(commitMsg) > 2000 else ''}"
        else:
            commitDetails = "\n**Closing Commit:** None found"

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

        header = f"""
## Issue Data to Classify

**Project:** {project}
**Issue Number:** #{issue.get('number', 'Unknown')}
**Title:** {title}
**Author:** {authorName} (Role: {authorRole})
**Labels:** {labelsStr}
**Created:** {createdAt}
**Closed:** {closedAt}
**Closed By:** {closedBy}
**Closed Reason:** {stateReason}
**Issue Body:**
{body}
{prDetails}{commitDetails}
**Comments & Discussion:**
{commentsStr}
---
## Your Task

Analyze this issue using the classification guide and reasoning framework provided above.
"""

        if self.useStructured:
            # The response schema fixes the shape, so asking for markdown headings
            # here would contradict the bound response_mime_type.
            task = """Fill every field of the required response schema. Work through the
reasoning fields in order before committing to the probabilities and the final answer.
The four probabilities must sum to 1.0.

DO NOT REPEAT RAW FILE PATHS OR FULL STACK TRACE OUTPUTS.
"""
        else:
            task = """You must use this structure in your response:

**Reasoning:**
**Definition Check** - Identify which category definitions match the signals
**Maintainer Signal** - Note any authoritative maintainer comments about root cause
**PR/Commit Evidence** - If closing PR exists, describe what it changed and why
**Temporal Signals** - Check for version changes, upgrades, "works on X fails on Y" patterns
**Contract Rule** - If API usage involved, determine who violated the contract
**Information Completeness** - Assess if there's enough detail to classify confidently
**Decision** - State your classification and the primary evidence supporting it
**Confidence:** High/Medium/Low

**Probability Distribution:**
- Intrinsic: 0.XX
- Extrinsic: 0.XX
- Not a Bug: 0.XX
- Unknown: 0.XX

**Final Answer:** [One of: Intrinsic / Extrinsic / Not a Bug / Unknown]

DO NOT REPEAT RAW FILE PATHS OR FULL STACK TRACE OUTPUTS.
"""
        return header + task

    # ---------------- classification ----------------

    def classifyOne(self, issue):
        result = self.callModel(self.base_prompt + "\n" + self.formatIssueData(issue))
        if result.ok:
            result = self.validateResult(result)
        return result

    def _classifyRows(self, owner, repo, rows, db):
        tally = {}
        for row in rows:
            issue_id, number, title, body, state, state_reason, created_at, closed_at, raw_data_str = row
            raw_data = json.loads(raw_data_str) if raw_data_str else {}

            # raw_data first: splatting it last let it silently override title/body/state.
            issue = {
                **raw_data,
                "owner": owner,
                "repo": repo,
                "number": number,
                "title": title,
                "body": body,
                "state": state,
                "state_reason": state_reason,
                "created_at": created_at,
                "closed_at": closed_at,
            }

            logger.info("Classifying #%d: %s", number, (title or "")[:60])

            try:
                result = self.classifyOne(issue)
            except Exception as e:  # never a bare except: KeyboardInterrupt must pass through
                logger.exception("Classification failed for #%d", number)
                result = ParsedResult(status="api_error", error=f"{type(e).__name__}: {e}")

            if not result.ok:
                logger.warning("Issue #%d recorded as %s: %s", number, result.status, result.error)
            elif result.flags:
                logger.info("Issue #%d flagged %s", number, result.flags)

            tally[result.status] = tally.get(result.status, 0) + 1

            db.save_classification(issue_id,
            {
                "classification": result.label,
                "classification_probabilities": result.probs,
                "classification_raw_response": result.raw_text,
                "status": result.status,
                "error": result.error,
                "validation_flags": result.flags,
                "argmax_label": result.argmax_label,
                "attempts": result.attempts,
            },
                model = self.model_config["model_name"],
                prompt= self.prompt_version,
                temp =self.model_config["temperature"],
                classifiedat= datetime.now().isoformat(),
            )

        return tally

    def _summarise(self, owner, repo, tally):
        ok = tally.get("ok", 0)
        failed = sum(n for status, n in tally.items() if status != "ok")
        msg = f"Classified {ok} issues from {owner}/{repo}."
        if failed:
            breakdown = ", ".join(f"{s}={n}" for s, n in sorted(tally.items()) if s != "ok")
            msg += (f" {failed} failed and were NOT labelled ({breakdown});"
                    " re-run to retry them.")
        return msg

    def _runKey(self):
        return (self.model_config["model_name"], self.prompt_version,
                self.model_config["temperature"])

    def classifyAll(self, owner, repo, db, max_issues = None, start = None, end = None, direction = "desc"):

        logger.info("Starting classification for %s/%s", owner, repo)

        db.cursor.execute("SELECT id from projects where owner = ? and repo = ?", (owner, repo))
        row = db.cursor.fetchone()
        if not row:
            return f"Project {owner}/{repo} not found in database."
        projectId = row[0]

        model, promptVersion, temp = self._runKey()
        unclassified = db.getUnclassifiedInWindow(projectId, model, promptVersion, temp,
                                                  start, end, direction, max_issues)
        if not unclassified:
            return f"No unclassified issues found for {owner}/{repo} in the given window."

        tally = self._classifyRows(owner, repo, unclassified, db)

        logger.info("Finished classification for %s/%s: %s", owner, repo, tally)
        return self._summarise(owner, repo, tally)

    def classifyByIds(self, owner, repo, issue_ids, db):
        logger.info("Starting classification of %d specific issues for %s/%s", len(issue_ids), owner, repo)

        rows = db.getIssuesByIds(issue_ids, *self._runKey())
        if not rows:
            return f"No unclassified issues found among the given ids for {owner}/{repo}."

        tally = self._classifyRows(owner, repo, rows, db)

        logger.info("Finished classification for %s/%s: %s", owner, repo, tally)
        return self._summarise(owner, repo, tally)

    def previewClassification(self, owner, repo, db, max_count=None, start=None, end=None, direction="desc"):
        db.cursor.execute("SELECT id FROM projects WHERE owner=? AND repo=?", (owner, repo))
        row = db.cursor.fetchone()

        if not row:
            return {"status": "error", "code": "project_not_found", "owner": owner, "repo": repo}

        project_id = row[0]
        model, promptVersion, temp = self._runKey()
        # would_classify is capped by max_count; total_remaining is the real backlog.
        total_remaining = db.countUnclassifiedInWindow(project_id, model, promptVersion, temp, start, end)
        rows = db.getUnclassifiedInWindow(project_id, model, promptVersion, temp,
                                          start, end, direction, max_count)
        if not rows:
            return {"status": "ok", "would_classify": 0, "owner": owner, "repo": repo,
                    "total_remaining": total_remaining, "model": model,
                    "prompt_version": promptVersion}

        dates = [r[6] for r in rows if r[6]]
        sample = [{"issue_number": r[1], "title": (r[2] or "")[:80]}
                for r in rows[:5]]
        return {
            "status": "ok",
            "owner": owner, "repo": repo,
            "would_classify": len(rows),
            "total_remaining": total_remaining,
            "model": model,
            "prompt_version": promptVersion,
            "earliest_created": min(dates) if dates else None,
            "latest_created": max(dates) if dates else None,
            "sample": sample,
        }
