"""Issue-to-version linkage.

A naive "highest semver published at or before the issue date" heuristic disagrees
with this implementation on 35% of a 53,000-issue corpus, almost all of it
prereleases being treated as the live release. These tests pin the correct
behaviour.
"""

import pytest

from tools.core.dependencies import DependencySnapshotter, _isPrerelease, _parseTs
from tools.core.npm_registry import sortVersionsDesc


@pytest.fixture
def pick():
    snap = DependencySnapshotter.__new__(DependencySnapshotter)

    def _pick(times, issue_date):
        # built exactly as snapshot_for_classified builds it
        ordered = sortVersionsDesc(list(times))
        got = snap._live_version_at(issue_date, [(v, times.get(v)) for v in ordered])
        return got[0] if got else None

    return _pick


class TestPublishOrder:
    def test_backport_published_after_a_major_wins(self, pick):
        """1.9.5 ships after 2.0.0. Semver order puts it below 2.x, so the old
        heuristic skipped it and linked the issue to 2.0.0."""
        times = {
            "1.9.0": "2019-06-01T00:00:00.000Z",
            "2.0.0": "2020-01-01T00:00:00.000Z",
            "1.9.5": "2020-06-01T00:00:00.000Z",
        }
        assert pick(times, "2020-07-01T00:00:00Z") == "1.9.5"

    def test_normal_history_is_unaffected(self, pick):
        times = {
            "1.0.0": "2019-01-01T00:00:00.000Z",
            "1.1.0": "2019-06-01T00:00:00.000Z",
            "2.0.0": "2020-01-01T00:00:00.000Z",
        }
        assert pick(times, "2019-08-01T00:00:00Z") == "1.1.0"
        assert pick(times, "2021-01-01T00:00:00Z") == "2.0.0"

    def test_issue_predating_every_release_is_unlinkable(self, pick):
        times = {"1.0.0": "2019-01-01T00:00:00.000Z"}
        assert pick(times, "2018-01-01T00:00:00Z") is None


class TestPrereleases:
    def test_a_newer_prerelease_does_not_displace_the_stable_release(self, pick):
        """The dominant correction: a prerelease is never the npm `latest` tag and
        is not what a reporter installed."""
        times = {"2.0.0": "2020-01-01T00:00:00.000Z", "2.1.0-next.999": "2020-06-01T00:00:00.000Z"}
        assert pick(times, "2020-07-01T00:00:00Z") == "2.0.0"

    def test_prerelease_used_when_no_stable_exists_yet(self, pick):
        times = {"1.0.0-beta.1": "2020-01-01T00:00:00.000Z"}
        assert pick(times, "2020-07-01T00:00:00Z") == "1.0.0-beta.1"

    def test_prerelease_detection(self):
        assert _isPrerelease("2.1.0-next.999")
        assert not _isPrerelease("2.1.0")


class TestTimestamps:
    def test_release_half_a_second_after_the_issue_is_excluded(self, pick):
        """npm reports sub-second precision, GitHub does not. Compared as raw
        strings, '.' sorts before 'Z' and the release looked earlier."""
        times = {"1.0.0": "2019-01-01T00:00:00.000Z", "1.1.0": "2020-01-01T09:00:00.500Z"}
        assert pick(times, "2020-01-01T09:00:00Z") == "1.0.0"

    def test_identical_instant_in_both_formats_is_included(self, pick):
        times = {"1.0.0": "2020-01-01T09:00:00.000Z"}
        assert pick(times, "2020-01-01T09:00:00Z") == "1.0.0"

    def test_formats_parse_to_the_same_instant(self):
        assert _parseTs("2020-01-01T00:00:00.000Z") == _parseTs("2020-01-01T00:00:00Z")

    def test_bare_date_is_comparable_with_an_aware_timestamp(self):
        assert _parseTs("2020-01-01") < _parseTs("2020-06-01T00:00:00.000Z")

    def test_unparseable_values_return_none(self):
        assert _parseTs("not-a-date") is None
        assert _parseTs(None) is None
        assert _parseTs("") is None

    def test_missing_publish_time_is_skipped_not_fatal(self, pick):
        times = {"1.0.0": None, "0.9.0": "2019-01-01T00:00:00.000Z"}
        assert pick(times, "2020-01-01T00:00:00Z") == "0.9.0"
