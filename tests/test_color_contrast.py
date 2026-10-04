"""WCAG 2.2 AA contrast (1.4.3) for theme tokens in static/style.css."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "static" / "style.css"
MIN_TEXT_CONTRAST = 4.5

# (foreground token, background token) pairs the UI renders as body text.
TEXT_PAIRS = (
    ("--text-main", "--bg-app"),
    ("--text-main", "--bg-sidebar"),
    ("--text-main", "--bg-input"),
    ("--text-header", "--bg-header"),
    ("--text-bubble-user", "--bg-bubble-user"),
    ("--text-bubble-bot", "--bg-bubble-bot"),
    ("--text-secondary", "--bg-app"),
    ("--text-secondary", "--bg-sidebar"),
    ("--danger", "--bg-bubble-bot"),
)

# Failing (theme, foreground, background) pairs; may only shrink.
KNOWN_LOW_CONTRAST = {
    ("light", "--text-secondary", "--bg-app"),
    ("light", "--text-secondary", "--bg-sidebar"),
    ("light", "--text-header", "--bg-header"),
    ("light", "--text-bubble-user", "--bg-bubble-user"),
    ("light", "--danger", "--bg-bubble-bot"),
    ("dark", "--text-bubble-user", "--bg-bubble-user"),
    ("dark", "--danger", "--bg-bubble-bot"),
    ("dim", "--text-bubble-user", "--bg-bubble-user"),
    ("dim", "--danger", "--bg-bubble-bot"),
}

THEME_BLOCKS = {
    "light": r":root\s*\{",
    "dark": r'\[data-theme="dark"\]\s*\{',
    "dim": r'\[data-theme="dim"\]\s*\{',
    "high-contrast": r'\[data-theme="high-contrast"\]\s*\{',
}


def _theme_tokens():
    css = STYLE.read_text(encoding="utf-8")
    themes = {}
    for theme, opener in THEME_BLOCKS.items():
        match = re.search(opener + r"(.*?)\}", css, re.S)
        assert match, f"theme block not found: {theme}"
        themes[theme] = dict(re.findall(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\b", match.group(1)))
    return themes


def _luminance(hex_color):
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground, background):
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _failing_pairs():
    failing = {}
    for theme, tokens in _theme_tokens().items():
        for foreground, background in TEXT_PAIRS:
            ratio = contrast_ratio(tokens[foreground], tokens[background])
            if ratio < MIN_TEXT_CONTRAST:
                failing[(theme, foreground, background)] = round(ratio, 2)
    return failing


def test_contrast_formula_matches_wcag_reference_values():
    assert round(contrast_ratio("#000000", "#ffffff"), 1) == 21.0
    assert round(contrast_ratio("#777777", "#ffffff"), 2) == 4.48


def test_theme_text_contrast_only_improves():
    failing = _failing_pairs()
    new = {pair: ratio for pair, ratio in failing.items() if pair not in KNOWN_LOW_CONTRAST}
    assert not new, f"text below {MIN_TEXT_CONTRAST}:1 contrast: {new}"
    fixed = sorted(KNOWN_LOW_CONTRAST - set(failing))
    assert not fixed, f"remove fixed pairs from KNOWN_LOW_CONTRAST: {fixed}"
