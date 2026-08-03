#!/usr/bin/env python3
"""Fail when a public source tree contains private presentation or secrets."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
FORBIDDEN_FILES = {
    "opening.html",
    "catroom_store.py",
    "room_store.py",
    "rhythm_store.py",
    ".env",
    "config.yaml",
}
FORBIDDEN_TEXT = {
    "/Users/lili": "private absolute path",
    "toy.pyrus.uk": "private service URL",
    "Pyruslili/Ombre-Brain/main/docs/assets": "private artwork URL",
}
SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def source_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        yield path


def main() -> int:
    failures: list[str] = []
    if not (ROOT / "dashboard.html").exists():
        failures.append("required file missing: dashboard.html")
    for name in FORBIDDEN_FILES:
        if (ROOT / name).exists():
            failures.append(f"forbidden file: {name}")

    for path in source_files():
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(ROOT)
        for needle, label in FORBIDDEN_TEXT.items():
            if needle in text:
                failures.append(f"{rel}: {label}")
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                failures.append(f"{rel}: possible {label}")

    if failures:
        print("public audit failed:", file=sys.stderr)
        for failure in sorted(set(failures)):
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("public audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
