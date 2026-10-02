"""Readers for the verified releases other code publishes. Pointer, release and content cache live in one tuple, so a read
that started on an old release can only ever fill that release's own cache, and the release is re-verified only when
its CURRENT pointer changes. Missing, corrupt or half-published data gives the caller's default, never an error."""
import json
from pathlib import Path

from core.publish import load_current

EMPTY_TRUST_INDEX = {"count": 0, "servers": []}


class PublishedData:
    PLANET: str = ""
    DATASET: str = ""

    def __init__(self, root, planet: str | None = None, dataset: str | None = None):
        self.root = Path(root)
        if planet is not None:
            self.PLANET = planet
        if dataset is not None:
            self.DATASET = dataset
        self._state = (object(), None, {})

    def _current(self):
        try:
            pointer = (self.root / self.PLANET / self.DATASET / "CURRENT").read_text(encoding="utf-8").strip()
        except OSError:
            pointer = None
        state = self._state
        if pointer != state[0] or state[1] is None:
            state = (pointer, load_current(self.root, self.PLANET, self.DATASET), {})
            self._state = state
        return state

    def release(self):
        return self._current()[1]

    def _json(self, name: str, default):
        _, release, cache = self._current()
        if release is None or name not in release.manifest.get("files", {}):
            return default
        if name not in cache:
            try:
                cache[name] = json.loads(release.read(name))
            except (OSError, ValueError):
                return default                      # a failed read is not cached: the next request retries
        return cache[name]


class TrustIndex(PublishedData):
    """Trust's published directory index: what Blueprints needs to know about tool grades."""
    PLANET, DATASET = "trust", "directory"

    def index(self) -> dict:
        data = self._json("index.json", EMPTY_TRUST_INDEX)
        return data if isinstance(data, dict) and isinstance(data.get("servers"), list) else EMPTY_TRUST_INDEX


class RadarPackages(PublishedData):
    """Radar's published package facts: the frameworks Radar tracks, with latest version and advisories."""
    PLANET, DATASET = "radar", "digests"

    def packages(self) -> dict:
        data = self._json("packages.json", {})
        packages = data.get("packages") if isinstance(data, dict) else None
        return packages if isinstance(packages, dict) else {}
