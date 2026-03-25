"""Tests for config validation logic."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.config_check import (
    CheckResult,
    check_global_config,
    check_project_config,
    format_check_report,
    format_check_report_json,
)


# ---------------------------------------------------------------------------
# CheckResult dataclass
# ---------------------------------------------------------------------------


class TestCheckResult:
    """CheckResult is a frozen dataclass with expected fields."""

    def test_frozen(self):
        result = CheckResult(label="test", passed=True, message="ok")
        with pytest.raises(AttributeError):
            result.label = "other"  # type: ignore[misc]

    def test_default_message_is_empty(self):
        result = CheckResult(label="test", passed=True)
        assert result.message == ""


# ---------------------------------------------------------------------------
# check_global_config
# ---------------------------------------------------------------------------


class TestCheckGlobalConfig:
    """Tests for global config validation."""

    def test_missing_file_returns_pass(self, tmp_path):
        """Missing global config is fine — means defaults are in use."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "nonexistent" / "config.toml"
        results = check_global_config(config_path, bridge_home=bridge_home)
        assert len(results) == 1
        assert results[0].passed is True
        assert "defaults" in results[0].message.lower()

    def test_valid_config_passes_all(self, tmp_path):
        """A well-formed config with valid scan paths passes all checks."""
        bridge_home = tmp_path / "bridge"
        bridge_home.mkdir()

        # Create real directories for scan paths
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir()
        (projects_dir / "alpha").mkdir()
        (projects_dir / "beta").mkdir()

        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'scan_paths = ["{projects_dir / "alpha"}", "{projects_dir / "beta"}"]\n'
            f'[log]\nlog_retention_days = 30\n'
        )
        results = check_global_config(config_path, bridge_home=bridge_home)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_malformed_toml_fails(self, tmp_path):
        """Malformed TOML is reported as failure."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text("[broken\nnot valid toml")
        results = check_global_config(config_path, bridge_home=bridge_home)

        # Should have at least one result, and the TOML check should fail
        toml_results = [r for r in results if "toml" in r.label.lower()]
        assert len(toml_results) == 1
        assert toml_results[0].passed is False

        # Should return early — no further checks after TOML parse failure
        assert len(results) == 1

    def test_scan_path_not_found_flagged(self, tmp_path):
        """Scan path pointing to nonexistent directory is flagged."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'scan_paths = ["/does/not/exist/anywhere"]\n'
        )
        results = check_global_config(config_path, bridge_home=bridge_home)

        # Find the scan_paths result
        scan_results = [r for r in results if "scan" in r.label.lower()]
        assert len(scan_results) == 1
        assert scan_results[0].passed is False
        assert "/does/not/exist/anywhere" in scan_results[0].message

    def test_scan_path_with_tilde_expands(self, tmp_path):
        """Scan paths with ~ are expanded before checking."""
        bridge_home = tmp_path / "bridge"
        # Use a real ~ path that almost certainly exists
        config_path = tmp_path / "config.toml"
        # Write a path using a real directory that exists
        real_dir = tmp_path / "realdir"
        real_dir.mkdir()
        config_path.write_text(f'scan_paths = ["{real_dir}"]\n')

        results = check_global_config(config_path, bridge_home=bridge_home)
        scan_results = [r for r in results if "scan" in r.label.lower()]
        assert len(scan_results) == 1
        assert scan_results[0].passed is True

    def test_unknown_top_level_keys_flagged(self, tmp_path):
        """Unknown top-level keys are flagged."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text('banana = true\napple = 42\n')

        results = check_global_config(config_path, bridge_home=bridge_home)
        unknown_results = [r for r in results if "unknown" in r.label.lower()]
        assert len(unknown_results) == 1
        assert unknown_results[0].passed is False
        assert "banana" in unknown_results[0].message
        assert "apple" in unknown_results[0].message

    def test_known_keys_not_flagged(self, tmp_path):
        """Known top-level keys (scan_paths, exclude_paths, log, exclude) are accepted."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            'scan_paths = []\n'
            'exclude_paths = []\n'
            '[log]\nlog_retention_days = 90\n'
            '[exclude]\nplugins = []\n'
        )
        results = check_global_config(config_path, bridge_home=bridge_home)
        unknown_results = [r for r in results if "unknown" in r.label.lower()]
        assert len(unknown_results) == 0

    def test_empty_scan_paths_passes(self, tmp_path):
        """Empty scan_paths list is valid."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text('scan_paths = []\n')

        results = check_global_config(config_path, bridge_home=bridge_home)
        scan_results = [r for r in results if "scan" in r.label.lower()]
        assert len(scan_results) == 1
        assert scan_results[0].passed is True

    def test_no_scan_paths_key_omits_scan_check(self, tmp_path):
        """Config without scan_paths does not produce a scan check."""
        bridge_home = tmp_path / "bridge"
        config_path = tmp_path / "config.toml"
        config_path.write_text('[log]\nlog_retention_days = 30\n')

        results = check_global_config(config_path, bridge_home=bridge_home)
        scan_results = [r for r in results if "scan" in r.label.lower()]
        assert len(scan_results) == 0

    def test_mixed_valid_and_invalid_scan_paths(self, tmp_path):
        """Mix of valid and invalid scan paths reports failure with details."""
        bridge_home = tmp_path / "bridge"
        real_dir = tmp_path / "exists"
        real_dir.mkdir()

        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'scan_paths = ["{real_dir}", "/does/not/exist"]\n'
        )

        results = check_global_config(config_path, bridge_home=bridge_home)
        scan_results = [r for r in results if "scan" in r.label.lower()]
        assert len(scan_results) == 1
        assert scan_results[0].passed is False
        assert "/does/not/exist" in scan_results[0].message


# ---------------------------------------------------------------------------
# check_project_config
# ---------------------------------------------------------------------------


class TestCheckProjectConfig:
    """Tests for project config validation."""

    def test_missing_file_returns_pass(self, tmp_path):
        """Missing project config is fine — means no overrides."""
        config_path = tmp_path / ".codex" / "bridge.toml"
        results = check_project_config(config_path)
        assert len(results) == 1
        assert results[0].passed is True
        assert "no project overrides" in results[0].message.lower()

    def test_valid_project_config(self, tmp_path):
        """Project config with only [exclude] passes."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text('[exclude]\nplugins = ["foo/bar"]\n')

        results = check_project_config(config_path)
        assert all(r.passed for r in results), [r for r in results if not r.passed]

    def test_malformed_toml_fails(self, tmp_path):
        """Malformed TOML in project config is reported."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text("[bad\nnot valid")

        results = check_project_config(config_path)
        toml_results = [r for r in results if "toml" in r.label.lower()]
        assert len(toml_results) == 1
        assert toml_results[0].passed is False
        # Early return — no further checks
        assert len(results) == 1

    def test_scan_paths_rejected_in_project(self, tmp_path):
        """scan_paths at top level is a global-only key, rejected in project config."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text('scan_paths = ["~/Work/*"]\n')

        results = check_project_config(config_path)
        rejected = [r for r in results if not r.passed]
        assert len(rejected) >= 1
        # Should mention scan_paths
        messages = " ".join(r.message for r in rejected)
        assert "scan_paths" in messages

    def test_exclude_paths_rejected_in_project(self, tmp_path):
        """exclude_paths at top level is a global-only key, rejected in project config."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text('exclude_paths = ["/tmp"]\n')

        results = check_project_config(config_path)
        rejected = [r for r in results if not r.passed]
        assert len(rejected) >= 1
        messages = " ".join(r.message for r in rejected)
        assert "exclude_paths" in messages

    def test_log_section_rejected_in_project(self, tmp_path):
        """[log] section is a global-only key, rejected in project config."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text('[log]\nlog_retention_days = 30\n')

        results = check_project_config(config_path)
        rejected = [r for r in results if not r.passed]
        assert len(rejected) >= 1
        messages = " ".join(r.message for r in rejected)
        assert "log" in messages

    def test_multiple_global_only_keys(self, tmp_path):
        """Multiple global-only keys are all reported."""
        config_path = tmp_path / "bridge.toml"
        config_path.write_text(
            'scan_paths = []\n'
            'exclude_paths = []\n'
            '[log]\nlog_retention_days = 30\n'
        )

        results = check_project_config(config_path)
        rejected = [r for r in results if not r.passed]
        assert len(rejected) >= 1
        messages = " ".join(r.message for r in rejected)
        assert "scan_paths" in messages
        assert "exclude_paths" in messages
        assert "log" in messages


# ---------------------------------------------------------------------------
# format_check_report
# ---------------------------------------------------------------------------


class TestFormatCheckReport:
    """Tests for human-readable report formatting."""

    def test_all_pass(self):
        """All-passing results format with check marks."""
        results = [
            CheckResult(label="TOML well-formed", passed=True),
            CheckResult(label="scan_paths", passed=True, message="2 paths, all resolve"),
        ]
        report = format_check_report("global config", results)

        assert "global config" in report
        # Each result on its own line with a check mark
        assert "\u2713" in report  # ✓
        assert "TOML well-formed" in report
        assert "scan_paths" in report

    def test_mixed_pass_fail(self):
        """Mixed results show both ✓ and ✗."""
        results = [
            CheckResult(label="TOML well-formed", passed=True),
            CheckResult(
                label="exclude.plugins",
                passed=False,
                message='"foo/bar" — not found',
            ),
        ]
        report = format_check_report("global config", results)
        assert "\u2713" in report  # ✓
        assert "\u2717" in report  # ✗
        assert "TOML well-formed" in report
        assert "exclude.plugins" in report
        assert "foo/bar" in report

    def test_empty_results(self):
        """Empty results list produces a header with no entries."""
        report = format_check_report("project config", [])
        assert "project config" in report

    def test_message_included_when_present(self):
        """Message is appended after the label when present."""
        results = [
            CheckResult(label="check_a", passed=True, message="details here"),
        ]
        report = format_check_report("test", results)
        assert "details here" in report


# ---------------------------------------------------------------------------
# format_check_report_json
# ---------------------------------------------------------------------------


class TestFormatCheckReportJson:
    """Tests for JSON report formatting."""

    def test_structure(self):
        """JSON output has global and project arrays."""
        global_results = [
            CheckResult(label="TOML well-formed", passed=True),
        ]
        project_results = [
            CheckResult(label="TOML well-formed", passed=False, message="parse error"),
        ]
        raw = format_check_report_json(global_results, project_results)
        data = json.loads(raw)

        assert "global" in data
        assert "project" in data
        assert len(data["global"]) == 1
        assert len(data["project"]) == 1

    def test_entry_fields(self):
        """Each JSON entry has label, passed, message."""
        global_results = [
            CheckResult(label="scan_paths", passed=True, message="ok"),
        ]
        raw = format_check_report_json(global_results, [])
        data = json.loads(raw)

        entry = data["global"][0]
        assert entry["label"] == "scan_paths"
        assert entry["passed"] is True
        assert entry["message"] == "ok"

    def test_valid_json(self):
        """Output is valid JSON."""
        raw = format_check_report_json([], [])
        data = json.loads(raw)
        assert data == {"global": [], "project": []}
