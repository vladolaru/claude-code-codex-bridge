"""Global bridge configuration loaded from config.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LOG_RETENTION_DAYS = 90


@dataclass(frozen=True)
class BridgeConfig:
    """Validated global bridge configuration."""

    log_retention_days: int = DEFAULT_LOG_RETENTION_DAYS


def load_config(config_path: Path) -> BridgeConfig:
    """Load bridge config from a TOML file, returning defaults for missing values."""
    if not config_path.exists():
        return BridgeConfig()

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError:
        return BridgeConfig()

    log_section = data.get("log", {})
    if not isinstance(log_section, dict):
        return BridgeConfig()

    retention = log_section.get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)
    if not isinstance(retention, int) or retention < 1:
        retention = DEFAULT_LOG_RETENTION_DAYS

    return BridgeConfig(log_retention_days=retention)
