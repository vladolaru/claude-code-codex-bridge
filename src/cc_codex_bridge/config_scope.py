"""Config scope resolution for the bridge config commands.

Determines which config file to operate on: global (``bridge_home/config.toml``)
or project-local (``.codex/bridge.toml`` within a project root).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_codex_bridge.discover import AGENTS_MD


@dataclass(frozen=True)
class ConfigScope:
    """Resolved config scope indicating which config file to target."""

    target: str  # "global" or "project"
    config_path: Path
    project_root: Path | None


def resolve_config_scope(
    *,
    bridge_home: Path,
    project_dir: Path | None = None,
    force_global: bool = False,
) -> ConfigScope:
    """Resolve which config file to operate on.

    Resolution order:

    1. *force_global* → always global (``bridge_home/config.toml``)
    2. If *project_dir* resolves to a project root (has ``AGENTS.md``),
       target project config (``.codex/bridge.toml``)
    3. Otherwise → global
    """
    if force_global:
        return _global_scope(bridge_home)

    if project_dir is not None:
        project_root = _find_project_root(project_dir)
        if project_root is not None:
            return ConfigScope(
                target="project",
                config_path=project_root / ".codex" / "bridge.toml",
                project_root=project_root,
            )

    return _global_scope(bridge_home)


def _global_scope(bridge_home: Path) -> ConfigScope:
    """Return a global config scope."""
    return ConfigScope(
        target="global",
        config_path=bridge_home / "config.toml",
        project_root=None,
    )


def _find_project_root(start: Path) -> Path | None:
    """Walk up from *start* looking for a directory containing ``AGENTS.md``.

    Returns the project root ``Path`` or ``None`` if no marker is found.
    This is intentionally lighter than ``discover.resolve_project_root`` —
    it does not require the Claude CLI and only checks for ``AGENTS.md``.
    """
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for directory in (candidate, *candidate.parents):
        if (directory / AGENTS_MD).is_file():
            return directory

    return None
