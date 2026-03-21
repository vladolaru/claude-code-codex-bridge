"""Bridge home directory resolution and project-specific paths.

The bridge stores all internal state under ``~/.cc-codex-bridge/``
(configurable via ``$CC_BRIDGE_HOME``).  This keeps bridge internals
out of both the project working tree and the Codex home directory.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

DEFAULT_BRIDGE_HOME = Path.home() / ".cc-codex-bridge"


def resolve_bridge_home() -> Path:
    """Resolve the bridge home directory."""
    env = os.environ.get("CC_BRIDGE_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_BRIDGE_HOME


def project_state_dir(project_root: Path, *, bridge_home: Path) -> Path:
    """Return the project-specific state directory under bridge home."""
    resolved = str(project_root.resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:16]
    return bridge_home / "projects" / digest


def plugin_resource_dir(
    marketplace: str,
    plugin_name: str,
    *,
    bridge_home: Path,
) -> Path:
    """Return the plugin resource directory under bridge home."""
    return bridge_home / "plugins" / f"{marketplace}-{plugin_name}"


def logs_dir(*, bridge_home: Path) -> Path:
    """Return the activity log directory under bridge home."""
    return bridge_home / "logs"


def config_path(*, bridge_home: Path) -> Path:
    """Return the global config file path under bridge home."""
    return bridge_home / "config.toml"
