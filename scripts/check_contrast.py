#!/usr/bin/env python3
"""Check WCAG AA contrast for the Agent Tree web UI themes.

Parses the CSS custom properties in agent_tree/web/app.css for the dark theme
(:root) and the light theme ([data-theme="light"], which inherits anything it
does not override), then checks:

  * text pairs       >= 4.5:1  (body text, muted text, links, flag text, button text)
  * graphics pairs   >= 3.0:1  (family hues, flag glyphs, accent, focus ring
                                against every surface they are drawn on)
  * family hues      distinct from each other under deuteranopia/protanopia
                     (simulated, CIE76 delta E >= 20) and in plain lightness/hue

Prints a table and exits 1 if any check fails. Standard library only.

    python3 scripts/check_contrast.py
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parent.parent / "agent_tree" / "web" / "app.css"

SURFACES = ["bg", "surface", "surface-2", "surface-3", "row-hover", "selected"]
# (foreground, [backgrounds], minimum ratio, kind)
TEXT_PAIRS = [
    ("text", SURFACES, 4.5),
    ("text-2", SURFACES, 4.5),
    ("text-muted", SURFACES, 4.5),
    ("accent-text", ["bg", "surface", "surface-2", "row-hover"], 4.5),
    ("alert-text", ["bg", "surface", "surface-2", "surface-3", "selected"], 4.5),
    ("warn-text", ["bg", "surface", "surface-2", "surface-3", "selected"], 4.5),
    ("text-on-accent", ["accent-fill"], 4.5),
]
GRAPHIC_PAIRS = [
    ("fam-anthropic", ["bg", "surface", "surface-2", "selected"], 3.0),
    ("fam-openai", ["bg", "surface", "surface-2", "selected"], 3.0),
    ("fam-other", ["bg", "surface", "surface-2", "selected"], 3.0),
    ("alert", ["bg", "surface", "surface-2", "selected"], 3.0),
    ("warn", ["bg", "surface", "surface-2", "selected"], 3.0),
    ("accent", ["bg", "surface", "surface-2"], 3.0),
    ("focus", SURFACES, 3.0),
    ("spark", ["surface"], 3.0),
]
FAMILY_PAIRS = [("fam-anthropic", "fam-openai"), ("fam-anthropic", "fam-other"), ("fam-openai", "fam-other")]
CVD_MIN_DE = 20.0


def parse_block(css: str, selector_regex: str) -> dict[str, str]:
    m = re.search(selector_regex + r"\s*\{(.*?)\}", css, re.S)
    if not m:
        raise SystemExit(f"could not find block {selector_regex!r} in {CSS}")
    return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;", m.group(1)))


def hex_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(h: str) -> float:
    r, g, b = (lin(c) for c in hex_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# --- colour-vision-deficiency simulation (Machado et al. 2009, severity 1.0) ---
CVD = {
    "deuteranopia": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "protanopia": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
}


def simulate(h: str, kind: str) -> tuple[float, float, float]:
    r, g, b = (lin(c) for c in hex_rgb(h))
    m = CVD[kind]
    return tuple(max(0.0, min(1.0, m[i][0] * r + m[i][1] * g + m[i][2] * b)) for i in range(3))  # type: ignore[return-value]


def lab(rgb_lin: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = rgb_lin
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def delta_e(a: tuple, b: tuple) -> float:
    return math.dist(lab(a), lab(b))


def main() -> int:
    css = CSS.read_text()
    dark = parse_block(css, r":root")
    light = {**dark, **parse_block(css, r'\[data-theme="light"\]')}
    failures = 0
    rows = []
    for theme, tokens in (("dark", dark), ("light", light)):
        for pairs, kind in ((TEXT_PAIRS, "text"), (GRAPHIC_PAIRS, "graphic")):
            for fg, bgs, minimum in pairs:
                for bg in bgs:
                    if fg not in tokens or bg not in tokens:
                        rows.append((theme, kind, fg, bg, "missing", minimum, "FAIL"))
                        failures += 1
                        continue
                    r = ratio(tokens[fg], tokens[bg])
                    ok = r >= minimum
                    failures += not ok
                    rows.append((theme, kind, fg, bg, f"{r:5.2f}", minimum, "ok" if ok else "FAIL"))
        for a, b in FAMILY_PAIRS:
            for cvd in ("normal", "deuteranopia", "protanopia"):
                if cvd == "normal":
                    ca = tuple(lin(c) for c in hex_rgb(tokens[a]))
                    cb = tuple(lin(c) for c in hex_rgb(tokens[b]))
                else:
                    ca, cb = simulate(tokens[a], cvd), simulate(tokens[b], cvd)
                de = delta_e(ca, cb)
                ok = de >= CVD_MIN_DE
                failures += not ok
                rows.append((theme, "cvd:" + cvd[:6], a, b, f"{de:5.1f}", f"dE{CVD_MIN_DE:.0f}", "ok" if ok else "FAIL"))

    w = [6, 12, 16, 12, 6, 6, 4]
    head = ("theme", "check", "foreground", "background", "value", "min", "")
    print("  ".join(h.ljust(w[i]) for i, h in enumerate(head)))
    print("  ".join("-" * x for x in w))
    for r in rows:
        print("  ".join(str(c).ljust(w[i]) for i, c in enumerate(r)))
    print()
    total = len(rows)
    if failures:
        print(f"FAILED: {failures} of {total} checks below WCAG AA / CVD thresholds")
        return 1
    print(f"PASSED: all {total} checks (text >= 4.5:1, graphics >= 3:1, family hues dE >= {CVD_MIN_DE:.0f} incl. deuteranopia/protanopia)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
