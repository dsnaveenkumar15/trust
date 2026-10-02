"""Shared evidence checks. A claim that fails these is dropped, never published."""
import re
from typing import Iterable

MIN_QUOTE_CHARS = 4


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(files: dict[str, str], path: str, line: int, quote: str, window: int = 3) -> bool:
    """True if `quote` appears verbatim (whitespace-normalised) in `path` within ±window lines of `line` (1-based)."""
    text = files.get(path)
    needle = _norm(quote)
    if text is None or len(needle) < MIN_QUOTE_CHARS:
        return False
    lines = text.splitlines()
    if not 1 <= line <= len(lines):
        return False
    lo, hi = max(0, line - 1 - window), min(len(lines), line + window)
    return needle in _norm("\n".join(lines[lo:hi]))


def missing(items: Iterable[str], known: Iterable[str]) -> list[str]:
    """Items absent from `known`, deduplicated, in first-seen order."""
    known_set, seen, out = set(known), set(), []
    for item in items:
        if item not in known_set and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def quote_in_text(text: str, quote: str) -> bool:
    """True if `quote` appears verbatim (whitespace-normalised) anywhere in `text`; short quotes never count."""
    needle = _norm(quote)
    return len(needle) >= MIN_QUOTE_CHARS and needle in _norm(text)
