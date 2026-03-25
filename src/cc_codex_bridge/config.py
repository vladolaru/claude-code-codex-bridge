"""Global bridge configuration loaded from config.toml."""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from cc_codex_bridge.exclusions import SyncExclusions, parse_sync_exclusions

DEFAULT_LOG_RETENTION_DAYS = 90

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BridgeConfig:
    """Validated global bridge configuration."""

    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS
    exclude: SyncExclusions = SyncExclusions()


def load_config(config_path: Path) -> BridgeConfig:
    """Load bridge config from a TOML file, returning defaults for missing values."""
    if not config_path.exists():
        return BridgeConfig()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError):
        return BridgeConfig()

    log_section = data.get("log", {})
    if not isinstance(log_section, dict):
        log_section = {}

    retention = log_section.get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)
    if isinstance(retention, bool) or not isinstance(retention, int) or retention < 1:
        retention = DEFAULT_LOG_RETENTION_DAYS

    exclusions = _parse_exclusions(data, config_path)

    return BridgeConfig(log_retention_days=retention, exclude=exclusions)


def _parse_exclusions(data: dict[str, object], config_path: Path) -> SyncExclusions:
    """Parse the ``[exclude]`` table from global config, falling back to empty on errors."""
    try:
        exclude_table = data.get("exclude", {})
        if not isinstance(exclude_table, dict):
            _log.warning("Global config `exclude` is not a table in: %s", config_path)
            return SyncExclusions()

        return parse_sync_exclusions(exclude_table, config_path)
    except Exception:
        _log.warning(
            "Failed to parse [exclude] section in %s; using empty exclusions",
            config_path,
            exc_info=True,
        )
        return SyncExclusions()
