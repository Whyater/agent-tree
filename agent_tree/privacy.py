"""Privacy guards shared by every parser.

Rules enforced here:
- no stored string longer than ``TEXT_LIMIT`` (160) characters;
- anything that looks like a credential is replaced with ``[redacted]``;
- metadata-only mode stores no free text at all;
- secret-looking files are never opened.
"""

from __future__ import annotations

import re
from pathlib import Path

TEXT_LIMIT = 160

_KEY_PATTERNS = [
    r"sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{16,}",          # Anthropic / OpenAI keys
    r"(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{16,}",  # GitHub tokens
    r"xox[abprs]-[A-Za-z0-9\-]{10,}",                    # Slack
    r"AKIA[0-9A-Z]{16}",                                 # AWS access key id
    r"AIza[0-9A-Za-z_\-]{30,}",                          # Google API key
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}",  # JWT
    r"(?i:bearer)\s+[A-Za-z0-9._\-~+/]{16,}=*",
    r"(?i:api[_-]?key|secret|token|password|passwd)\s*[:=]\s*\S{8,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
]
KEY_RE = re.compile("|".join(f"(?:{p})" for p in _KEY_PATTERNS))
_WS_RE = re.compile(r"\s+")

# Path fragments that must never be opened, even if a scan root contains them.
SECRET_DIR_NAMES = {"secrets", ".secrets", "keychains"}
SECRET_NAME_RE = re.compile(r"(?i)^(\.env(\..*)?|auth.*\.json|.*tokens?\.json|.*credential.*|.*\.pem|.*\.key|.*\.p12)$")


def looks_secret(text: str) -> bool:
    return bool(KEY_RE.search(text))


def redact(text: str) -> str:
    return KEY_RE.sub("[redacted]", text)


def clean_text(value: object, metadata_only: bool = False, limit: int = TEXT_LIMIT) -> str | None:
    """Normalize a free-text field for storage: collapse whitespace, redact, truncate.

    Returns None when metadata-only mode is on or the value is empty.
    Redaction happens before truncation so a key can't be split across the cut.
    """
    if metadata_only or value is None:
        return None
    text = _WS_RE.sub(" ", str(value)).strip()
    if not text:
        return None
    text = redact(text)
    limit = min(limit, TEXT_LIMIT)
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def is_secret_path(path: Path | str) -> bool:
    p = Path(path)
    if any(part.lower() in SECRET_DIR_NAMES for part in p.parts):
        return True
    return bool(SECRET_NAME_RE.match(p.name))
