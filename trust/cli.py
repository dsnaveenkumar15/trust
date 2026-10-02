"""Audit one repository from the command line: python -m trust.cli owner/repo | --local DIR"""
import argparse
import os
import sys
import time

from core.claude_cli import ClaudeCliBackend
from core.db import connect
from core.github import GitHub, GitHubError
from trust import store
from trust.local import load_local
from trust.pipeline import run_audit, wanted_path

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def format_report(outputs: dict) -> str:
    snap, record, scored = outputs["fetch"], outputs["record"], outputs["score"]
    lines = [
        "Trust audit",
        f"repo   {snap['repo']} @ {snap['sha']}",
        f"status {record['status']}   grade {scored['grade']} (score {scored['total']})",
    ]
    if scored["categories"]:
        lines.append("  " + " | ".join(f"{c} {v}" for c, v in scored["categories"].items()))
    findings = sorted(outputs["review"]["findings"], key=lambda f: (SEVERITY_ORDER[f["severity"]], f["file"], f["line"]))
    lines.append(f"findings ({len(findings)}):")
    for f in findings:
        lines.append(f"  [{f['severity']}] {f['rule']}  {f['file']}:{f['line']}  {f['message']}")
        if f["quote"]:
            lines.append(f"      > {f['quote']}")
        if f.get("explanation"):
            lines.append(f"      reviewer: {f['explanation']}")
    if outputs["review"]["needs_review"]:
        lines.append("needs owner review:")
        lines.extend(f"  - {item}" for item in outputs["review"]["needs_review"])
    if snap.get("skipped"):
        lines.append(f"skipped files: {', '.join(snap['skipped'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m trust.cli", description="Audit one MCP server repository.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("repo", nargs="?", help="owner/name on GitHub")
    source.add_argument("--local", help="audit a local directory instead of GitHub")
    source.add_argument("--publish", action="store_true", help="publish the Trust directory release and exit")
    parser.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "data/published"))
    parser.add_argument("--include-local", action="store_true",
                        help="with --publish: also publish local/ audits (local previews only, never production)")
    parser.add_argument("--sha", help="commit SHA (default: tip of the default branch)")
    parser.add_argument("--no-llm", action="store_true", help="rules only; the audit is marked provisional")
    parser.add_argument("--model", default="sonnet", help="Claude model for the review (default: sonnet)")
    parser.add_argument("--db", default="data/planets.db")
    args = parser.parse_args(argv)

    conn = connect(args.db)
    store.ensure_schema(conn)
    if args.publish:
        from trust.publishing import publish_directory
        print(publish_directory(conn, args.data_root, time.time(), include_local=args.include_local))
        return 0
    try:
        if args.local:
            snapshot = load_local(args.local)
        else:
            snapshot = GitHub(conn, token=os.environ.get("GITHUB_TOKEN") or None).snapshot(args.repo, args.sha,
                                                                                            wanted_path)
    except (GitHubError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    backend = None if args.no_llm else ClaudeCliBackend(model=args.model)
    outputs = run_audit(conn, snapshot, backend, None if args.no_llm else args.model, time.time())
    print(format_report(outputs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
