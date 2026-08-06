"""credentials.env reader with docker-compose-dotenv semantics.

The parse rules replicate compose v2.29 as observed on the NAS: this module is
the widget's source of truth about what the NEXT container restart will load,
so it must agree with compose, warts included (empty value + inline comment
leaks the comment text in as the value).
"""
from __future__ import annotations

import os
import re
import tempfile

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


def set_value(path: str, env_var: str, value: str) -> None:
    """Update one variable in credentials.env, preserving comments, blank
    lines, ordering and every other entry.

    Rewritten atomically (temp file in the same directory + os.replace) so a
    crash mid-write cannot truncate the file the API loads its credentials
    from. A partial credentials.env is worse than a stale one.
    """
    # A newline in value would inject a second, uncommented line below this
    # one, letting one call silently define an arbitrary extra variable (or
    # split an existing line in two). The dotenv format this module mirrors
    # has no escape for that, so refuse rather than write something the
    # reader would parse into more variables than the caller intended.
    if "\n" in value or "\r" in value:
        raise ValueError(f"value for {env_var!r} must not contain a newline")

    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []

    replaced = False
    out: list[str] = []
    for raw in lines:
        m = _LINE.match(raw.strip())
        if m and m.group(1) == env_var and not replaced:
            out.append(f"{env_var}={value}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(f"{env_var}={value}")

    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".credentials.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
        os.replace(tmp, path)
    except BaseException:
        # Never leave a temp file holding a credential behind.
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
