"""CLI integration tests for config commands."""

from __future__ import annotations

import pytest

from cc_codex_bridge import cli


def test_config_show_global_no_config_file():
    """config show --global works even without a config file."""
    exit_code = cli.main(["config", "show", "--global"])
    assert exit_code == 0


def test_config_check_no_config_files():
    """config check works with no config files present."""
    exit_code = cli.main(["config", "check"])
    assert exit_code == 0


def test_config_scan_list_empty():
    """config scan list works with no scan paths."""
    exit_code = cli.main(["config", "scan", "list"])
    assert exit_code == 0


def test_config_log_set_retention_valid():
    """Setting a valid retention value succeeds."""
    exit_code = cli.main(["config", "log", "set-retention", "30"])
    assert exit_code == 0


def test_config_log_set_retention_invalid():
    """Zero value is rejected."""
    exit_code = cli.main(["config", "log", "set-retention", "0"])
    assert exit_code == 1


def test_config_log_set_retention_negative():
    """Negative value is rejected."""
    exit_code = cli.main(["config", "log", "set-retention", "--", "-5"])
    assert exit_code == 1


def test_config_exclude_list_empty():
    """config exclude list works with no exclusions."""
    exit_code = cli.main(["config", "exclude", "list", "--global"])
    assert exit_code == 0


def test_config_scan_list_json(tmp_path):
    """config scan list --json emits valid JSON."""
    import json
    from cc_codex_bridge import cli
    exit_code = cli.main(["config", "scan", "list", "--json"])
    assert exit_code == 0


def test_config_exclude_list_json():
    """config exclude list --json --global emits valid JSON."""
    import json
    from cc_codex_bridge import cli
    exit_code = cli.main(["config", "exclude", "list", "--global", "--json"])
    assert exit_code == 0


def test_config_exclude_add_unexpected_error_propagates(tmp_path, monkeypatch):
    """Unexpected exceptions from discover() propagate instead of being caught."""
    import cc_codex_bridge.discover as discover_module
    from cc_codex_bridge import cli

    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# test\n")

    def _boom(**kwargs):
        raise RuntimeError("unexpected internal error")

    monkeypatch.setattr(discover_module, "discover", _boom)

    with pytest.raises(RuntimeError, match="unexpected internal error"):
        cli.main(["config", "exclude", "add", "plugin", "some/plugin", "--project", str(project_root)])
