from dataclasses import replace

from inex.app import build_app
from inex.errors import InexError
from inex.services.classification import ClassifyPreview, ClassifyResult
from inex.services.collection import CollectResult, CountResult, SpecificResult
from inex.services.linkage import LinkResult


class FakeCollection:
    def __init__(self, count=14779, error=None):
        self.calls = []
        self.issue_count = count
        self.error = error

    def count(self, owner, repo):
        self.calls.append(("count", owner, repo))
        if (owner, repo) == ("nope", "nope"):
            raise InexError("repo_not_found", "nope/nope was not found on GitHub.")
        return CountResult(owner, repo, self.issue_count)

    def collect(self, owner, repo, **kwargs):
        self.calls.append(("collect", owner, repo, kwargs))
        if self.error:
            raise self.error
        return CollectResult(collected=10, skipped=0)

    def collect_specific(self, owner, repo, numbers):
        self.calls.append(("collect_specific", owner, repo, list(numbers)))
        return SpecificResult(1, tuple(range(1, len(numbers) + 1)), len(numbers), 0, 0)


class FakeClassification:
    def __init__(self):
        self.calls = []

    def preview(self, owner, repo, **kwargs):
        self.calls.append(("preview", owner, repo, kwargs))
        return ClassifyPreview(owner, repo, 10, 10, "m", "p", None, None, ())

    def classify(self, owner, repo, **kwargs):
        self.calls.append(("classify", owner, repo, kwargs))
        return ClassifyResult(owner, repo, classified=10, failed={})

    def classify_ids(self, owner, repo, issue_ids):
        self.calls.append(("classify_ids", owner, repo, list(issue_ids)))
        return ClassifyResult(owner, repo, classified=len(issue_ids), failed={})


class FakeLinkage:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def link(self, owner, repo, package, **kwargs):
        self.calls.append(("link", owner, repo, package, kwargs))
        if self.error:
            raise self.error
        return LinkResult(package, 1, 0, 1, 0, 0)


def fake_app(settings, **services):
    defaults = {
        "collection": FakeCollection(),
        "classification": FakeClassification(),
        "linkage": FakeLinkage(),
    }
    defaults.update(services)
    return replace(build_app(settings), **defaults)
