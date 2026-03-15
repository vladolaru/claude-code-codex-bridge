"""Tests for machine-level doctor checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge import cli
from cc_codex_bridge.doctor import doctor_exit_code, overall_status, run_doctor


def test_run_doctor_reports_warnings_without_failing_for_missing_optional_state(tmp_path: Path):
    """Missing Claude cache or PATH visibility should not make doctor fail outright."""
    codex_home = tmp_path / "codex-home"
    launchagents_dir = tmp_path / "LaunchAgents"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_path = bin_dir / "cc-codex-bridge"
    command_path.write_text("#!/bin/sh\n")
    command_path.chmod(0o755)

    checks = run_doctor(
        cache_dir=tmp_path / "missing-cache",
        codex_home=codex_home,
        launchagents_dir=launchagents_dir,
        python_version=(3, 11, 9),
        python_executable=tmp_path / "python3",
        path_env=str(bin_dir),
    )

    assert overall_status(checks) == "warning"
    assert doctor_exit_code(checks) == 0
    assert any(check.name == "python" and check.status == "ok" for check in checks)
    assert any(check.name == "claude_cache" and check.status == "warning" for check in checks)
    assert any(check.name == "command_path" and check.status == "ok" for check in checks)


def test_run_doctor_fails_for_unsupported_python_or_invalid_codex_home(tmp_path: Path):
    """Doctor should fail on blocking runtime prerequisites."""
    invalid_codex_home = tmp_path / "codex-home"
    invalid_codex_home.write_text("not a directory\n")

    checks = run_doctor(
        cache_dir=tmp_path / "missing-cache",
        codex_home=invalid_codex_home,
        launchagents_dir=tmp_path / "LaunchAgents",
        python_version=(3, 10, 14),
        python_executable=tmp_path / "python3",
        path_env="",
    )

    assert overall_status(checks) == "error"
    assert doctor_exit_code(checks) == 1
    assert any(check.name == "python" and check.status == "error" for check in checks)
    assert any(check.name == "codex_home" and check.status == "error" for check in checks)


def test_doctor_warns_when_cache_has_plugin_without_valid_versions(tmp_path: Path):
    """Doctor should warn when a plugin dir has no valid semver versions."""
    cache = tmp_path / "cache"
    marketplace = cache / "market"
    plugin = marketplace / "bad-plugin"
    invalid_version = plugin / "not-a-semver"
    invalid_version.mkdir(parents=True)

    checks = run_doctor(cache_dir=cache, codex_home=tmp_path / "codex")

    cache_check = next(c for c in checks if c.name == "claude_cache")
    assert cache_check.status == "warning"
    assert "no valid" in cache_check.message.lower() or "invalid" in cache_check.message.lower()


def test_doctor_rejects_semver_lookalikes_that_discovery_rejects(tmp_path: Path):
    """Doctor must reject version names that look like semver but fail SemVer.parse."""
    cache = tmp_path / "cache"
    for bad_name in ("1.0.0foo", "01.2.3", "1.2.3-"):
        plugin = cache / "market" / f"plugin-{bad_name}"
        (plugin / bad_name).mkdir(parents=True)

    checks = run_doctor(cache_dir=cache, codex_home=tmp_path / "codex")

    cache_check = next(c for c in checks if c.name == "claude_cache")
    assert cache_check.status == "warning"
    assert "no valid" in cache_check.message.lower()


def test_doctor_cli_supports_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """The CLI doctor command should return JSON output for installer use."""
    codex_home = tmp_path / "codex-home"
    launchagents_dir = tmp_path / "LaunchAgents"

    exit_code = cli.main(
        [
            "doctor",
            "--json",
            "--cache-dir",
            str(tmp_path / "missing-cache"),
            "--codex-home",
            str(codex_home),
            "--launchagents-dir",
            str(launchagents_dir),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "warning"
    assert {check["name"] for check in payload["checks"]} == {
        "python",
        "claude_cache",
        "codex_home",
        "launchagents_dir",
        "command_path",
    }
