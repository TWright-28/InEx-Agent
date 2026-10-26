"""Issue collection against a fake GitHub.

The fake honours the list endpoint's `direction` and `page` parameters the way
GitHub does, so these tests catch a request that asks for the wrong order.
"""

from urllib.parse import parse_qs, urlparse

import pytest
import requests

from inex.errors import InexError
from inex.services.collection import CollectionService, CollectResult, CountResult

# number -> created_at, deliberately listed out of order
CREATED = {
    3: "2020-03-10T00:00:00Z",
    1: "2020-01-10T00:00:00Z",
    5: "2020-05-10T00:00:00Z",
    2: "2020-02-10T00:00:00Z",
    4: "2020-04-10T00:00:00Z",
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}", response=self)


def fake_issue_list(url, hdrs=None):
    params = parse_qs(urlparse(url).query)
    if params.get("page", ["1"])[0] != "1":
        return FakeResponse([])
    reverse = params["direction"][0] == "desc"
    ordered = sorted(CREATED.items(), key=lambda kv: kv[1], reverse=reverse)
    return FakeResponse(
        [
            {
                "number": n,
                "title": f"issue {n}",
                "state": "closed",
                "created_at": ts,
                "html_url": f"https://github.com/acme/widget/issues/{n}",
            }
            for n, ts in ordered
        ]
    )


def fake_collect_issue(owner, repo, num):
    return {
        "number": num,
        "title": f"issue {num}",
        "body": "body",
        "state": "closed",
        "state_reason": "completed",
        "created_at": CREATED[num],
    }


@pytest.fixture
def collector(monkeypatch, db):
    c = CollectionService(db, "test-token")
    monkeypatch.setattr(c, "get", fake_issue_list)
    monkeypatch.setattr(c, "collectIssue", fake_collect_issue)
    return c


def saved_in_order(db):
    return [r[0] for r in db.fetch_readonly("SELECT issue_number FROM issues ORDER BY id")[1]]


class TestCollectDirection:
    def test_ascending_collects_oldest_first_up_to_end_date(self, collector, db):
        collector.collect("acme", "widget", end="2020-03-31T23:59:59Z", order="asc")
        assert saved_in_order(db) == [1, 2, 3]

    def test_ascending_count_takes_the_oldest_issues(self, collector, db):
        collector.collect("acme", "widget", count=2, order="asc")
        assert saved_in_order(db) == [1, 2]

    def test_descending_stops_at_start_date(self, collector, db):
        collector.collect("acme", "widget", start="2020-03-01T00:00:00Z", order="desc")
        assert saved_in_order(db) == [5, 4, 3]


class TestCollectResult:
    def test_collect_reports_counts_and_skips_on_rerun(self, collector, db):
        assert collector.collect("acme", "widget") == CollectResult(collected=5, skipped=0)
        assert collector.collect("acme", "widget") == CollectResult(collected=0, skipped=5)

    def test_collect_without_a_token_raises(self, db):
        with pytest.raises(InexError) as e:
            CollectionService(db, None).collect("acme", "widget")
        assert e.value.code == "github_token_missing"


class TestCountIssues:
    """GitHub's search API answers 422 for some public repositories (facebook/react
    among them), so the count must not depend on it."""

    @pytest.fixture
    def graphql(self, monkeypatch):
        sent = []

        def install(payload):
            def post(url, headers=None, json=None, timeout=None):
                sent.append({"url": url, "json": json})
                return FakeResponse(payload)

            monkeypatch.setattr("inex.services.collection.requests.post", post)
            return sent

        return install

    @pytest.fixture
    def search_refuses(self, monkeypatch):
        def refuse(self, url, hdrs=None):
            FakeResponse({"message": "Validation Failed"}, status=422).raise_for_status()

        monkeypatch.setattr(CollectionService, "get", refuse)

    def test_count_works_where_search_is_refused(self, graphql, search_refuses, db):
        sent = graphql({"data": {"repository": {"issues": {"totalCount": 14779}}}})
        assert CollectionService(db, "t").count("facebook", "react") == CountResult(
            "facebook", "react", 14779
        )
        assert sent[0]["url"] == "https://api.github.com/graphql"
        assert sent[0]["json"]["variables"] == {"owner": "facebook", "name": "react"}

    def test_missing_repository_raises(self, graphql, search_refuses, db):
        graphql(
            {
                "data": {"repository": None},
                "errors": [
                    {
                        "type": "NOT_FOUND",
                        "path": ["repository"],
                        "message": "Could not resolve to a Repository with the name 'nope/nope'.",
                    }
                ],
            }
        )
        with pytest.raises(InexError) as e:
            CollectionService(db, "t").count("nope", "nope")
        assert e.value.code == "repo_not_found"

    def test_missing_token_raises_before_any_request(self, graphql, db):
        sent = graphql({})
        with pytest.raises(InexError) as e:
            CollectionService(db, None).count("facebook", "react")
        assert e.value.code == "github_token_missing"
        assert sent == []
