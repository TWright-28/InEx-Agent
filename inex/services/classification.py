import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

import requests
from tenacity import (
    Retrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from inex.errors import InexError
from inex.services.profiles import (
    build_schema,
    count_word,
    match_label,
    match_probabilities,
)

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_BASE = "http://localhost:11434"
CONTEXT_WINDOW_TOKENS = 30000
MAX_OUTPUT_TOKENS = 25000
MODEL_TIMEOUT_SECONDS = 600
CLOUD_SDK_RETRIES = 3
PROB_SUM_TOLERANCE = 0.02
PROMPT_HASH_CHARS = 12
RETRY_WAIT_INITIAL_SECONDS = 4
RETRY_WAIT_MAX_SECONDS = 120
RETRY_WAIT_JITTER_SECONDS = 4
MAX_PR_BODY_CHARS = 15000
MAX_COMMIT_MESSAGE_CHARS = 2000
MAX_MAINTAINER_COMMENT_CHARS = 20000
MAX_OTHER_COMMENT_CHARS = 8000
MAX_OTHER_COMMENTS = 50
MAX_BODY_CHARS = 80000
MAX_TITLE_LOG_CHARS = 60
MAX_SAMPLE_TITLE_CHARS = 80
PREVIEW_SAMPLE_SIZE = 5
PROGRESS_EVERY = 25

TEMPLATE_VERSION = "2"


class AttemptFailure(Exception):
    def __init__(self, status, message, raw_text="", retryable=False):
        super().__init__(message)
        self.status = status
        self.raw_text = raw_text
        self.retryable = retryable


@dataclass
class ParsedResult:
    label: str | None = None
    probs: dict = field(default_factory=dict)
    raw_text: str = ""
    status: str = "ok"
    error: str | None = None
    flags: list = field(default_factory=list)
    argmax_label: str | None = None
    attempts: int = 1

    @property
    def ok(self):
        return self.status == "ok"


@dataclass(frozen=True)
class ClassifyPreview:
    owner: str
    repo: str
    would_classify: int
    total_remaining: int
    model: str
    prompt_version: str
    earliest_created: str | None
    latest_created: str | None
    sample: tuple


@dataclass(frozen=True)
class ClassifyResult:
    owner: str
    repo: str
    classified: int
    failed: dict


class ClassificationService:
    def __init__(
        self,
        store,
        profile,
        model,
        temperature,
        *,
        workers=1,
        retries=5,
        ollama_base=DEFAULT_OLLAMA_BASE,
    ):
        self.store = store
        self.ollama_base = ollama_base
        retry_attempts = retries
        self.workers = max(1, int(workers or 1))
        self.model_config = {
            "model_name": model,
            "temperature": temperature,
            "context_window": CONTEXT_WINDOW_TOKENS,
        }
        self.profile = profile
        self.Labels = [label.name for label in profile.labels]
        self.LABEL_MAP = {label.lower(): label for label in self.Labels}
        self.PROB_FIELDS = {label.slug: label.name for label in profile.labels}
        self.response_schema = build_schema(profile)
        self._llm = None
        self._structured = None
        self._clientLock = threading.Lock()
        self.base_prompt = profile.guide_text.strip()
        profile_text = profile.raw_bytes.replace(b"\r\n", b"\n")
        self.prompt_hash = hashlib.sha256(
            profile_text + profile.guide_text.encode("utf-8")
        ).hexdigest()[:PROMPT_HASH_CHARS]
        self.prompt_version = f"{profile.name}@{self.prompt_hash}+t{TEMPLATE_VERSION}"

        self.retry_attempts = retry_attempts
        self.retry_wait = wait_exponential_jitter(
            initial=RETRY_WAIT_INITIAL_SECONDS,
            max=RETRY_WAIT_MAX_SECONDS,
            jitter=RETRY_WAIT_JITTER_SECONDS,
        )

    def _newRetrying(self):
        return Retrying(
            stop=stop_after_attempt(self.retry_attempts),
            wait=self.retry_wait,
            retry=retry_if_exception(self._isRetryable),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )

    CLOUD_PROVIDERS = {"openai", "anthropic", "google_genai", "google_vertexai"}

    @property
    def useStructured(self):
        provider, sep, _ = self.model_config["model_name"].partition(":")
        return bool(sep) and provider in self.CLOUD_PROVIDERS

    def getAuthorInfo(self, obj):
        info = obj.get("author", {})
        username = info.get("username", "Unknown") if info else "Unknown"
        role = info.get("author_association", "NONE") if info else "NONE"
        return username, role

    def extractLabel(self, text):
        return match_label(self.profile, text)

    def extractProbabilities(self, text):
        return match_probabilities(self.profile, text) or None

    @staticmethod
    def _isRetryable(exc):
        if isinstance(exc, AttemptFailure):
            return exc.retryable

        if isinstance(exc, requests.exceptions.Timeout | requests.exceptions.ConnectionError):
            return True
        if isinstance(exc, requests.exceptions.HTTPError):
            resp = getattr(exc, "response", None)
            code = getattr(resp, "status_code", None)
            return code == 429 or (code is not None and code >= 500)

        name = type(exc).__name__
        if name in (
            "TimeoutException",
            "ConnectTimeout",
            "ReadTimeout",
            "TransportError",
            "ConnectError",
            "ReadError",
            "RemoteProtocolError",
        ):
            return True
        if name == "ServerError":
            return True
        if name in ("GoogleContextOverflowError", "ContextOverflowError"):
            return False
        if name in ("ChatGoogleGenerativeAIError", "GoogleGenerativeAIError", "ClientError"):
            code = getattr(getattr(exc, "__cause__", None), "code", None)
            if code in (408, 429):
                return True
            text = str(exc)
            return "429" in text or "RESOURCE_EXHAUSTED" in text
        return False

    def callModel(self, prompt):
        attempts = {"n": 0}

        def attempt():
            attempts["n"] += 1
            if self.useStructured:
                return self.callCloud(prompt)
            return self.callOllama(prompt)

        try:
            result = self._newRetrying()(attempt)
        except AttemptFailure as e:
            result = ParsedResult(raw_text=e.raw_text, status=e.status, error=str(e))
        except Exception as e:
            e.classify_attempts = attempts["n"]
            raise
        result.attempts = attempts["n"]
        return result

    def _cloudStructured(self):
        if self._structured is not None:
            return self._structured
        with self._clientLock:
            if self._structured is None:
                from langchain.chat_models import init_chat_model

                self._llm = init_chat_model(
                    self.model_config["model_name"],
                    temperature=self.model_config["temperature"],
                    max_retries=CLOUD_SDK_RETRIES,
                    timeout=MODEL_TIMEOUT_SECONDS,
                )
                self._structured = self._llm.with_structured_output(
                    self.response_schema, method="json_schema", include_raw=True
                )
        return self._structured

    def callCloud(self, prompt):
        out = self._cloudStructured().invoke(prompt)
        raw = out.get("raw")
        parsed = out.get("parsed")
        parse_error = out.get("parsing_error")

        raw_text = getattr(raw, "content", "") or ""
        if not isinstance(raw_text, str):
            raw_text = json.dumps(raw_text, default=str)

        finish = (getattr(raw, "response_metadata", None) or {}).get("finish_reason")

        if parsed is None:
            if finish in ("MAX_TOKENS", "SAFETY", "RECITATION", "PROHIBITED_CONTENT"):
                raise AttemptFailure(
                    "truncated" if finish == "MAX_TOKENS" else "api_error",
                    f"model stopped early (finish_reason={finish})",
                    raw_text=raw_text,
                    retryable=False,
                )
            if not raw_text.strip():
                raise AttemptFailure(
                    "empty_response",
                    "model returned an empty response",
                    raw_text=raw_text,
                    retryable=True,
                )
            raise AttemptFailure(
                "parse_error",
                f"structured output failed: {parse_error}",
                raw_text=raw_text,
                retryable=True,
            )

        probs = {
            canon: getattr(parsed.probabilities, fieldName)
            for fieldName, canon in self.PROB_FIELDS.items()
        }
        return ParsedResult(
            label=parsed.final_answer, probs=probs, raw_text=raw_text or parsed.model_dump_json()
        )

    def callOllama(self, prompt):
        payload = {
            "model": self.model_config["model_name"],
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.model_config["temperature"],
                "num_predict": MAX_OUTPUT_TOKENS,
                "num_ctx": self.model_config["context_window"],
            },
        }

        response = requests.post(
            f"{self.ollama_base}/api/generate", json=payload, timeout=MODEL_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        responseText = response.json().get("response", "")

        if not (responseText or "").strip():
            raise AttemptFailure(
                "empty_response",
                "model returned an empty response",
                raw_text=responseText or "",
                retryable=True,
            )

        label = self.extractLabel(responseText)
        if label is None:
            raise AttemptFailure(
                "parse_error",
                "no parsable '**Final Answer:**' in response",
                raw_text=responseText,
                retryable=True,
            )

        return ParsedResult(
            label=label, probs=self.extractProbabilities(responseText) or {}, raw_text=responseText
        )

    def validateResult(self, result):
        if result.label not in self.Labels:
            result.status, result.error = "invalid_result", f"unknown label {result.label!r}"
            result.label = None
            return result

        probs = result.probs or {}
        missing = set(self.Labels) - set(probs)
        if missing:
            result.status = "invalid_result"
            result.error = f"probabilities missing {sorted(missing)}"
            result.label = None
            return result

        bad = {
            k: v
            for k, v in probs.items()
            if not isinstance(v, int | float) or not 0.0 <= float(v) <= 1.0
        }
        if bad:
            result.status, result.error = "invalid_result", f"probabilities out of range: {bad}"
            result.label = None
            return result

        if abs(sum(probs.values()) - 1.0) > PROB_SUM_TOLERANCE:
            result.flags.append("prob_sum_off")

        top = max(probs.values())
        winners = [k for k, v in probs.items() if v == top]
        if len(winners) > 1:
            result.flags.append("argmax_tie")
        elif winners[0] != result.label:
            result.flags.append("argmax_mismatch")
            result.argmax_label = winners[0]

        return result

    def formatComment(self, i, comment, maxLen, alwaysShowRole):
        commentUser, commentRole = self.getAuthorInfo(comment)
        commentBody = comment.get("body") or ""
        commentDate = comment.get("created_at", "")

        if len(commentBody) > maxLen:
            commentBody = commentBody[:maxLen] + "\n[...truncated...]"

        roleIndicator = (
            f" [{commentRole}]" if (alwaysShowRole or commentRole == "CONTRIBUTOR") else ""
        )
        return f"**Comment {i}** by {commentUser}{roleIndicator} ({commentDate}):\n{commentBody}"

    def formatIssueData(self, issue):
        project = f"{issue['owner']}/{issue['repo']}"
        title = issue.get("title") or "No title"
        body = issue.get("body") or "No description provided"

        authorName, authorRole = self.getAuthorInfo(issue)

        labels = issue.get("labels", [])
        if labels:
            labelNames = [label.get("name", "") for label in labels if label.get("name")]
            labelsStr = ", ".join(labelNames) if labelNames else "None"
        else:
            labelsStr = "None"

        createdAt = issue.get("created_at", "Unknown")
        closedAt = issue.get("closed_at", "Not closed")
        stateReason = issue.get("state_reason") or "Not specified"

        closedByInfo = issue.get("closed_by", {})
        closedBy = closedByInfo.get("username", "Unknown") if closedByInfo else "Unknown"

        closingPr = issue.get("closing_pr")
        closingCommit = issue.get("closing_commit")

        if closingPr:
            prTitle = closingPr.get("title", "No title")
            prBody = closingPr.get("body", "")
            prFiles = closingPr.get("changed_files", 0)
            prMerged = closingPr.get("merged", False)

            prDetails = f"""
**Closing PR Details:**
- Title: {prTitle}
- Merged: {prMerged}
- Files Changed: {prFiles}
- Body: {prBody[:MAX_PR_BODY_CHARS] if prBody else "No description"}{"..." if prBody and len(prBody) > MAX_PR_BODY_CHARS else ""}
"""
        else:
            prDetails = "\n**Closing PR:** None found\n"

        if closingCommit:
            commitMsg = (
                closingCommit
                if isinstance(closingCommit, str)
                else closingCommit.get("message", "No message")
            )
            commitDetails = f"\n**Closing Commit:** {commitMsg[:MAX_COMMIT_MESSAGE_CHARS]}{'...' if len(commitMsg) > MAX_COMMIT_MESSAGE_CHARS else ''}"
        else:
            commitDetails = "\n**Closing Commit:** None found"

        commentsList = issue.get("comments", [])
        maintainerComments = []
        regularComments = []

        for comment in commentsList:
            _, commentRole = self.getAuthorInfo(comment)
            if commentRole in ["OWNER", "MEMBER", "COLLABORATOR", "CONTRIBUTOR"]:
                maintainerComments.append(comment)
            else:
                regularComments.append(comment)

        formattedComments = []

        for i, comment in enumerate(maintainerComments, 1):
            formattedComments.append(
                self.formatComment(i, comment, MAX_MAINTAINER_COMMENT_CHARS, alwaysShowRole=True)
            )

        for i, comment in enumerate(
            regularComments[:MAX_OTHER_COMMENTS], len(maintainerComments) + 1
        ):
            formattedComments.append(
                self.formatComment(i, comment, MAX_OTHER_COMMENT_CHARS, alwaysShowRole=False)
            )

        totalShown = len(maintainerComments) + min(MAX_OTHER_COMMENTS, len(regularComments))
        if len(commentsList) > totalShown:
            formattedComments.append(
                f"\n[...{len(commentsList) - totalShown} more comments omitted...]"
            )

        commentsStr = "\n\n".join(formattedComments) if formattedComments else "No comments"

        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n[..truncated for length..]"

        header = f"""
## Issue Data to Classify

**Project:** {project}
**Issue Number:** #{issue.get("number", "Unknown")}
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
            task = self._structured_task()
        else:
            task = self._markdown_task()
        return header + task

    def classifyOne(self, issue):
        result = self.callModel(self.base_prompt + "\n" + self.formatIssueData(issue))
        if result.ok:
            result = self.validateResult(result)
        return result

    def _prepareIssue(self, owner, repo, row):
        issue_id, number, title, body, state, state_reason, created_at, closed_at, raw_data_str = (
            row
        )
        raw_data = json.loads(raw_data_str) if raw_data_str else {}

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
        return issue_id, number, issue

    def _classifyAndSave(self, owner, repo, row):
        issue_id, number, issue = self._prepareIssue(owner, repo, row)
        logger.info("Classifying #%s: %s", number, (issue.get("title") or "")[:MAX_TITLE_LOG_CHARS])

        try:
            result = self.classifyOne(issue)
        except Exception as e:
            logger.exception("Classification failed for #%s", number)
            result = ParsedResult(
                status="api_error",
                error=f"{type(e).__name__}: {e}",
                attempts=getattr(e, "classify_attempts", 1),
            )

        if not result.ok:
            logger.warning("Issue #%s recorded as %s: %s", number, result.status, result.error)
        elif result.flags:
            logger.info("Issue #%s flagged %s", number, result.flags)

        try:
            self.store.save_classification(
                issue_id,
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
                model=self.model_config["model_name"],
                prompt=self.prompt_version,
                temp=self.model_config["temperature"],
                classified_at=datetime.now().isoformat(),
            )
        except Exception:
            logger.exception("Could not persist classification for #%s", number)
            return "save_error"

        return result.status

    def _classifyRows(self, owner, repo, rows, workers=None):
        workers = max(1, int(workers if workers is not None else self.workers))
        workers = min(workers, len(rows))
        tally = {}

        def record(status):
            tally[status] = tally.get(status, 0) + 1

        if workers == 1:
            for row in rows:
                record(self._classifyAndSave(owner, repo, row))
            return tally

        if self.useStructured:
            self._cloudStructured()

        logger.info("Classifying %d issues across %d workers", len(rows), workers)
        done = 0
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="classify")
        try:
            futures = [pool.submit(self._classifyAndSave, owner, repo, row) for row in rows]
            for fut in as_completed(futures):
                record(fut.result())
                done += 1
                if done % PROGRESS_EVERY == 0 or done == len(rows):
                    logger.info("  progress %d/%d  %s", done, len(rows), tally)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        return tally

    def _structured_task(self):
        count = count_word(len(self.profile.labels))
        return (
            "Fill every field of the required response schema. Work through the\n"
            "reasoning fields in order before committing to the probabilities and the final answer.\n"
            f"The {count} probabilities must sum to 1.0.\n"
            "\n"
            "DO NOT REPEAT RAW FILE PATHS OR FULL STACK TRACE OUTPUTS.\n"
        )

    def _markdown_task(self):
        lines = ["You must use this structure in your response:", "", "**Reasoning:**"]
        lines += [f"**{step.title}** - {step.description}" for step in self.profile.reasoning]
        if self.profile.confidence:
            lines.append(f"**Confidence:** {'/'.join(self.profile.confidence)}")
        lines += ["", "**Probability Distribution:**"]
        lines += [f"- {name}: 0.XX" for name in self.Labels]
        lines += [
            "",
            f"**Final Answer:** [One of: {' / '.join(self.Labels)}]",
            "",
            "DO NOT REPEAT RAW FILE PATHS OR FULL STACK TRACE OUTPUTS.",
            "",
        ]
        return "\n".join(lines)

    def _runKey(self):
        return (
            self.model_config["model_name"],
            self.prompt_version,
            self.model_config["temperature"],
        )

    def _project_id(self, owner, repo):
        project_id = self.store.project_id(owner, repo)
        if project_id is None:
            raise InexError(
                "project_not_found",
                f"{owner}/{repo} is not in this database. Collect it first.",
                owner=owner,
                repo=repo,
            )
        return project_id

    def _result(self, owner, repo, tally):
        failed = {status: n for status, n in sorted(tally.items()) if status != "ok"}
        return ClassifyResult(owner, repo, classified=tally.get("ok", 0), failed=failed)

    def classify(self, owner, repo, *, count=None, start=None, end=None, order="desc"):
        logger.info("Starting classification for %s/%s", owner, repo)
        project_id = self._project_id(owner, repo)
        model, prompt_version, temp = self._runKey()
        rows = self.store.unclassified_in_window(
            project_id, model, prompt_version, temp, start, end, order, count
        )
        tally = self._classifyRows(owner, repo, rows) if rows else {}
        logger.info("Finished classification for %s/%s: %s", owner, repo, tally)
        return self._result(owner, repo, tally)

    def classify_ids(self, owner, repo, issue_ids):
        logger.info(
            "Starting classification of %d specific issues for %s/%s", len(issue_ids), owner, repo
        )
        rows = self.store.unclassified_by_ids(issue_ids, *self._runKey())
        tally = self._classifyRows(owner, repo, rows) if rows else {}
        logger.info("Finished classification for %s/%s: %s", owner, repo, tally)
        return self._result(owner, repo, tally)

    def preview(self, owner, repo, *, count=None, start=None, end=None, order="desc"):
        project_id = self._project_id(owner, repo)
        model, prompt_version, temp = self._runKey()
        total_remaining = self.store.count_unclassified(
            project_id, model, prompt_version, temp, start, end
        )
        rows = self.store.unclassified_in_window(
            project_id, model, prompt_version, temp, start, end, order, count
        )
        dates = [r[6] for r in rows if r[6]]
        sample = tuple(
            {"issue_number": r[1], "title": (r[2] or "")[:MAX_SAMPLE_TITLE_CHARS]}
            for r in rows[:PREVIEW_SAMPLE_SIZE]
        )
        return ClassifyPreview(
            owner=owner,
            repo=repo,
            would_classify=len(rows),
            total_remaining=total_remaining,
            model=model,
            prompt_version=prompt_version,
            earliest_created=min(dates) if dates else None,
            latest_created=max(dates) if dates else None,
            sample=sample,
        )
