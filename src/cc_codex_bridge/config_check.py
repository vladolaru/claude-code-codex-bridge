"""Config validation logic for the bridge config check command.

Pure functions with no CLI concerns. Used by both ``config check``
and the doctor integration.
"""

from __future__ import annotations

import glob as glob_mod
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path


# Known top-level keys in the global config.toml.
_KNOWN_GLOBAL_KEYS: frozenset[str] = frozenset(
    {"scan_paths", "exclude_paths", "log", "exclude"}
)

# Keys that are only valid in the global config, not in project config.
_GLOBAL_ONLY_KEYS: frozenset[str] = frozenset(
    {"scan_paths", "exclude_paths", "log"}
)


@dataclass(frozen=True)
class CheckResult:
    """One validation check result."""

    label: str
    passed: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Global config validation
# ---------------------------------------------------------------------------


def check_global_config(
    config_path: Path,
    *,
    bridge_home: Path,
) -> list[CheckResult]:
    """Validate the global bridge config file.

    Returns a list of :class:`CheckResult` entries. If the file does not
    exist, returns a single pass result (using defaults). If TOML parsing
    fails, returns early with a single failure (further checks are
    impossible).
    """
    if not config_path.exists():
        return [
            CheckResult(
                label="global_config",
                passed=True,
                message="File not found — using defaults",
            )
        ]

    # --- TOML well-formedness ---
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        return [
            CheckResult(
                label="TOML well-formed",
                passed=False,
                message=f"Parse error: {exc}",
            )
        ]

    results: list[CheckResult] = [
        CheckResult(label="TOML well-formed", passed=True),
    ]

    # --- scan_paths expansion check ---
    if "scan_paths" in data:
        scan_paths = data["scan_paths"]
        if isinstance(scan_paths, list):
            results.append(_check_scan_paths(scan_paths))

    # --- Unknown top-level keys ---
    unknown = sorted(set(data.keys()) - _KNOWN_GLOBAL_KEYS)
    if unknown:
        results.append(
            CheckResult(
                label="unknown keys",
                passed=False,
                message=f"Unknown top-level keys: {', '.join(unknown)}",
            )
        )

    return results


def _check_scan_paths(scan_paths: list[object]) -> CheckResult:
    """Validate that each scan path expands to at least one existing directory."""
    string_paths = [p for p in scan_paths if isinstance(p, str)]
    if not string_paths:
        return CheckResult(
            label="scan_paths",
            passed=True,
            message="0 paths configured",
        )

    missing: list[str] = []
    resolved_count = 0

    for pattern in string_paths:
        expanded = str(Path(pattern).expanduser())
        matches = glob_mod.glob(expanded)
        dirs = [m for m in matches if Path(m).is_dir()]
        if dirs:
            resolved_count += len(dirs)
        else:
            missing.append(pattern)

    if missing:
        return CheckResult(
            label="scan_paths",
            passed=False,
            message=(
                f"{len(string_paths)} paths configured, "
                f"{len(missing)} not found: {', '.join(missing)}"
            ),
        )

    return CheckResult(
        label="scan_paths",
        passed=True,
        message=f"{len(string_paths)} paths, all resolve",
    )


# ---------------------------------------------------------------------------
# Project config validation
# ---------------------------------------------------------------------------


def check_project_config(config_path: Path) -> list[CheckResult]:
    """Validate a project-level bridge config file.

    Returns a list of :class:`CheckResult` entries. If the file does not
    exist, returns a single pass result (no project overrides). If TOML
    parsing fails, returns early with a single failure.

    Global-only keys (``scan_paths``, ``exclude_paths``, ``[log]``) are
    rejected — they belong in the global config only.
    """
    if not config_path.exists():
        return [
            CheckResult(
                label="project_config",
                passed=True,
                message="No project overrides",
            )
        ]

    # --- TOML well-formedness ---
    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        return [
            CheckResult(
                label="TOML well-formed",
                passed=False,
                message=f"Parse error: {exc}",
            )
        ]

    results: list[CheckResult] = [
        CheckResult(label="TOML well-formed", passed=True),
    ]

    # --- Reject global-only keys ---
    found_global_only = sorted(set(data.keys()) & _GLOBAL_ONLY_KEYS)
    if found_global_only:
        results.append(
            CheckResult(
                label="global-only keys",
                passed=False,
                message=(
                    f"Keys only valid in global config: "
                    f"{', '.join(found_global_only)}; "
                    f"move to ~/.cc-codex-bridge/config.toml"
                ),
            )
        )

    return results


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def format_check_report(config_label: str, results: list[CheckResult]) -> str:
    """Format check results as a human-readable report.

    Example output::

        Checking global config...
          ✓ TOML well-formed
          ✓ scan_paths: 2 paths, all resolve
          ✗ exclude.plugins: "foo/bar" — not found
    """
    lines: list[str] = [f"Checking {config_label}..."]

    for result in results:
        icon = "\u2713" if result.passed else "\u2717"
        if result.message:
            lines.append(f"  {icon} {result.label}: {result.message}")
        else:
            lines.append(f"  {icon} {result.label}")

    return "\n".join(lines)


def format_check_report_json(
    global_results: list[CheckResult],
    project_results: list[CheckResult],
) -> str:
    """Format check results as JSON.

    Returns a JSON string with ``{"global": [...], "project": [...]}``,
    where each entry has ``label``, ``passed``, and ``message`` fields.
    """
    payload = {
        "global": [
            {
                "label": r.label,
                "passed": r.passed,
                "message": r.message,
            }
            for r in global_results
        ],
        "project": [
            {
                "label": r.label,
                "passed": r.passed,
                "message": r.message,
            }
            for r in project_results
        ],
    }
    return json.dumps(payload, indent=2)
