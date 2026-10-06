# tests/test_themes.py
"""Quality gates for every theme pack under web/static/themes/<app>/."""
import re
from pathlib import Path

import pytest

THEMES = Path(__file__).resolve().parent.parent / "web" / "static" / "themes"
PACKS = sorted(p.name for p in THEMES.iterdir() if (p / "theme.css").is_file()) if THEMES.is_dir() else []
COLOURS = ["bg", "surface", "surface-2", "ink", "ink-2", "muted", "rule", "rule-2", "field", "accent", "accent-ink",
           "accent-soft", "ok", "ok-soft", "warn", "warn-soft", "bad", "bad-soft", "mark"]
MODE_FREE = ["sans", "mono", "display", "r-sm", "r-md", "r-lg", "shadow-1", "shadow-2"]
EXTRA = {"radar": ["sig-break", "sig-depr", "sig-cve", "grid"],
         "trust": ["sev-critical", "sev-high", "sev-medium", "sev-low", "sev-high-soft"]}
TEXT_ON = ["ink", "ink-2", "muted", "accent", "ok", "warn", "bad"]          # read as text on bg and surface


def blocks(app: str) -> tuple[dict, dict]:
    css = (THEMES / app / "theme.css").read_text(encoding="utf-8")
    def grab(selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
        assert m, f"{app}: missing block {selector}"
        return dict(re.findall(r"--([\w-]+)\s*:\s*([^;]+);", m.group(1)))
    return grab(f".planet-{app}"), grab(f':root[data-theme="dark"] .planet-{app}')


def luminance(hex_colour: str) -> float:
    h = hex_colour.strip().lstrip("#")
    assert len(h) == 6, f"text colours must be opaque #RRGGBB, got {hex_colour}"
    def channel(c):
        v = int(c, 16) / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (channel(h[i:i + 2]) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("app", PACKS)
def test_every_pack_defines_the_whole_token_contract_in_both_modes(app):
    light, dark = blocks(app)
    need = COLOURS + EXTRA.get(app, [])
    assert not [t for t in need + MODE_FREE if t not in light], "light block incomplete"
    assert not [t for t in need if t not in dark], "dark block incomplete"


@pytest.mark.parametrize("app", PACKS)
def test_text_meets_wcag_aa_in_both_modes(app):
    for mode in blocks(app):
        for token in TEXT_ON + [t for t in EXTRA.get(app, []) if t.startswith(("sig-", "sev-")) and not t.endswith("-soft")]:
            for ground in ("bg", "surface"):
                ratio = contrast(mode[token], mode[ground])
                assert ratio >= 4.5, f"{app}: --{token} on --{ground} is {ratio:.2f}:1"
        assert contrast(mode["accent-ink"], mode["accent"]) >= 4.5, f"{app}: button text on accent"


@pytest.mark.parametrize("app", PACKS)
def test_pack_stays_within_its_budgets(app):
    fonts = sum(p.stat().st_size for p in (THEMES / app / "fonts").glob("*.woff2"))
    assert fonts <= 120_000, f"{app}: fonts {fonts} bytes"
    assert (THEMES / app / "theme.css").stat().st_size <= 40_000
    assert (THEMES / app / "icons.svg").stat().st_size <= 15_000
    css = (THEMES / app / "theme.css").read_text(encoding="utf-8")
    assert css.count("@font-face") == css.count("font-display: swap")
    assert "prefers-reduced-motion" in css or "animation" not in css
    assert not re.search(r"url\((?!\"?/static/)", css), "assets must be self-hosted under /static"


@pytest.mark.skipif(not (THEMES / "radar").is_dir(), reason="no Radar pack in this repository")
def test_new_digest_entries_keep_muted_text_readable():
    # --accent-soft behind --muted text measured 4.0-4.5:1 (below AA); new entries are marked by their badge instead.
    css = (THEMES / "radar" / "theme.css").read_text(encoding="utf-8")
    rule = re.search(r"\.planet-radar \.cluster\.is-new\s*\{([^}]*)\}", css).group(1)
    assert "accent-soft" not in rule


@pytest.mark.skipif(not (THEMES / "trust").is_dir(), reason="no Trust pack in this repository")
def test_trust_grade_badges_meet_aa_in_light_mode():
    light, _ = blocks("trust")
    for ink, ground in (("ok", "ok-soft"), ("sev-medium", "warn-soft"), ("sev-high", "sev-high-soft"), ("sev-critical", "bad-soft")):
        ratio = contrast(light[ink], light[ground])
        assert ratio >= 4.5, f"--{ink} on --{ground} is {ratio:.2f}:1"
