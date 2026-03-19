"""Tests for bridge home resolution."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.bridge_home import resolve_bridge_home, project_state_dir, plugin_resource_dir


def test_default_bridge_home(tmp_path):
    """Default bridge home uses the monkeypatched DEFAULT_BRIDGE_HOME."""
    home = resolve_bridge_home()
    assert home == tmp_path / "home" / ".cc-codex-bridge"


def test_bridge_home_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CC_BRIDGE_HOME", str(tmp_path / "custom"))
    home = resolve_bridge_home()
    assert home == (tmp_path / "custom").resolve()


def test_bridge_home_expands_tilde(monkeypatch):
    monkeypatch.setenv("CC_BRIDGE_HOME", "~/.my-bridge")
    home = resolve_bridge_home()
    assert home == (Path.home() / ".my-bridge").resolve()


def test_project_state_dir_is_deterministic(tmp_path):
    dir1 = project_state_dir(tmp_path / "my-project", bridge_home=tmp_path / "bridge")
    dir2 = project_state_dir(tmp_path / "my-project", bridge_home=tmp_path / "bridge")
    assert dir1 == dir2


def test_project_state_dir_differs_per_project(tmp_path):
    dir1 = project_state_dir(tmp_path / "project-a", bridge_home=tmp_path / "bridge")
    dir2 = project_state_dir(tmp_path / "project-b", bridge_home=tmp_path / "bridge")
    assert dir1 != dir2


def test_project_state_dir_under_bridge_home(tmp_path):
    bridge = tmp_path / "bridge"
    d = project_state_dir(tmp_path / "my-project", bridge_home=bridge)
    assert str(d).startswith(str(bridge / "projects"))


def test_plugin_resource_dir_structure(tmp_path):
    d = plugin_resource_dir("market", "pirategoat-tools", bridge_home=tmp_path / "bridge")
    assert d == tmp_path / "bridge" / "plugins" / "market-pirategoat-tools"


def test_plugin_resource_dir_different_plugins(tmp_path):
    bridge = tmp_path / "bridge"
    d1 = plugin_resource_dir("market", "tools-a", bridge_home=bridge)
    d2 = plugin_resource_dir("market", "tools-b", bridge_home=bridge)
    assert d1 != d2
