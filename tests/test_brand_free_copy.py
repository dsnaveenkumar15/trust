"""Visible page copy never names a third-party vendor or model brand (Hostinger abuse policy: no brand names in what we
publish). The API host in the CSP header and the model ids in option values are code, not copy."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app, model_label

PAGES = {"trust": ["/", "/check", "/methodology"], "blueprints": ["/", "/custom", "/compare"],
         "radar": ["/", "/ask", "/upgrade"]}
BRANDS = re.compile(r"claude|anthropic|opus|sonnet|haiku|openai|zerodha|binance", re.I)


def visible(html: str) -> str:
    html = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S | re.I)
    html = re.sub(r"\svalue=\"[^\"]*\"", "", html)
    return re.sub(r"<[^>]+>", " ", html)


@pytest.mark.parametrize("planet,path", [(p, u) for p, urls in PAGES.items() for u in urls])
def test_page_copy_names_no_vendor(tmp_path, planet, path):
    settings = make_settings(tmp_path)
    if planet not in settings.enabled_planets:
        pytest.skip(f"{planet} not in this repo")
    r = TestClient(create_app(settings), base_url=f"http://{settings.hosts[planet]}").get(path)
    assert r.status_code == 200 and not BRANDS.search(visible(r.text)), BRANDS.search(visible(r.text))


def test_model_label():
    assert [model_label(m) for m in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "x")] == [
        "large model", "mid-size model", "small model", "x"]
