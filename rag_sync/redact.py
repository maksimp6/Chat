"""What leaves the device is masked: secrets, tokens and e-mail addresses; credential files are not read.

Moved from scripts/rag_ingest_via_app.py (mask) and scripts/semantic_pipeline.py
(_repository_path_is_sensitive). Nothing is dropped: a masked value is replaced in place.
"""

from __future__ import annotations

import hashlib
import re

JSON_SECRET = re.compile(
    r'(?i)(["\'](?:password|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
    r'private[_-]?key|secret[_-]?key|token|secret|credential)["\']\s*:\s*)'
    r'(?P<quote>["\'])(?P<value>[^"\'\r\n]{8,})(?P=quote)'
)
AUTH_SECRET = re.compile(
    r"(?i)(\bauthorization\s*[\"']?\s*[:=]\s*[\"']?\s*(?:api-key|basic|bearer)\s+)[A-Za-z0-9._~+/=-]{8,}"
)
TOKEN_SHAPE = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"AQVN[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_-]{35}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PLACEHOLDERS = ("${{", "${", "$", "your_", "changeme", "placeholder", "[redacted]")


def mask(text: str) -> tuple[str, int]:
    """The text with secrets and e-mail addresses masked, and how many were masked."""
    count = 0

    def secret(match: re.Match) -> str:
        nonlocal count
        if match.group("value").casefold().startswith(_PLACEHOLDERS):
            return match.group(0)
        count += 1
        return (
            match.group(1) + match.group("quote") + "[REDACTED]" + match.group("quote")
        )

    def auth(match: re.Match) -> str:
        nonlocal count
        count += 1
        return match.group(1) + "[REDACTED]"

    def shape(_match: re.Match) -> str:
        nonlocal count
        count += 1
        return "[REDACTED]"

    def email(match: re.Match) -> str:
        nonlocal count
        count += 1
        return (
            "[email:"
            + hashlib.sha256(match.group().casefold().encode()).hexdigest()[:16]
            + "]"
        )

    text = JSON_SECRET.sub(secret, text)
    text = AUTH_SECRET.sub(auth, text)
    text = TOKEN_SHAPE.sub(shape, text)
    return EMAIL.sub(email, text), count


def path_is_sensitive(path: str) -> bool:
    """A credential file: its contents are never read."""
    name = path.rsplit("/", 1)[-1].lower()
    if name in {
        ".env",
        ".npmrc",
        ".pypirc",
        "secrets.yaml",
        "secrets.yml",
        "secrets.json",
        "credentials.yaml",
        "credentials.yml",
        "credentials.json",
        "auth.json",
        "private.key",
        "id_rsa",
        "id_ed25519",
    }:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if any(word in name for word in ("secret", "credential")) and name.endswith(
        (".yaml", ".yml", ".json", ".toml", ".ini", ".cfg")
    ):
        return True
    return name.endswith((".pem", ".key", ".p12", ".pfx", ".jks"))
