"""Bring-your-own-key deep audit. The visitor's browser calls Claude; this site only builds the prompt and
deterministically validates and scores what comes back. The key never reaches this server."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from trust.review import SYSTEM, ReviewOutput, apply_review, build_user_prompt
from trust.rules_code import code_findings
from trust.scoring import score
from web.ratelimit import enforce
from web.security import client_ip
from web.trust_check import MANIFEST_FILE, MAX_MANIFEST_CHARS, manifest_findings, parse_manifest
from web.trust_site import trust_only

BYOK_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5")
UNSUPPORTED_SCHEMA_KEYS = {"maxLength", "minLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
                           "multipleOf", "maxItems", "minItems", "uniqueItems", "default"}
_FILENAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")


def api_schema(node):
    """Pydantic JSON schema -> the subset structured outputs accept. Limits come back on apply, re-validated."""
    if isinstance(node, list):
        return [api_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in UNSUPPORTED_SCHEMA_KEYS:
            continue
        if key in ("properties", "$defs") and isinstance(value, dict):
            out[key] = {name: api_schema(sub) for name, sub in value.items()}
        else:
            out[key] = api_schema(value)
    if out.get("type") == "object" or "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out.get("properties", {}))
    return out


class ByokRequest(BaseModel):
    manifest: str = Field(min_length=2, max_length=MAX_MANIFEST_CHARS)
    code: str = Field(default="", max_length=200_000)
    filename: str = Field(default="server.py", max_length=200)


class ByokApply(ByokRequest):
    output: dict


def _inputs(req: ByokRequest):
    defs, findings, files = manifest_findings(parse_manifest(req.manifest))
    if req.code:
        if not _FILENAME.fullmatch(req.filename) or ".." in req.filename or req.filename == MANIFEST_FILE:
            raise HTTPException(400, "filename must be a simple relative path like server.py")
        files[req.filename] = req.code
        findings = findings + code_findings({req.filename: req.code})
    return defs, findings, files


router = APIRouter(dependencies=[Depends(trust_only)])


@router.post("/api/byok/prompt")
def byok_prompt(request: Request, body: ByokRequest):
    enforce(request.app.state.byok_limiter, client_ip(request, request.app.state.settings.trust_proxy))
    defs, findings, files = _inputs(body)
    return {"system": SYSTEM, "user": build_user_prompt(defs, findings, files, 1),
            "schema": api_schema(ReviewOutput.model_json_schema()), "models": list(BYOK_MODELS),
            "findings": len(findings)}


@router.post("/api/byok/apply")
def byok_apply(request: Request, body: ByokApply):
    enforce(request.app.state.byok_limiter, client_ip(request, request.app.state.settings.trust_proxy))
    _, findings, files = _inputs(body)
    try:
        output = ReviewOutput.model_validate(body.output)
    except ValidationError as exc:
        raise HTTPException(422, f"review output does not match the schema: {exc.errors()[0]['msg']}")
    result = apply_review(findings, output, files)
    scored = score(result.findings)
    return {"grade": scored.grade, "score": scored.total, "categories": scored.categories,
            "findings": [f.to_dict() for f in result.findings], "needs_review": result.needs_review,
            "dropped_new": result.dropped_new}
