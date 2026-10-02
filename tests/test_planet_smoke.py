"""What every app repo must do, whichever planets its files contain: serve its own planet(s), 404 everything else, and not
even import the planets it does not have. This file is copied into each split repo unchanged."""
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.webutil import make_settings
from web.app import create_app
from web.config import ALL_PLANETS, Settings
from web.planets import PLANETS

ROOT = Path(__file__).resolve().parent.parent
AUTH = ("owner", "correct-horse-battery-staple")


def present() -> tuple:
    return Settings.detect_planets()


def client(settings, planet):
    return TestClient(create_app(settings), base_url=f"http://{settings.hosts[planet]}")


@pytest.mark.parametrize("planet", present())
def test_each_present_planet_renders_its_home_and_healthz(tmp_path, planet):
    c = client(make_settings(tmp_path), planet)
    assert c.get("/healthz").json() == {"ok": True}
    r = c.get("/")
    assert r.status_code == 200 and f"<title>{PLANETS[planet]['name']}" in r.text and f'class="planet-{planet}"' in r.text


@pytest.mark.parametrize("planet", present())
def test_every_page_says_it_is_independent_and_unaffiliated(tmp_path, planet):
    r = client(make_settings(tmp_path), planet).get("/")
    assert "Not affiliated with or endorsed by any company or project mentioned here" in r.text


@pytest.mark.parametrize("planet", present())
def test_home_has_the_title_block_and_every_page_names_its_author(tmp_path, planet):
    c = client(make_settings(tmp_path), planet)
    home, other = c.get("/"), c.get("/no-such-page")
    assert 'class="titleblock"' in home.text and PLANETS[planet]["action"][1] in home.text
    assert "built and run by" in home.text and 'class="titleblock"' not in other.text


def test_unknown_hosts_are_404(tmp_path):
    c = TestClient(create_app(make_settings(tmp_path)), base_url="http://not-ours.example")
    assert c.get("/").status_code == 404 and c.get("/healthz").status_code == 200


def test_the_planets_are_detected_from_the_modules_present():
    assert set(present()) <= set(ALL_PLANETS) and present()
    assert Settings.from_env({"PLANETS": "radar,trust"}).enabled_planets == ("trust", "radar")
    assert Settings.from_env({}).enabled_planets == present()
    with pytest.raises(ValueError):
        Settings.from_env({"PLANETS": "radar,nope"})


@pytest.mark.skipif(len(present()) < 2, reason="needs a repo with several planets")
def test_only_the_enabled_planets_are_served(tmp_path):
    only = present()[0]
    settings = make_settings(tmp_path, planets=(only,))
    for planet in present():
        r = TestClient(create_app(settings), base_url=f"http://{settings.hosts[planet]}").get("/")
        assert (r.status_code == 200) is (planet == only), planet


@pytest.mark.skipif(len(present()) < 2, reason="needs a repo with several planets")
def test_a_disabled_planet_is_not_even_imported():
    only, other = present()[0], present()[-1]
    code = ("import sys\nfrom tests.webutil import make_settings\nfrom web.app import create_app\nimport tempfile, pathlib\n"
            f"create_app(make_settings(pathlib.Path(tempfile.mkdtemp()), planets=({only!r},)))\n"
            f"bad = [m for m in sys.modules if m.split('.')[0] == {other!r} or m.startswith('web.{other}_')]\n"
            "assert not bad, bad\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-600:]


@pytest.mark.parametrize("planet", present())
def test_admin_shows_only_the_enabled_planets_sections(tmp_path, planet):
    settings = make_settings(tmp_path, admin_user="owner", admin_password="correct-horse-battery-staple", planets=(planet,))
    html = client(settings, planet).get("/admin", auth=AUTH).text
    assert "Claude quota" in html and "Review queue" in html and "Dead jobs" in html
    assert ("<h2>Radar</h2>" in html) is (planet == "radar") and ("Mark a cluster as updated" in html) is (planet == "radar")
