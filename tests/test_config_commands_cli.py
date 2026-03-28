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


def test_list_discoverable_entities_global_scope_excludes_project(tmp_path):
    """Global scope should not include project-scoped entities."""
    from cc_codex_bridge.config_exclude_commands import list_discoverable_entities
    from cc_codex_bridge.model import DiscoveryResult, ProjectContext

    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# test\n")

    proj_skills = project_root / ".claude" / "skills" / "my-skill"
    proj_skills.mkdir(parents=True)
    (proj_skills / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n")
    proj_agents = project_root / ".claude" / "agents"
    proj_agents.mkdir(parents=True)
    (proj_agents / "my-agent.md").write_text("---\nname: my-agent\ndescription: test\n---\n")
    proj_commands = project_root / ".claude" / "commands"
    proj_commands.mkdir(parents=True)
    (proj_commands / "my-cmd.md").write_text("---\ndescription: test\n---\n")

    discovery = DiscoveryResult(
        project=ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md"),
        plugins=(),
        user_skills=(proj_skills,),
        user_agents=(),
        user_commands=(),
        project_skills=(proj_skills,),
        project_agents=(proj_agents / "my-agent.md",),
        project_commands=(proj_commands / "my-cmd.md",),
        user_claude_md=None,
    )

    # Global scope: project entities should be excluded
    result = list_discoverable_entities(discovery, scope="global")
    for kind_list in result.values():
        for entry in kind_list:
            assert not entry.startswith("project/"), f"Global scope should not include {entry}"

    # Project scope (default): project entities should be included
    result_proj = list_discoverable_entities(discovery, scope="project")
    project_entries = [e for lst in result_proj.values() for e in lst if e.startswith("project/")]
    assert len(project_entries) > 0, "Project scope should include project entities"


def test_config_exclude_add_global_does_not_offer_project_entities(
    tmp_path, monkeypatch, capsys,
):
    """config exclude add --global should not accept project-scoped entity IDs."""
    import cc_codex_bridge.discover as discover_module
    from cc_codex_bridge.model import DiscoveryResult, ProjectContext
    from cc_codex_bridge import cli

    project_root = tmp_path / "proj"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# test\n")

    proj_skills = project_root / ".claude" / "skills" / "local-skill"
    proj_skills.mkdir(parents=True)
    (proj_skills / "SKILL.md").write_text("---\nname: local-skill\ndescription: test\n---\n")

    monkeypatch.setattr(discover_module, "discover", lambda **kw: DiscoveryResult(
        project=ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md"),
        plugins=(),
        user_skills=(),
        user_agents=(),
        user_commands=(),
        project_skills=(proj_skills,),
        project_agents=(),
        project_commands=(),
        user_claude_md=None,
    ))

    monkeypatch.chdir(project_root)
    exit_code = cli.main([
        "config", "exclude", "add", "--global", "skill", "project/local-skill",
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "not found" in captured.out.lower() or "not found" in captured.err.lower()
