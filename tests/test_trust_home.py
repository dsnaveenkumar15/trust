# tests/test_trust_home.py
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app


def home(tmp_path):
    return TestClient(create_app(make_settings(tmp_path)), base_url="http://trust.localhost").get("/").text


def test_home_is_a_console_with_the_working_checker_and_the_live_example(tmp_path):
    html = home(tmp_path)
    assert 'class="hero trust-hero"' in html and 'id="check-form"' in html and 'name="manifest"' in html
    assert 'id="check-result"' in html and '<script src="/static/trust-check.js' in html
    assert html.index('id="check-form"') < html.index('class="hero-visual"')       # checker left, example right
    assert "Hidden instructions in" in html and "<mark>&lt;IMPORTANT&gt;" in html
    assert "hero-copy" in html and html.count('class="hero ') == 1                # the generic hero is replaced, once


def test_home_is_complete_without_a_published_directory(tmp_path):
    html = home(tmp_path)
    assert html.count('<use href="/static/themes/trust/icons.svg#i-') >= 5          # checker label + four capability cards
    for title in ("Hidden instructions", "Tool shadowing", "Invisible characters", "Risky capabilities"):
        assert f"<h3>{title}</h3>" in html, title
    assert 'class="flow"' in html and html.count("<li>", html.index('class="flow"')) >= 3
    assert "does not list other people" in html and "Published reviews" not in html


def test_score_tables_sit_side_by_side_with_aligned_numbers(tmp_path):
    import re
    html = TestClient(create_app(make_settings(tmp_path)), base_url="http://trust.localhost").get("/methodology").text
    block = html[html.index('<div class="score-tables">'):html.index("</div>", html.index('<div class="score-tables">'))]
    assert block.count('<table class="kv">') == 3
    values = re.findall(r'<td class="([^"]*)">[0-9.]+</td>', block)
    assert values and all("num" in cls for cls in values)          # every number is right-aligned in its own column
    assert block.count("<colgroup>") == 3                           # fixed column widths, so the three tables line up
