"""credentials.env reader with docker-compose-dotenv semantics.

The parse rules replicate compose v2.29 as observed on the NAS: this module is
the widget's source of truth about what the NEXT container restart will load,
so it must agree with compose, warts included (empty value + inline comment
leaks the comment text in as the value).
"""
from __future__ import annotations

import re

_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def parse_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if not value.startswith("#"):
            # Inline comment after a real value is stripped; '#' with no
            # preceding whitespace is part of the value.
            cut = value.find(" #")
            if cut != -1:
                value = value[:cut].rstrip()
        out[key] = value
    return out


def load(path: str) -> dict[str, str] | None:
    try:
        with open(path, encoding="utf-8") as f:
            return parse_text(f.read())
    except OSError:
        return None


def load_with_warnings(path: str) -> tuple[dict[str, str] | None, list[str]]:
    """Like `load`, but also reports malformed lines (non-blank, non-comment,
    not matching KEY=VALUE) as ["line N", ...] instead of silently skipping
    them. Only the line number is reported — the line text could contain a
    partial secret, so it is never included."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None, []

    out: dict[str, str] = {}
    warnings: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            warnings.append(f"line {lineno}")
            continue
        key, value = m.group(1), m.group(2).strip()
        if not value.startswith("#"):
            cut = value.find(" #")
            if cut != -1:
                value = value[:cut].rstrip()
        out[key] = value
    return out, warnings
