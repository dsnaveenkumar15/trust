"""Where Trust finds servers to audit: the official MCP registry (GitHub search lives in core.github)."""
import re

import httpx

from core.github import repo_from_url  # noqa: F401  (moved to core; still importable from here)


REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"
class SourceError(Exception):
    """A discovery source is unavailable; discovery continues with the others."""


def registry_repos(client: httpx.Client, max_pages: int = 5, page_size: int = 100) -> list[str]:
    repos: list[str] = []
    cursor = None
    for _ in range(max_pages):
        params = {"version": "latest", "limit": page_size}
        if cursor:
            params["cursor"] = cursor
        try:
            response = client.get(REGISTRY_URL, params=params)
        except httpx.HTTPError as exc:
            raise SourceError(f"registry unreachable: {exc}") from exc
        if response.status_code != 200:
            raise SourceError(f"registry returned {response.status_code}")
        data = response.json()
        for entry in data.get("servers", []):
            info = (entry.get("server") or {}).get("repository") or {}
            repo = repo_from_url(info.get("url", "")) if info.get("source") == "github" else None
            if repo and repo not in repos:
                repos.append(repo)
        cursor = (data.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    return repos
