# tests/test_trust_polish.py - small Trust fixes: a failed check says so, the filter keeps its place, fonts and surfaces.
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"
HARNESS = """
function make(tag) { return {tag, textContent: '', className: '', children: [], append(...c) { this.children.push(...c); },
                             replaceChildren(...c) { this.children = c; }}; }
const out = make('div');
const form = Object.assign(make('form'), {manifest: {value: '{}'}, addEventListener(type, fn) { this.handler = fn; }});
global.document = {createElement: make, getElementById: id => id === 'check-form' ? form : id === 'check-result' ? out : null};
global.window = {};
global.fetch = async () => { %(fetch)s };
require(%(script)s);
(async () => { await form.handler({preventDefault() {}}); console.log(JSON.stringify(out.children.map(c => c.textContent))); })();
"""


def submit(fetch_body: str) -> list[str]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    code = HARNESS % {"fetch": fetch_body, "script": json.dumps(str(STATIC / "trust-check.js"))}
    return json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)


def test_a_network_failure_tells_the_visitor_instead_of_leaving_checking_on_screen():
    shown = submit("throw new TypeError('network down');")
    assert shown and "Checking" not in shown[0] and "could not" in shown[0].lower()


def test_a_server_error_is_still_shown_as_before():
    shown = submit("return {ok: false, status: 503, json: async () => ({detail: 'Busy, try again.'})};")
    assert shown == ["Busy, try again."]


def test_the_directory_filter_returns_to_the_reviews_not_the_top_of_the_page(tmp_path):
    from tests.test_trust_site import publish_entries, server_entry
    settings = make_settings(tmp_path)
    publish_entries(settings.data_root, [server_entry("acme/alpha", "A")])
    html = TestClient(create_app(settings), base_url="http://trust.localhost").get("/").text
    assert 'class="filters"' in html and 'action="/#reviews"' in html and 'id="reviews"' in html


def test_trust_css_fixes():
    css = (STATIC / "themes" / "trust" / "theme.css").read_text(encoding="utf-8")
    assert re.search(r"\.planet-trust h1\.mono\s*\{[^}]*font-family:\s*var\(--mono\)", css), "a monospace h1 must stay monospace"
    assert re.search(r"\.window-finding \.quote\s*\{[^}]*background:\s*var\(--bg\)", css), "the quote must differ from its panel"
