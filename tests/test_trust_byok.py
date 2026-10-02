import json

import pytest
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app
from web.trust_byok import api_schema

MANIFEST = json.dumps({"tools": [{"name": "run", "description": "Run a maintenance command."}]})
CODE = "import subprocess\n\n\ndef run(cmd):\n    subprocess.run(cmd)\n    return eval(cmd)\n"
BASE = {"manifest": MANIFEST, "code": CODE, "filename": "server.py"}


@pytest.fixture
def c(tmp_path):
    return TestClient(create_app(make_settings(tmp_path)), base_url="http://trust.localhost")


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_prompt_endpoint_returns_delimited_prompt_and_api_safe_schema(c):
    data = c.post("/api/byok/prompt", json=BASE).json()
    assert data["user"].startswith("<untrusted_repository_content>") and data["findings"] == 2
    assert data["models"][0] == "claude-opus-5-5"
    for node in walk(data["schema"]):
        assert not {"maxLength", "minLength", "maxItems", "minimum", "maximum", "default"} & set(node)
        if node.get("type") == "object":
            assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])


def test_api_schema_keeps_properties_named_like_keywords():
    schema = {"type": "object", "properties": {"maxLength": {"type": "string", "maxLength": 3}}}
    assert api_schema(schema) == {"type": "object", "properties": {"maxLength": {"type": "string"}},
                                  "additionalProperties": False, "required": ["maxLength"]}


def test_apply_scores_the_visitors_review(c):
    confirm = {"verdicts": [{"finding_id": i, "decision": "confirm", "reason_code": "true_positive",
                             "confidence": "high", "explanation": "real"} for i in range(2)],
               "new_findings": [{"category": "exfiltration", "severity": "medium", "confidence": "medium",
                                 "file": "server.py", "line": 6, "quote": "return eval(cmd)", "message": "m"},
                                {"category": "exfiltration", "severity": "high", "confidence": "high",
                                 "file": "server.py", "line": 1, "quote": "os.remove('/')", "message": "invented"}],
               "summary": "s"}
    data = c.post("/api/byok/apply", json=dict(BASE, output=confirm)).json()
    assert data["grade"] == "B" and data["dropped_new"] == 1
    assert {f["rule"] for f in data["findings"]} == {"shell-exec", "dynamic-eval", "llm-review"}


def test_apply_rejects_malformed_review_output(c):
    r = c.post("/api/byok/apply", json=dict(BASE, output={"verdicts": "nope"}))
    assert r.status_code == 422


@pytest.mark.parametrize("filename", ["../etc/passwd", "manifest.json", "a b.py", "/abs.py"])
def test_unsafe_filenames_are_rejected(c, filename):
    assert c.post("/api/byok/prompt", json=dict(BASE, filename=filename)).status_code in (400, 422)


def test_byok_section_and_script_never_send_the_key_to_this_site(c):
    html = c.get("/check").text
    assert 'id="byok-key"' in html and 'name="apikey"' not in html   # no name: a no-JS submit cannot carry the key
    js = c.get("/static/trust-byok.js").text
    assert "https://api.anthropic.com/v1/messages" in js and "anthropic-dangerous-direct-browser-access" in js
    assert "innerHTML" not in js and "localStorage" not in js and "sessionStorage" not in js
    assert js.count("byok-key") == 1 and "apikey" not in js   # read once by id, used only in api.anthropic.com headers
    assert "key" not in js.split("const input = ")[1].split(";")[0]   # the body posted to this site has no key
