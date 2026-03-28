"""Surgical TOML config editing for MCP server entries in Codex config.toml.

Uses ``tomlkit`` for style-preserving (comment-preserving) read-modify-write
of TOML documents.  Individual MCP server entries can be added, updated, or
removed without disturbing other content in the file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import tomlkit
import tomlkit.items


def read_codex_config(path: Path) -> tomlkit.TOMLDocument:
    """Read and parse a Codex config.toml, or return empty doc if missing."""
    if not path.exists():
        return tomlkit.document()
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return tomlkit.document()
    return tomlkit.parse(text)


def write_codex_config(path: Path, doc: tomlkit.TOMLDocument) -> None:
    """Write a TOML document atomically (temp file + rename).  Creates parent dirs.

    If the serialized content is empty after stripping whitespace, remove the
    file instead of writing an empty file.
    """
    content = tomlkit.dumps(doc)
    if not content.strip():
        # Nothing meaningful to write — clean up file if it exists.
        path.unlink(missing_ok=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".toml.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def apply_mcp_changes(
    doc: tomlkit.TOMLDocument,
    *,
    desired: dict[str, dict],
    owned: set[str],
) -> dict[str, list[str]]:
    """Apply MCP server changes to a parsed TOML document.

    Mutates *doc* in place and returns a summary of changes made::

        {"added": [...], "updated": [...], "removed": [...]}

    Ownership rules:

    - Servers in *owned* that are NOT in *desired* are removed.
    - Servers in *desired* that already exist and ARE in *owned* are replaced.
    - Servers in *desired* that already exist but are NOT in *owned* are
      skipped (user-authored — never touch).
    - Servers in *desired* that do not exist are added.
    """
    added: list[str] = []
    updated: list[str] = []
    removed: list[str] = []

    # Early exit: nothing to do.
    if not desired and not owned:
        return {"added": added, "updated": updated, "removed": removed}

    # Ensure the mcp_servers super table exists when we have servers to add.
    mcp_section: tomlkit.items.Table | None = doc.get("mcp_servers")

    # Step 1: remove owned servers no longer desired.
    for name in sorted(owned):
        if name in desired:
            continue
        if mcp_section is not None and name in mcp_section:
            del mcp_section[name]
            removed.append(name)

    # Step 2: add or update desired servers.
    for name, table_dict in sorted(desired.items()):
        if mcp_section is not None and name in mcp_section:
            if name in owned:
                # Replace owned server.
                mcp_section[name] = _dict_to_toml_table(table_dict)
                updated.append(name)
            # else: user-authored — skip.
        else:
            # New server — ensure mcp_servers section exists.
            if mcp_section is None:
                mcp_section = tomlkit.table(is_super_table=True)
                doc.add("mcp_servers", mcp_section)
            mcp_section.add(name, _dict_to_toml_table(table_dict))
            added.append(name)

    # Step 3: clean up empty mcp_servers section.
    if mcp_section is not None and len(mcp_section) == 0:
        del doc["mcp_servers"]

    return {"added": added, "updated": updated, "removed": removed}


def hash_mcp_server_table(table_dict: dict) -> str:
    """Compute a SHA-256 content hash for an MCP server table dict.

    Uses deterministic JSON serialization (sorted keys, compact separators)
    so that logically equivalent dicts always produce the same hash.

    Returns a string in the form ``sha256:<hex>``.
    """
    canonical = json.dumps(table_dict, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _dict_to_toml_table(d: dict) -> tomlkit.items.Table:
    """Convert a plain dict to a ``tomlkit.items.Table``.

    Nested dicts (e.g. ``env``) are converted to sub-tables.  All other
    values are added as-is (tomlkit handles strings, lists, bools, etc.).
    """
    table = tomlkit.table()
    for key, value in d.items():
        if isinstance(value, dict):
            table.add(key, _dict_to_toml_table(value))
        else:
            table.add(key, value)
    return table
