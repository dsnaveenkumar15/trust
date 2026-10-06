# tests/test_theme_js.py
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "web" / "static" / "theme.js"
HARNESS = """
const attrs = {}; let cookie = %(cookie)s; const store = %(store)s;
global.document = { documentElement: { getAttribute: k => attrs[k] ?? null, setAttribute: (k, v) => { attrs[k] = v; } },
                    get cookie() { return cookie; }, set cookie(v) { cookie = v; }, addEventListener() {} };
global.location = { hostname: "radar.naveenlabs.tech" };
global.localStorage = { getItem: k => store[k] ?? null, setItem: (k, v) => { store[k] = v; } };
global.window = { matchMedia: () => ({ matches: %(system_dark)s }) };
if (%(default)s) attrs["data-default-theme"] = %(default)s;
require(%(script)s);
console.log(attrs["data-theme"]);
"""


def run(default=None, cookie="", store=None, system_dark=False):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    code = HARNESS % {"cookie": json.dumps(cookie), "store": json.dumps(store or {}), "system_dark": json.dumps(system_dark),
                      "default": json.dumps(default) if default else "null", "script": json.dumps(str(SCRIPT))}
    return subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout.strip()


def test_signature_mode_applies_when_the_visitor_never_chose():
    assert run(default="dark", system_dark=False) == "dark"
    assert run(default="light", system_dark=True) == "light"


def test_an_explicit_choice_wins_over_the_signature_mode():
    assert run(default="dark", cookie="theme=light") == "light"
    assert run(default="dark", store={"theme": "light"}) == "light"


def test_sites_without_a_signature_mode_follow_the_system():
    assert run(system_dark=True) == "dark" and run(system_dark=False) == "light"
