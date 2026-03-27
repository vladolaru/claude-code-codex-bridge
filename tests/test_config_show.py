"""Tests for config show formatting."""

from __future__ import annotations

import json

from cc_codex_bridge.config import BridgeConfig, DEFAULT_LOG_RETENTION_DAYS
from cc_codex_bridge.config_show import format_config_show, format_config_show_json
from cc_codex_bridge.exclusions import SyncExclusions


# ---------------------------------------------------------------------------
# format_config_show — human-readable output
# ---------------------------------------------------------------------------


def test_all_defaults_renders_default_and_none():
    """All-defaults config renders '(default)' for retention and '(none)' for lists."""
    output = format_config_show(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )

    assert "(default)" in output
    assert "90 days" in output
    assert "(none)" in output
    # No real entries — only "(none)" markers should appear for list sections
    assert "Log retention:" in output
    assert "Scan paths:" in output
    assert "Exclude paths:" in output
    assert "Exclude plugins:" in output
    assert "Exclude skills:" in output
    assert "Exclude agents:" in output
    assert "Exclude commands:" in output


def test_scan_paths_show_global_attribution():
    """Scan paths always show '(global)' attribution."""
    output = format_config_show(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=("~/Work/a8c/*", "~/Work/oss/*"),
        exclude_paths=(),
        scope="global",
    )

    assert "~/Work/a8c/*" in output
    assert "~/Work/oss/*" in output
    # Both scan paths should be attributed as global
    lines = output.splitlines()
    scan_lines = [l for l in lines if "~/Work/" in l]
    for line in scan_lines:
        assert "(global)" in line


def test_exclude_paths_show_global_attribution():
    """Exclude paths always show '(global)' attribution."""
    output = format_config_show(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=("~/Work/scratch",),
        scope="global",
    )

    assert "~/Work/scratch" in output
    lines = output.splitlines()
    exclude_lines = [l for l in lines if "~/Work/scratch" in l]
    for line in exclude_lines:
        assert "(global)" in line


def test_custom_retention_shows_global_attribution():
    """Non-default log retention shows '(global)' instead of '(default)'."""
    config = BridgeConfig(log_retention_days=30)
    output = format_config_show(
        global_config=config,
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )

    assert "30 days" in output
    assert "(global)" in output
    # Should not show "(default)" for the retention line
    retention_line = [l for l in output.splitlines() if "Log retention:" in l][0]
    assert "(default)" not in retention_line
    assert "(global)" in retention_line


def test_merged_exclusions_show_global_and_project_attribution():
    """Merged scope shows both '(global)' and '(project)' per entry."""
    global_config = BridgeConfig(
        exclude=SyncExclusions(
            plugins=("acme/tool-a",),
            skills=("skill-x",),
        )
    )
    project_exclusions = SyncExclusions(
        plugins=("vladolaru/pirategoat",),
        skills=("skill-y",),
    )

    output = format_config_show(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="merged",
    )

    # Global exclusions attributed as global
    assert "acme/tool-a" in output
    lines = output.splitlines()
    acme_line = [l for l in lines if "acme/tool-a" in l][0]
    assert "(global)" in acme_line

    # Project exclusions attributed as project
    pirate_line = [l for l in lines if "vladolaru/pirategoat" in l][0]
    assert "(project)" in pirate_line

    # Skills also have correct attribution
    skillx_line = [l for l in lines if "skill-x" in l][0]
    assert "(global)" in skillx_line
    skilly_line = [l for l in lines if "skill-y" in l][0]
    assert "(project)" in skilly_line


def test_global_scope_hides_project_exclusions():
    """When scope='global', project exclusions are not shown."""
    global_config = BridgeConfig(
        exclude=SyncExclusions(plugins=("acme/tool-a",))
    )
    project_exclusions = SyncExclusions(
        plugins=("vladolaru/pirategoat",),
    )

    output = format_config_show(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )

    assert "acme/tool-a" in output
    assert "vladolaru/pirategoat" not in output


def test_project_scope_shows_only_project_exclusions():
    """When scope='project', only project exclusions are shown."""
    global_config = BridgeConfig(
        exclude=SyncExclusions(plugins=("acme/tool-a",))
    )
    project_exclusions = SyncExclusions(
        plugins=("vladolaru/pirategoat",),
    )

    output = format_config_show(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="project",
    )

    # Project exclusions present
    assert "vladolaru/pirategoat" in output
    # Global exclusions hidden
    assert "acme/tool-a" not in output


def test_merged_deduplicates_overlapping_exclusions():
    """Merged scope deduplicates entries present in both global and project.

    When an entry appears in both, it should show only once.
    We attribute as '(global)' since global is the canonical source.
    """
    global_config = BridgeConfig(
        exclude=SyncExclusions(plugins=("acme/tool-a",))
    )
    project_exclusions = SyncExclusions(
        plugins=("acme/tool-a",),
    )

    output = format_config_show(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="merged",
    )

    lines = output.splitlines()
    acme_lines = [l for l in lines if "acme/tool-a" in l]
    # Should appear exactly once (deduplicated)
    assert len(acme_lines) == 1
    assert "(global)" in acme_lines[0]


def test_none_shown_for_empty_sections():
    """Each exclusion category with no entries shows '(none)' on the line after the header."""
    output = format_config_show(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="merged",
    )

    lines = output.splitlines()
    # Check that each section header is followed by "  (none)" on the next line
    for section_key in ("Exclude plugins:", "Exclude skills:", "Exclude agents:", "Exclude commands:"):
        idx = next(i for i, l in enumerate(lines) if section_key in l)
        # Header line should not contain "(none)"
        assert "(none)" not in lines[idx], (
            f"Expected '{section_key}' header to be on its own line"
        )
        # The line immediately after the header should be "  (none)"
        assert lines[idx + 1] == "  (none)", (
            f"Expected '  (none)' after '{section_key}' but got: {repr(lines[idx + 1])}"
        )


def test_format_config_show_exclusion_none_indented_consistently():
    """Empty exclusion sections show '  (none)' indented, not '(none)' after padded label."""
    from cc_codex_bridge.config import BridgeConfig
    from cc_codex_bridge.config_show import format_config_show

    cfg = BridgeConfig()  # all defaults, no exclusions
    output = format_config_show(
        global_config=cfg,
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )
    lines = output.splitlines()
    # Find lines containing "(none)"
    none_lines = [line for line in lines if "(none)" in line]
    # All (none) entries should be indented with 2 spaces
    for line in none_lines:
        assert line.startswith("  "), (
            f"Expected '  (none)' to be indented but got: {repr(line)}"
        )


# ---------------------------------------------------------------------------
# format_config_show_json — structured JSON output
# ---------------------------------------------------------------------------


def test_json_output_includes_all_fields():
    """JSON output includes scope, log_retention_days, scan/exclude paths, and exclusions."""
    global_config = BridgeConfig(
        log_retention_days=30,
        exclude=SyncExclusions(plugins=("acme/tool-a",)),
    )

    raw = format_config_show_json(
        global_config=global_config,
        project_exclusions=SyncExclusions(plugins=("vladolaru/pirategoat",)),
        scan_paths=("~/Work/*",),
        exclude_paths=("~/Work/scratch",),
        scope="merged",
    )
    data = json.loads(raw)

    assert data["scope"] == "merged"
    assert data["log_retention_days"] == {"value": 30, "source": "global"}
    assert data["scan_paths"] == [{"value": "~/Work/*", "source": "global"}]
    assert data["exclude_paths"] == [{"value": "~/Work/scratch", "source": "global"}]
    assert "exclude" in data
    assert {"value": "acme/tool-a", "source": "global"} in data["exclude"]["plugins"]
    assert {"value": "vladolaru/pirategoat", "source": "project"} in data["exclude"]["plugins"]


def test_json_default_retention_source():
    """JSON output shows source='default' when log_retention_days equals the default."""
    raw = format_config_show_json(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )
    data = json.loads(raw)

    assert data["log_retention_days"] == {
        "value": DEFAULT_LOG_RETENTION_DAYS,
        "source": "default",
    }


def test_json_global_scope_hides_project_exclusions():
    """JSON output with scope='global' excludes project exclusion entries."""
    global_config = BridgeConfig(
        exclude=SyncExclusions(plugins=("acme/tool-a",))
    )
    project_exclusions = SyncExclusions(plugins=("vladolaru/pirategoat",))

    raw = format_config_show_json(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )
    data = json.loads(raw)

    plugin_values = [e["value"] for e in data["exclude"]["plugins"]]
    assert "acme/tool-a" in plugin_values
    assert "vladolaru/pirategoat" not in plugin_values


def test_json_empty_exclusion_categories():
    """JSON output shows empty lists for exclusion categories with no entries."""
    raw = format_config_show_json(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="merged",
    )
    data = json.loads(raw)

    assert data["exclude"]["plugins"] == []
    assert data["exclude"]["skills"] == []
    assert data["exclude"]["agents"] == []
    assert data["exclude"]["commands"] == []


def test_json_merged_deduplicates():
    """JSON merged scope deduplicates entries, keeping global attribution."""
    global_config = BridgeConfig(
        exclude=SyncExclusions(skills=("my-skill",))
    )
    project_exclusions = SyncExclusions(skills=("my-skill",))

    raw = format_config_show_json(
        global_config=global_config,
        project_exclusions=project_exclusions,
        scan_paths=(),
        exclude_paths=(),
        scope="merged",
    )
    data = json.loads(raw)

    skill_entries = data["exclude"]["skills"]
    assert len(skill_entries) == 1
    assert skill_entries[0] == {"value": "my-skill", "source": "global"}


def test_json_is_valid_json_string():
    """format_config_show_json returns a valid JSON string."""
    raw = format_config_show_json(
        global_config=BridgeConfig(),
        project_exclusions=None,
        scan_paths=(),
        exclude_paths=(),
        scope="global",
    )

    # Should not raise
    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
