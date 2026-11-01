"""Minimal .env loader (no dependency). Real environment variables always win; values are never logged."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"  # the git-prov checkout (editable install)


def load(*paths: Path) -> None:
    for path in paths:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            key, value = key.strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key and value and key not in os.environ:
                os.environ[key] = value
