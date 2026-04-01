"""Config show formatting with source attribution.

Produces human-readable and JSON output for ``config show``, attributing
each value to its source: ``(default)``, ``(global)``, or ``(project)``.
"""

from __future__ import annotations

import json
from typing import Any

from cc_codex_bridge.config import BridgeConfig, DEFAULT_LOG_RETENTION_DAYS
from cc_codex_bridge.exclusions import SyncExclusions


# Exclusion category names in display order.
_EXCLUSION_KINDS: tuple[str, ...] = ("plugins", "skills", "agents", "commands", "mcp_servers")


def format_config_show(
    *,
    global_config: BridgeConfig,
    project_exclusions: SyncExclusions | None,
    scan_paths: tuple[str, ...],
    exclude_paths: tuple[str, ...],
    scope: str,  # "global", "project", or "merged"
) -> str:
    """Format config display for human-readable terminal output.

    Each value line includes a ``(source)`` attribution suffix:

    - ``(default)`` — built-in default value
    - ``(global)`` — set in global ``config.toml``
    - ``(project)`` — set in project ``.codex/bridge.toml``
    - ``(none)`` — no entries for that section
    """
    lines: list[str] = []

    # --- Log retention ---
    retention = global_config.log_retention_days
    if retention == DEFAULT_LOG_RETENTION_DAYS:
        source = "default"
    else:
        source = "global"
    lines.append(f"Log retention:    {retention} days ({source})")
    lines.append("")

    # --- Scan paths (always global) ---
    lines.append("Scan paths:")
    if scan_paths:
        for path in scan_paths:
            lines.append(f"  {path:<24}(global)")
    else:
        lines.append("  (none)")
    lines.append("")

    # --- Exclude paths (always global) ---
    lines.append("Exclude paths:")
    if exclude_paths:
        for path in exclude_paths:
            lines.append(f"  {path:<24}(global)")
    else:
        lines.append("  (none)")
    lines.append("")

    # --- Exclusion categories ---
    for kind in _EXCLUSION_KINDS:
        entries = _build_attributed_entries(
            kind=kind,
            global_config=global_config,
            project_exclusions=project_exclusions,
            scope=scope,
        )
        label = f"Exclude {kind}:"
        lines.append(label)
        if entries:
            for value, source in entries:
                lines.append(f"  {value:<24}({source})")
        else:
            lines.append("  (none)")
        lines.append("")

    # Remove trailing blank line
    while lines and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


def format_config_show_json(
    *,
    global_config: BridgeConfig,
    project_exclusions: SyncExclusions | None,
    scan_paths: tuple[str, ...],
    exclude_paths: tuple[str, ...],
    scope: str,
) -> str:
    """Format config display as a JSON string with source attribution."""
    retention = global_config.log_retention_days
    retention_source = (
        "default" if retention == DEFAULT_LOG_RETENTION_DAYS else "global"
    )

    exclude_obj: dict[str, list[dict[str, str]]] = {}
    for kind in _EXCLUSION_KINDS:
        entries = _build_attributed_entries(
            kind=kind,
            global_config=global_config,
            project_exclusions=project_exclusions,
            scope=scope,
        )
        exclude_obj[kind] = [
            {"value": value, "source": source} for value, source in entries
        ]

    payload: dict[str, Any] = {
        "scope": scope,
        "log_retention_days": {"value": retention, "source": retention_source},
        "scan_paths": [
            {"value": p, "source": "global"} for p in scan_paths
        ],
        "exclude_paths": [
            {"value": p, "source": "global"} for p in exclude_paths
        ],
        "exclude": exclude_obj,
    }

    return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_attributed_entries(
    *,
    kind: str,
    global_config: BridgeConfig,
    project_exclusions: SyncExclusions | None,
    scope: str,
) -> list[tuple[str, str]]:
    """Build a list of ``(value, source)`` pairs for one exclusion category.

    Handles scope filtering and deduplication:

    - ``scope="global"`` — only global entries
    - ``scope="project"`` — only project entries
    - ``scope="merged"`` — union of both, deduplicated (global wins on overlap)
    """
    global_values = set(getattr(global_config.exclude, kind))
    project_values = set(
        getattr(project_exclusions, kind) if project_exclusions else ()
    )

    entries: list[tuple[str, str]] = []

    if scope == "global":
        for v in sorted(global_values):
            entries.append((v, "global"))
    elif scope == "project":
        for v in sorted(project_values):
            entries.append((v, "project"))
    else:
        # "merged": global first, then project-only entries
        seen: set[str] = set()
        for v in sorted(global_values):
            entries.append((v, "global"))
            seen.add(v)
        for v in sorted(project_values):
            if v not in seen:
                entries.append((v, "project"))

    return entries
