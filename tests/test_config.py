"""Tests for global config loading."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.config import BridgeConfig, load_config


def test_load_config_missing_file(tmp_path):
    """Missing config file returns defaults."""
    cfg = load_config(tmp_path / "config.toml")
    assert cfg.log_retention_days == 90


def test_load_config_empty_file(tmp_path):
    """Empty config file returns defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_load_config_no_log_section(tmp_path):
    """Config with unrelated sections returns log defaults."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[other]\nfoo = 1\n")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_load_config_custom_retention(tmp_path):
    """Config with custom retention days is respected."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[log]\nlog_retention_days = 30\n")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 30


def test_load_config_partial_log_section(tmp_path):
    """Log section with other keys but no retention uses default."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[log]\nother_key = true\n")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_load_config_boolean_retention_falls_back(tmp_path):
    """Boolean log_retention_days falls back to default (bool is subclass of int)."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[log]\nlog_retention_days = true\n")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_bridge_config_defaults():
    """BridgeConfig with no args uses all defaults."""
    cfg = BridgeConfig()
    assert cfg.log_retention_days == 90


def test_load_config_malformed_toml(tmp_path):
    """Malformed TOML returns defaults instead of crashing."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[log\nbroken syntax")
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_load_config_directory_instead_of_file(tmp_path):
    """Config path that is a directory returns defaults instead of crashing."""
    config_path = tmp_path / "config.toml"
    config_path.mkdir()
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90


def test_load_config_unreadable_file(tmp_path):
    """Config file with bad permissions returns defaults instead of crashing."""
    config_path = tmp_path / "config.toml"
    config_path.write_text("[log]\nlog_retention_days = 30\n")
    config_path.chmod(0o000)
    cfg = load_config(config_path)
    assert cfg.log_retention_days == 90
    config_path.chmod(0o644)  # cleanup


def test_load_config_reads_global_exclusions(tmp_path):
    """Global [exclude] section is parsed into SyncExclusions."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        '[exclude]\n'
        'plugins = ["vladolaru-claude-code-plugins/yoloing-safe"]\n'
        'skills = ["some-skill"]\n'
    )
    cfg = load_config(config_file)
    assert cfg.exclude.plugins == ("vladolaru-claude-code-plugins/yoloing-safe",)
    assert cfg.exclude.skills == ("some-skill",)
    assert cfg.exclude.agents == ()
    assert cfg.exclude.commands == ()


def test_load_config_returns_empty_exclusions_when_missing(tmp_path):
    """Missing [exclude] section yields empty SyncExclusions."""
    config_file = tmp_path / "config.toml"
    config_file.write_text('[log]\nlog_retention_days = 30\n')
    cfg = load_config(config_file)
    assert cfg.exclude.plugins == ()
    assert cfg.exclude.skills == ()


def test_load_config_returns_empty_exclusions_when_no_file(tmp_path):
    """Non-existent config file yields empty SyncExclusions."""
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.exclude.plugins == ()
