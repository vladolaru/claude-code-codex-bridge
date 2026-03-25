"""Config scan add/remove/list command handlers."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path

from cc_codex_bridge.config_writer import (
    add_to_string_list,
    read_config_data,
    remove_from_string_list,
    write_config_data,
)


@dataclass(frozen=True)
class ScanCommandResult:
    """Result of a scan add or remove operation."""

    success: bool
    message: str


@dataclass(frozen=True)
class ScanListResult:
    """Result of a scan list operation."""

    paths: tuple[str, ...]
    exclude_paths: tuple[str, ...]


def handle_scan_add(*, pattern: str, config_path: Path) -> ScanCommandResult:
    """Add a scan path pattern to the global config.

    Expands the glob to verify at least one directory matches, then
    stores the *pattern* (not the expanded paths) in ``scan_paths``.
    """
    # Expand the glob to verify it matches at least one directory.
    expanded = str(Path(pattern).expanduser())
    matches = [m for m in glob.glob(expanded) if Path(m).is_dir()]

    if not matches:
        return ScanCommandResult(
            success=False,
            message=f"No directories matched pattern: {pattern}",
        )

    # Read, modify, write.
    data = read_config_data(config_path)
    added = add_to_string_list(data, "scan_paths", pattern)

    if not added:
        return ScanCommandResult(
            success=False,
            message=f"Pattern already in scan_paths: {pattern}",
        )

    write_config_data(config_path, data)

    noun = "directory" if len(matches) == 1 else "directories"
    return ScanCommandResult(
        success=True,
        message=f"Added pattern ({len(matches)} {noun} matched): {pattern}",
    )


def handle_scan_remove(*, pattern: str, config_path: Path) -> ScanCommandResult:
    """Remove a scan path pattern from the global config."""
    data = read_config_data(config_path)
    removed = remove_from_string_list(data, "scan_paths", pattern)

    if not removed:
        return ScanCommandResult(
            success=False,
            message=f"Pattern not found in scan_paths: {pattern}",
        )

    write_config_data(config_path, data)

    return ScanCommandResult(
        success=True,
        message=f"Removed pattern from scan_paths: {pattern}",
    )


def handle_scan_list(*, config_path: Path) -> ScanListResult:
    """List current scan_paths and exclude_paths from the global config."""
    data = read_config_data(config_path)

    scan_paths = data.get("scan_paths", [])
    exclude_paths = data.get("exclude_paths", [])

    # Ensure we return tuples of strings even if config has unexpected types.
    return ScanListResult(
        paths=tuple(str(p) for p in scan_paths),
        exclude_paths=tuple(str(p) for p in exclude_paths),
    )
