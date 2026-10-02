def locate(files: dict[str, str], path: str, start_line: int, needle: str, max_ahead: int = 400) -> tuple[int, str]:
    """First source line at/after `start_line` containing the needle's first line; that line is the evidence quote.
    Falls back to the start line, so every rule finding cites text that really exists in the file."""
    lines = files.get(path, "").splitlines()
    stripped = needle.strip()
    probe = stripped.splitlines()[0].strip()[:60] if stripped else ""
    if probe:
        for idx in range(max(0, start_line - 1), min(len(lines), start_line - 1 + max_ahead)):
            if probe in lines[idx]:
                return idx + 1, lines[idx].strip()[:300]
    if 1 <= start_line <= len(lines):
        return start_line, lines[start_line - 1].strip()[:300]
    return start_line, ""
