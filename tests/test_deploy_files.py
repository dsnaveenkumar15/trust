from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def service_block(compose: str, name: str) -> str:
    start = compose.index(f"\n  {name}:\n")
    nxt = [compose.find(f"\n  {other}:\n", start + 1) for other in ("web", "batch") if other != name]
    ends = [i for i in nxt if i > start] + [compose.index("\nnetworks:\n")]
    return compose[start:min(ends)]


def test_claude_credentials_only_reach_batch():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "batch.env" in service_block(compose, "batch")
    assert "batch.env" not in service_block(compose, "web")


def test_traefik_routes_this_apps_host_to_web():
    web = service_block((ROOT / "docker-compose.yml").read_text(encoding="utf-8"), "web")
    assert "TRUST_HOST" in web and "traefik.enable=true" in web and "PLANETS=trust" in web


def test_resource_caps_present():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'cpus: "1.5"' in service_block(compose, "batch")
    assert "memory:" in service_block(compose, "web")


def test_the_files_the_dockerfile_copies_are_enough_to_import_every_entry_point(tmp_path):
    """The images copy only these directories: a module that reaches for anything else would only fail on the VPS."""
    import os
    import re
    import shutil
    import subprocess
    import sys

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for source, target in re.findall(r"^COPY (?!requirements)(\S+) (\S+)$", dockerfile, flags=re.M):
        src, dst = ROOT / source, tmp_path / target.lstrip("./")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst) if src.is_dir() else shutil.copy(src, dst)
    code = ("import web.app, web.admin, core.worker, trust.jobs, evals.trust_eval;"
            "from web.config import Settings;"
            "app = web.app.create_app(Settings(hosts={'trust': 't.l', 'radar': 'r.l', 'blueprints': 'b.l'}, db_path='/tmp/x.db', "
            "data_root='/tmp/x', admin_user=None, admin_password=None, trust_proxy=False, ollama_url='http://o', public_scheme='http', "
            "public_port='', dispute_url=''))")
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1"})
    assert result.returncode == 0, result.stderr[-800:]
