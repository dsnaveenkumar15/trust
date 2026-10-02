"""Copy the shared core from the master repository into this one, so the app repo stays self-contained but never drifts by accident.

    python scripts/sync_core.py --source PATH_OR_GIT_URL [--ref BRANCH_OR_TAG] [--check] [--allow-dirty]

The master is a repository whose scripts/core_manifest.json lists the shared files. --check only reports what would change
(exit 1 when something would). A local master with uncommitted changes in those files is refused unless --allow-dirty,
so a half-finished edit never spreads into the app repos."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def expand(root: Path, patterns: list[str]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        if pattern.endswith("/**"):
            base = root / pattern[:-3]
            found += [p.relative_to(root).as_posix() for p in sorted(base.rglob("*"))
                      if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
        elif (root / pattern).is_file():
            found.append(pattern)
    return sorted(set(found))


def git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="sync_core")
    parser.add_argument("--source", required=True)
    parser.add_argument("--ref", default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--dest", default=str(ROOT), help="the repository to update (default: this one)")
    args = parser.parse_args(argv)
    dest = Path(args.dest).resolve()
    temp = None
    source = Path(args.source)
    if not source.exists():
        temp = tempfile.mkdtemp(prefix="core-")
        clone = ["git", "clone", "--depth", "1", *(["--branch", args.ref] if args.ref else []), args.source, temp]
        subprocess.run(clone, check=True, capture_output=True)
        source = Path(temp)
    try:
        source = source.resolve()
        manifest = json.loads((source / "scripts" / "core_manifest.json").read_text(encoding="utf-8"))
        files = expand(source, manifest["files"])
        commit = "unknown"
        if (source / ".git").exists():
            commit = git(["rev-parse", "HEAD"], source).strip()
            dirty = git(["status", "--porcelain", "--", *files], source).strip()
            if dirty and not args.allow_dirty:
                print("refusing: the master's core files have uncommitted changes:\n" + dirty, file=sys.stderr)
                return 2
        changes = []
        for rel in files:
            target = dest / rel
            if not target.exists():
                changes.append(("new", rel))
            elif target.read_bytes() != (source / rel).read_bytes():
                changes.append(("changed", rel))
        for kind, rel in changes:
            print(f"{kind:8} {rel}")
        print(f"{len(changes)} difference(s) across {len(files)} shared files (master {commit[:12]})")
        if args.check:
            return 1 if changes else 0
        for _, rel in changes:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, dest / rel)
        (dest / "CORE_VERSION").write_text(commit + "\n", encoding="utf-8", newline="\n")
        return 0
    finally:
        if temp:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
