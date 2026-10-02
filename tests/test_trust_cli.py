import pytest

from trust.cli import main
from trust.local import load_local

RISKY = '''from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ops")


@mcp.tool()
def run(cmd: str) -> str:
    """Run a maintenance command."""
    import subprocess
    subprocess.run(cmd)
    return str(eval(cmd))
'''


def test_load_local_keeps_only_wanted_decodable_files(tmp_path):
    root = tmp_path / "srv"
    (root / "node_modules" / "x").mkdir(parents=True)
    (root / "server.py").write_text(RISKY, encoding="utf-8")
    (root / "notes.txt").write_text("ignore me", encoding="utf-8")
    (root / "node_modules" / "x" / "index.js").write_text("server.tool('a','b',{},h)", encoding="utf-8")
    (root / "bad.py").write_bytes("café".encode("latin-1"))
    snap = load_local(root)
    assert snap["repo"] == "local/srv" and snap["sha"] == "local" and snap["meta"] == {}
    assert list(snap["files"]) == ["server.py"]


def test_cli_local_without_llm_prints_report(tmp_path, capsys):
    root = tmp_path / "srv"
    root.mkdir()
    (root / "server.py").write_text(RISKY, encoding="utf-8")
    assert main(["--local", str(root), "--no-llm", "--db", str(tmp_path / "a.db")]) == 0
    out = capsys.readouterr().out
    assert "status provisional" in out and "grade B" in out
    assert "shell-exec" in out and "server.py:10" in out


def test_cli_requires_a_source(capsys):
    with pytest.raises(SystemExit):
        main([])


def test_cli_publish_builds_a_release(tmp_path, capsys):
    root = tmp_path / "srv"
    root.mkdir()
    (root / "server.py").write_text(RISKY, encoding="utf-8")
    db = str(tmp_path / "a.db")
    assert main(["--local", str(root), "--no-llm", "--db", db]) == 0
    assert main(["--publish", "--include-local", "--db", db, "--data-root", str(tmp_path / "pub")]) == 0
    assert "'published': True" in capsys.readouterr().out
    from core.publish import load_current
    assert load_current(tmp_path / "pub", "trust", "directory").read("servers/local--srv.json")


def test_cli_publish_keeps_local_audits_private_by_default(tmp_path, capsys):
    root = tmp_path / "srv"
    root.mkdir()
    (root / "server.py").write_text(RISKY, encoding="utf-8")
    db = str(tmp_path / "a.db")
    main(["--local", str(root), "--no-llm", "--db", db])
    main(["--publish", "--db", db, "--data-root", str(tmp_path / "pub")])
    from core.publish import load_current
    assert "servers/local--srv.json" not in load_current(tmp_path / "pub", "trust", "directory").manifest["files"]
