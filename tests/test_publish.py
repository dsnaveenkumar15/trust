import json

import pytest

from core.publish import load_current, prune, publish

PROV = {"rule_version": "1.0", "prompt_version": "trust.review@1", "model": "sonnet"}


def test_roundtrip_with_manifest_and_provenance(tmp_path, conn):
    rid = publish(tmp_path, "trust", "directory", {"index.json": b'{"n": 1}', "servers/a.json": b"{}"},
                  PROV, conn=conn, now=1000.0)
    release = load_current(tmp_path, "trust", "directory")
    assert release.id == rid
    assert json.loads(release.read("index.json")) == {"n": 1}
    assert release.manifest["provenance"] == PROV
    assert set(release.manifest["files"]) == {"index.json", "servers/a.json"}
    assert conn.execute("SELECT release_id FROM manifests").fetchone()[0] == rid


def test_tampered_release_falls_back_to_previous_good(tmp_path):
    old = publish(tmp_path, "trust", "d", {"x.json": b"old"}, PROV, now=1000.0)
    new = publish(tmp_path, "trust", "d", {"x.json": b"new"}, PROV, now=2000.0)
    (tmp_path / "trust" / "d" / "releases" / new / "x.json").write_bytes(b"tampered")
    release = load_current(tmp_path, "trust", "d")
    assert release.id == old
    assert release.read("x.json") == b"old"


def test_broken_current_pointer_falls_back_to_newest_good(tmp_path):
    publish(tmp_path, "radar", "d", {"x.json": b"1"}, PROV, now=1000.0)
    newest = publish(tmp_path, "radar", "d", {"x.json": b"2"}, PROV, now=2000.0)
    (tmp_path / "radar" / "d" / "CURRENT").write_text("does-not-exist")
    assert load_current(tmp_path, "radar", "d").id == newest


def test_nothing_published_returns_none(tmp_path):
    assert load_current(tmp_path, "trust", "d") is None


@pytest.mark.parametrize("bad", ["../escape.json", "/abs.json", r"C:\x.json", r"a\b.json", "", "manifest.json"])
def test_unsafe_paths_rejected(tmp_path, bad):
    with pytest.raises(ValueError):
        publish(tmp_path, "trust", "d", {bad: b"x"}, PROV)


def test_empty_release_rejected(tmp_path):
    with pytest.raises(ValueError):
        publish(tmp_path, "trust", "d", {}, PROV)


def test_prune_keeps_newest_and_current(tmp_path):
    ids = [publish(tmp_path, "trust", "d", {"x.json": str(i).encode()}, PROV, now=1000.0 + i) for i in range(5)]
    (tmp_path / "trust" / "d" / "CURRENT").write_text(ids[0])  # pin an old one as current
    removed = prune(tmp_path, "trust", "d", keep=2)
    assert set(removed) == {ids[1], ids[2]}
    remaining = {p.name for p in (tmp_path / "trust" / "d" / "releases").iterdir()}
    assert remaining == {ids[0], ids[3], ids[4]}


@pytest.mark.parametrize("planet,name", [("trust", "../../escaped"), ("../x", "d"), ("trust", "a/b"),
                                         ("trust", ""), ("Trust", "d"), ("trust", r"a\b")])
def test_unsafe_planet_or_name_rejected_everywhere(tmp_path, planet, name):
    with pytest.raises(ValueError):
        publish(tmp_path / "root", planet, name, {"x.json": b"1"}, PROV)
    with pytest.raises(ValueError):
        load_current(tmp_path / "root", planet, name)
    with pytest.raises(ValueError):
        prune(tmp_path / "root", planet, name)
    assert not (tmp_path / "escaped").exists()
