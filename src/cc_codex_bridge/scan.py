"""Scan config loading and bulk project discovery."""

from __future__ import annotations

import glob
from dataclasses import dataclass
from pathlib import Path
import tomllib

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.text import read_utf8_text


SCAN_CONFIG_FILENAME = "config.toml"


# ---------------------------------------------------------------------------
# Config loading (Task 1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanConfig:
    """Parsed scan configuration from bridge home config.toml."""

    scan_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()


def load_scan_config(bridge_home: Path) -> ScanConfig:
    """Load scan configuration from ``bridge_home/config.toml``.

    Returns an empty :class:`ScanConfig` when the file does not exist.
    Raises :class:`ReconcileError` on malformed TOML or invalid field types.
    Unknown keys are silently ignored for forward compatibility.
    """
    config_path = bridge_home / SCAN_CONFIG_FILENAME
    if not config_path.exists():
        return ScanConfig()

    try:
        payload = tomllib.loads(
            read_utf8_text(config_path, label="scan config", error_type=ReconcileError)
        )
    except tomllib.TOMLDecodeError as exc:
        raise ReconcileError(f"Invalid TOML in scan config: {config_path}") from exc

    return ScanConfig(
        scan_paths=tuple(_read_string_list(payload, "scan_paths", config_path)),
        exclude_paths=tuple(_read_string_list(payload, "exclude_paths", config_path)),
    )


def _read_string_list(table: dict[str, object], key: str, config_path: Path) -> list[str]:
    """Read one string-list key from the config payload."""
    raw = table.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ReconcileError(
            f"`scan.{key}` must be a list of strings in: {config_path}"
        )
    return [item.strip() for item in raw if item.strip()]


# ---------------------------------------------------------------------------
# Glob expansion (Task 2)
# ---------------------------------------------------------------------------


def expand_scan_globs(
    *,
    scan_paths: tuple[str, ...],
    exclude_paths: tuple[str, ...],
) -> list[Path]:
    """Expand scan globs and apply exclude filtering.

    - Expand ``~`` via :meth:`Path.expanduser`.
    - Use :func:`glob.glob` for pattern expansion.
    - Collect only directories (not files).
    - Convert to absolute paths (symlinks are preserved, not resolved).
    - Apply *exclude_paths* (also glob-expanded) to remove matches.
    - Return a sorted, deduplicated list.
    """
    # Expand scan patterns into candidate directories.
    # Keep symlinks unresolved so filter_scan_candidates can classify them.
    seen: set[Path] = set()
    candidates: list[Path] = []
    for pattern in scan_paths:
        expanded = str(Path(pattern).expanduser())
        for match in glob.glob(expanded):
            p = Path(match)
            if not p.is_dir():
                continue
            # Use absolute path but do NOT resolve symlinks yet.
            key = Path(p).absolute()
            if key not in seen:
                seen.add(key)
                candidates.append(key)

    # Expand exclude patterns into a set of absolute paths to remove.
    excludes: set[Path] = set()
    for pattern in exclude_paths:
        expanded = str(Path(pattern).expanduser())
        for match in glob.glob(expanded):
            excludes.add(Path(match).absolute())

    return sorted(p for p in candidates if p not in excludes)


# ---------------------------------------------------------------------------
# Structural filtering (Task 3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScanCandidate:
    """One candidate directory evaluated during scanning."""

    path: Path
    status: str  # "bridgeable", "not_bridgeable", "filtered"
    filter_reason: str | None = None


@dataclass(frozen=True)
class ScanResult:
    """Categorized scan output."""

    bridgeable: tuple[Path, ...] = ()
    not_bridgeable: tuple[ScanCandidate, ...] = ()
    filtered: tuple[ScanCandidate, ...] = ()


_EMPTY_SCAN_RESULT = ScanResult()


def filter_scan_candidates(candidates: list[Path]) -> ScanResult:
    """Apply structural filters to candidate directories.

    Filters applied in order:

    1. Reject symlinks → filter_reason="symlink"
    2. Must have ``.git`` directory (NOT file) → no ``.git`` = "no_git",
       ``.git`` is a file = "not_git_root"
    3. Must have Claude presence (``AGENTS.md``, ``CLAUDE.md``, or ``.claude/``)
       → "no_claude"
    4. Has Claude presence but no ``AGENTS.md`` or ``CLAUDE.md`` →
       status="not_bridgeable", reason="no_agents_or_claude_md"
    5. Has ``AGENTS.md`` or ``CLAUDE.md`` → status="bridgeable"

    All output tuples are sorted by path.
    """
    bridgeable: list[Path] = []
    not_bridgeable: list[ScanCandidate] = []
    filtered: list[ScanCandidate] = []

    for path in candidates:
        # 1. Reject symlinks.
        if path.is_symlink():
            filtered.append(ScanCandidate(path=path, status="filtered", filter_reason="symlink"))
            continue

        # 2. Must have .git directory (not a file).
        git_path = path / ".git"
        if not git_path.exists():
            filtered.append(ScanCandidate(path=path, status="filtered", filter_reason="no_git"))
            continue
        if git_path.is_file():
            filtered.append(ScanCandidate(path=path, status="filtered", filter_reason="not_git_root"))
            continue

        # 3. Must have Claude presence.
        has_agents_md = (path / "AGENTS.md").is_file()
        has_claude_md = (path / "CLAUDE.md").is_file()
        has_dot_claude = (path / ".claude").is_dir()

        if not (has_agents_md or has_claude_md or has_dot_claude):
            filtered.append(ScanCandidate(path=path, status="filtered", filter_reason="no_claude"))
            continue

        # 4/5. Distinguish bridgeable from not_bridgeable.
        if has_agents_md or has_claude_md:
            bridgeable.append(path)
        else:
            not_bridgeable.append(
                ScanCandidate(
                    path=path,
                    status="not_bridgeable",
                    filter_reason="no_agents_or_claude_md",
                )
            )

    return ScanResult(
        bridgeable=tuple(sorted(bridgeable)),
        not_bridgeable=tuple(sorted(not_bridgeable, key=lambda c: c.path)),
        filtered=tuple(sorted(filtered, key=lambda c: c.path)),
    )


# ---------------------------------------------------------------------------
# Top-level scan entry point (Task 4)
# ---------------------------------------------------------------------------


def scan_for_projects(bridge_home: Path) -> ScanResult:
    """Scan for projects using config from *bridge_home*.

    Composes :func:`load_scan_config` → :func:`expand_scan_globs` →
    :func:`filter_scan_candidates`.

    Returns an empty :class:`ScanResult` when the config file is missing
    or ``scan_paths`` is empty.
    """
    config = load_scan_config(bridge_home)
    if not config.scan_paths:
        return _EMPTY_SCAN_RESULT

    candidates = expand_scan_globs(
        scan_paths=config.scan_paths,
        exclude_paths=config.exclude_paths,
    )
    if not candidates:
        return _EMPTY_SCAN_RESULT

    return filter_scan_candidates(candidates)
