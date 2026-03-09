"""Shared UTF-8 text loading helpers for user-facing runtime files."""

from __future__ import annotations

from pathlib import Path
from typing import TypeVar


E = TypeVar("E", bound=Exception)


def read_utf8_text(path: Path, *, label: str, error_type: type[E]) -> str:
    """Read one UTF-8 text file and raise a typed user-facing error on decode failure."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise error_type(f"Unable to decode {label} as UTF-8: {path}") from exc
