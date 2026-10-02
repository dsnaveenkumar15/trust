import json

import pytest

from core.publish import publish
from tests.webutil import make_settings
from web import published


def put(root, planet, dataset, files):
    return publish(root, planet, dataset, {n: json.dumps(v).encode() for n, v in files.items()}, {})


def test_published_data_re_reads_only_when_the_pointer_changes(tmp_path, monkeypatch):
    put(tmp_path, "radar", "digests", {"index.json": {"digests": []}})
    calls = []
    real = published.load_current
    monkeypatch.setattr(published, "load_current", lambda *a: calls.append(a) or real(*a))
    data = published.PublishedData(tmp_path, "radar", "digests")
    assert data._json("index.json", None) == {"digests": []} and data._json("index.json", None) == {"digests": []}
    assert len(calls) == 1
    put(tmp_path, "radar", "digests", {"index.json": {"digests": [1]}})
    assert data._json("index.json", None) == {"digests": [1]} and len(calls) == 2


def test_a_missing_or_corrupt_release_gives_the_default(tmp_path):
    data = published.PublishedData(tmp_path / "nowhere", "radar", "digests")
    assert data._json("index.json", "default") == "default" and data.release() is None
    put(tmp_path, "radar", "digests", {"index.json": {"ok": 1}})
    data = published.PublishedData(tmp_path, "radar", "digests")
    assert data._json("not-in-the-manifest.json", "default") == "default"


def test_trust_index_and_radar_packages_read_their_own_roots(tmp_path):
    put(tmp_path / "t", "trust", "directory", {"index.json": {"count": 1, "servers": [{"repo": "a/b", "grade": "B"}]}})
    put(tmp_path / "r", "radar", "digests", {"packages.json": {"packages": {"pypi:x": {"latest_stable": "1"}}}})
    assert published.TrustIndex(tmp_path / "t").index()["servers"][0]["repo"] == "a/b"
    assert published.RadarPackages(tmp_path / "r").packages() == {"pypi:x": {"latest_stable": "1"}}
    assert published.TrustIndex(tmp_path / "r").index() == {"count": 0, "servers": []}
    assert published.RadarPackages(tmp_path / "t").packages() == {}


@pytest.mark.parametrize("payload", [None, [], "x", {"servers": 3}])
def test_odd_trust_data_is_an_empty_index(tmp_path, payload):
    put(tmp_path, "trust", "directory", {"index.json": payload})
    assert published.TrustIndex(tmp_path).index()["servers"] == []


def test_the_other_roots_default_to_the_data_root(tmp_path):
    settings = make_settings(tmp_path)
    assert settings.trust_root == settings.radar_root == settings.data_root
