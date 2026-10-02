"""Read-only GitHub access for audits: metadata, tree, raw files. Never clones, never executes."""
import json
import re
import time
from urllib.parse import quote

import httpx

from core.llm import RateLimited

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
MAX_FILES = 200
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 3_000_000
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_SHA = re.compile(r"[0-9a-fA-F]{7,40}")


_GITHUB_URL = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/#?].*)?$")


def repo_from_url(url: str) -> str | None:
    match = _GITHUB_URL.match(url.strip())
    if not match or ".." in match.group(1) or ".." in match.group(2):
        return None
    return f"{match.group(1)}/{match.group(2)}".lower()


class GitHubError(Exception):
    """Unexpected GitHub response."""


class RepoNotFound(GitHubError):
    """Repository or ref does not exist (or is private)."""


class GitHubRateLimited(RateLimited):
    """GitHub limit hit. Subclassing RateLimited makes the M0 worker defer the job instead of failing it."""

    def __init__(self, message: str, reset_at: float):
        super().__init__(message)
        self.reset_at = reset_at


class GitHub:
    def __init__(self, conn, token: str | None = None, client: httpx.Client | None = None, clock=time.time):
        self.conn, self.clock = conn, clock
        self.client = client or httpx.Client(timeout=30, follow_redirects=True)
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                        "User-Agent": "atlas-trust"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _check(self, response: httpx.Response, url: str) -> None:
        if response.status_code == 429 or (
                response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"):
            reset = float(response.headers.get("x-ratelimit-reset") or self.clock() + 3600)
            raise GitHubRateLimited(f"GitHub rate limit on {url}", reset)
        if response.status_code == 404:
            raise RepoNotFound(url)

    def _get(self, url: str, headers: dict) -> httpx.Response:
        try:
            return self.client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub unreachable: {exc}") from exc

    def _get_json(self, url: str):
        cached = self.conn.execute("SELECT etag, body FROM http_cache WHERE url=?", (url,)).fetchone()
        headers = dict(self.headers)
        if cached:
            headers["If-None-Match"] = cached["etag"]
        response = self._get(url, headers)
        if response.status_code == 304 and cached:
            return json.loads(cached["body"])
        self._check(response, url)
        if response.status_code != 200:
            raise GitHubError(f"GitHub {response.status_code} for {url}")
        etag = response.headers.get("etag")
        if etag:
            self.conn.execute(
                "INSERT INTO http_cache(url, etag, body, fetched_at) VALUES (?, ?, ?, ?) ON CONFLICT(url) DO UPDATE "
                "SET etag=excluded.etag, body=excluded.body, fetched_at=excluded.fetched_at",
                (url, etag, response.text, self.clock()))
        return response.json()

    def repo_meta(self, repo: str) -> dict:
        data = self._get_json(f"{API}/repos/{repo}")
        return {
            "default_branch": data.get("default_branch") or "main",
            "stars": data.get("stargazers_count", 0),
            "license": (data.get("license") or {}).get("spdx_id"),
            "archived": bool(data.get("archived")),
            "pushed_at": data.get("pushed_at"),
            "html_url": data.get("html_url"),
        }

    def head_sha(self, repo: str, ref: str) -> str:
        return self._get_json(f"{API}/repos/{repo}/commits/{quote(ref, safe='')}")["sha"]

    def tree(self, repo: str, sha: str) -> tuple[list[dict], bool]:
        data = self._get_json(f"{API}/repos/{repo}/git/trees/{sha}?recursive=1")
        return [e for e in data.get("tree", []) if e.get("type") == "blob"], bool(data.get("truncated"))

    def search_repositories(self, query: str, per_page: int = 50) -> list[dict]:
        data = self._get_json(f"{API}/search/repositories?q={quote(query, safe=':')}&sort=stars&order=desc"
                              f"&per_page={per_page}")
        return [{"repo": item["full_name"].lower(), "stars": item.get("stargazers_count", 0),
                 "archived": bool(item.get("archived"))} for item in data.get("items", [])]

    def releases(self, repo: str, per_page: int = 30) -> list[dict]:
        out = []
        for r in self._get_json(f"{API}/repos/{repo}/releases?per_page={per_page}"):
            if r.get("draft"):
                continue
            out.append({"tag": r.get("tag_name") or "", "name": r.get("name") or "", "url": r.get("html_url") or "",
                        "published": r.get("published_at") or r.get("created_at") or "", "body": r.get("body") or "",
                        "prerelease": bool(r.get("prerelease"))})
        return out

    def raw(self, repo: str, sha: str, path: str) -> str | None:
        url = f"{RAW}/{repo}/{sha}/{quote(path)}"
        response = self._get(url, {"User-Agent": self.headers["User-Agent"]})
        self._check(response, url)
        if response.status_code != 200 or b"\x00" in response.content:
            return None
        # Replace undecodable bytes instead of skipping: one stray byte must not hide a whole file from the audit.
        return response.content.decode("utf-8", "replace")

    def snapshot(self, repo: str, sha: str | None = None, wanted=lambda path: True) -> dict:
        if not _REPO.fullmatch(repo) or ".." in repo:
            raise ValueError(f"not an owner/name repository: {repo!r}")
        if sha is not None and not _SHA.fullmatch(sha):
            raise ValueError(f"not a commit SHA: {sha!r}")
        meta = self.repo_meta(repo)
        sha = sha or self.head_sha(repo, meta["default_branch"])
        entries, tree_truncated = self.tree(repo, sha)
        candidates = sorted((e for e in entries if wanted(e["path"])), key=lambda e: e["path"])
        files, skipped, total = {}, [], 0
        for entry in candidates[:MAX_FILES]:
            size = entry.get("size") or 0
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                skipped.append(entry["path"])
                continue
            text = self.raw(repo, sha, entry["path"])
            if text is None:
                skipped.append(entry["path"])
                continue
            files[entry["path"]] = text
            total += size
        return {"repo": repo, "sha": sha, "meta": meta, "files": files, "skipped": skipped,
                "truncated": tree_truncated or len(candidates) > MAX_FILES}
