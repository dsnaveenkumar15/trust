"""Atomic, hash-manifested releases. `web` only ever serves a release whose manifest verifies."""
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

STALE_TMP_S = 3600
_SEGMENT = re.compile(r"[a-z0-9][a-z0-9._-]{0,100}")


def _base(root, planet: str, name: str) -> Path:
    """Planet and dataset names become directories: allow only simple slugs so nothing escapes `root`."""
    for value in (planet, name):
        if not _SEGMENT.fullmatch(value):
            raise ValueError(f"unsafe planet/name segment: {value!r}")
    return Path(root) / planet / name


def _check_rel(rel: str) -> None:
    parts = PurePosixPath(rel).parts
    if (not rel or rel == "manifest.json" or rel.startswith("/") or "\\" in rel or ":" in rel
            or ".." in parts):
        raise ValueError(f"unsafe release path: {rel!r}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class Release:
    id: str
    path: Path
    manifest: dict

    def read(self, rel: str) -> bytes:
        _check_rel(rel)
        return (self.path / rel).read_bytes()


def publish(root, planet: str, name: str, files: dict[str, bytes], provenance: dict, conn=None, now=None) -> str:
    if not files:
        raise ValueError("refusing to publish an empty release")
    for rel in files:
        _check_rel(rel)
    now = time.time() if now is None else now
    base = _base(root, planet, name)
    releases = base / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release_id = f"{int(now * 1000):015d}-{secrets.token_hex(3)}"   # zero-padded: lexical order = time order
    tmp = releases / f".tmp-{release_id}"
    tmp.mkdir()
    manifest = {"release_id": release_id, "planet": planet, "name": name, "created_at": now,
                "provenance": provenance, "files": {}}
    for rel, data in files.items():
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest["files"][rel] = _sha(data)
    (tmp / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.rename(tmp, releases / release_id)
    _write_atomic(base / "CURRENT", release_id)
    if conn is not None:
        conn.execute("INSERT INTO manifests(planet, name, release_id, created_at) VALUES (?, ?, ?, ?)",
                     (planet, name, release_id, now))
    return release_id


def verify(release_dir: Path) -> dict | None:
    try:
        manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
        for rel, digest in manifest["files"].items():
            _check_rel(rel)
            if _sha((release_dir / rel).read_bytes()) != digest:
                return None
        return manifest
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def load_current(root, planet: str, name: str) -> Release | None:
    """CURRENT first, then newest-first fallback; the first release that verifies wins."""
    base = _base(root, planet, name)
    releases = base / "releases"
    if not releases.is_dir():
        return None
    candidates = []
    current = base / "CURRENT"
    if current.exists():
        candidates.append(current.read_text(encoding="utf-8").strip())
    candidates += sorted((p.name for p in releases.iterdir() if p.is_dir() and not p.name.startswith(".tmp-")),
                         reverse=True)
    for release_id in dict.fromkeys(candidates):
        if not release_id or "/" in release_id or "\\" in release_id or release_id.startswith("."):
            continue
        manifest = verify(releases / release_id)
        if manifest is not None:
            return Release(release_id, releases / release_id, manifest)
    return None


def prune(root, planet: str, name: str, keep: int = 10) -> list[str]:
    """Delete releases beyond the newest `keep` (never CURRENT) and abandoned temp dirs older than 1h."""
    base = _base(root, planet, name)
    releases = base / "releases"
    if not releases.is_dir():
        return []
    current = (base / "CURRENT").read_text(encoding="utf-8").strip() if (base / "CURRENT").exists() else None
    ids = sorted((p.name for p in releases.iterdir() if p.is_dir()), reverse=True)
    real = [i for i in ids if not i.startswith(".tmp-")]
    stale_tmp = [i for i in ids if i.startswith(".tmp-")
                 and time.time() - (releases / i).stat().st_mtime > STALE_TMP_S]
    removed = []
    for release_id in real[keep:] + stale_tmp:
        if release_id == current:
            continue
        shutil.rmtree(releases / release_id)
        removed.append(release_id)
    return removed
