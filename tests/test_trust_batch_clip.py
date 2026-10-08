import json

import httpx

from core.db import connect
from core.llm import Prompt, complete
from core.openrouter import OpenRouterBackend
from trust import store
from trust.review import ReviewOutput


def test_trusts_real_review_schema_takes_an_over_long_openrouter_reply(tmp_path):
    """The live failure: the model's summary ran past 1200 characters and the whole review was rejected."""
    reply = {"verdicts": [{"finding_id": 1, "decision": "confirm", "reason_code": "true_positive", "confidence": "high",
                           "explanation": "e" * 900}],
             "new_findings": [{"category": "injection", "severity": "high", "confidence": "medium", "file": "server.py", "line": 3,
                               "quote": "q" * 500, "message": "m" * 900} for _ in range(14)],
             "summary": "s" * 3000}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "choices": [{"message": {"content": json.dumps(reply)}}], "usage": {"total_tokens": 10, "cost": 0.001}})))
    conn = connect(str(tmp_path / "t.db"))
    store.ensure_schema(conn)
    out = complete(conn, OpenRouterBackend("m", "k", client=client), Prompt("trust.review", "trust.review@1", "sys", "usr"), ReviewOutput)
    assert len(out.summary) == 1200 and len(out.new_findings) == 10 and out.new_findings[0].quote == "q" * 300
    assert len(out.verdicts[0].explanation) == 600
