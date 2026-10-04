# Accessibility (WCAG 2.2 AA)

The web UI targets WCAG 2.2 level AA. Two ratchets in the Code rules gate keep
it from regressing; their violation counts may only go down.

## Structure: `tests/test_accessibility.js`

Loads `templates/index.html` into BrowserShim and checks:

| Criterion | Rule |
|---|---|
| 4.1.2 | Every `button` and `a` has an accessible name (text, `aria-label`, `aria-labelledby` or `title`) |
| 1.3.1 / 3.3.2 | Every form field has a `<label for>`, a wrapping `<label>`, `aria-label` or `aria-labelledby`; a placeholder is not a label |
| 4.1.2 | Every modal has `role="dialog"`, `aria-modal="true"` and `aria-labelledby` pointing to an existing id |
| 4.1.1 | Ids are unique |
| 1.1.1 | Every `img` has `alt` (empty for decorative images) |
| 3.1.1 | The document declares `lang` |

Known violations: `#msg-input` and `#response-mode-select` have no label, and
`#alice-system-status` lacks dialog semantics.

## Contrast: `tests/test_color_contrast.py`

Computes the WCAG contrast ratio of text/background token pairs in all four
themes of `static/style.css` and requires 4.5:1 for body text. Nine pairs are
below it today (secondary text, header, user bubble and error text in the
light, dark and dim themes); `high-contrast` passes everywhere.

## Fixing a violation

Fix one component per pull request, then lower `BASELINE` in
`test_accessibility.js` or remove the pair from `KNOWN_LOW_CONTRAST` in the
same change.
