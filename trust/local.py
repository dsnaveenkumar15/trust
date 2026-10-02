from pathlib import Path

from trust.pipeline import wanted_path


def load_local(directory) -> dict:
    """Snapshot of a local checkout, same shape as GitHub.snapshot (no repo metadata)."""
    root = Path(directory)
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not wanted_path(rel):
            continue
        try:
            files[rel] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return {"repo": f"local/{root.name}", "sha": "local", "meta": {}, "files": files, "skipped": [],
            "truncated": False}
