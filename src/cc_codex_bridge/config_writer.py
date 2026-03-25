"""TOML config read-modify-write helpers.

Uses ``tomllib`` (stdlib, Python 3.11+) for reading and ``tomli_w`` for
writing, giving lossless roundtrip fidelity for the subset of TOML that
bridge config files use.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w


def read_config_data(config_path: Path) -> dict:
    """Read a TOML config file, returning empty dict if missing."""
    if not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def write_config_data(config_path: Path, data: dict) -> None:
    """Write *data* as TOML to *config_path* atomically.  Creates parent dirs.

    Writes to a temporary file first, then atomically replaces the target
    to avoid leaving a truncated/corrupt file on interrupted writes.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = config_path.with_suffix(".toml.tmp")
    try:
        tmp_path.write_bytes(tomli_w.dumps(data).encode())
        os.replace(tmp_path, config_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def add_to_string_list(data: dict, key: str, value: str) -> bool:
    """Add *value* to a top-level list stored under *key*.

    Returns ``True`` if the value was added, ``False`` if it was already
    present (duplicate).
    """
    lst: list[str] = data.setdefault(key, [])
    if value in lst:
        return False
    lst.append(value)
    return True


def remove_from_string_list(data: dict, key: str, value: str) -> bool:
    """Remove *value* from a top-level list stored under *key*.

    Returns ``True`` if the value was removed, ``False`` if the key did
    not exist or the value was not found.
    """
    lst: list[str] | None = data.get(key)
    if lst is None or value not in lst:
        return False
    lst.remove(value)
    return True


def set_nested_value(data: dict, keys: list[str], value: object) -> None:
    """Set *value* at a nested key path, creating intermediate dicts.

    ``set_nested_value(d, ["a", "b", "c"], 1)`` is equivalent to
    ``d["a"]["b"]["c"] = 1``, creating ``d["a"]`` and ``d["a"]["b"]``
    as empty dicts if they do not already exist.
    """
    current = data
    for k in keys[:-1]:
        current = current.setdefault(k, {})
    current[keys[-1]] = value
