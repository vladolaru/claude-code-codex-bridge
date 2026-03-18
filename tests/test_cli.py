"""Tests for the Codex bridge CLI against isolated fixtures."""

from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path
import plistlib

import pytest

from cc_codex_bridge import cli


def test_validate_runs_against_isolated_project_and_cache(
    make_project, make_plugin_version
):
    """`validate` should succeed entirely from temporary fixtures."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        agent_names=("architecture-reviewer",),
    )
    (version_dir / "agents" / "architecture-reviewer.md").write_text(
        "---\n"
        "name: architecture-reviewer\n"
        "description: Software architecture review\n"
        "tools:\n"
        "  - Read\n"
        "---\n\n"
        "You are an architecture reviewer.\n"
    )
    (project_root / "CLAUDE.md").write_text("@AGENTS.md\n")

    exit_code = cli.main(
        ["validate", "--project", str(project_root), "--cache-dir", str(cache_root)]
    )

    assert exit_code == 0


def test_reconcile_and_dry_run_respect_fake_codex_home(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """CLI reconcile writes outputs, while a later `reconcile --dry-run` reports no changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_exit = cli.main(
        [
            "reconcile",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )
    dry_run_exit = cli.main(
        [
            "reconcile",
            "--dry-run",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )

    assert reconcile_exit == 0
    assert dry_run_exit == 0
    assert (codex_home / "agents" / "reviewer.toml").exists()
    assert (codex_home / "skills" / "prompt-engineer").exists()


def test_install_launchagent_cli_writes_plist(tmp_path: Path):
    """CLI install-launchagent writes a global plist into the requested LaunchAgents directory."""
    launchagents_dir = tmp_path / "LaunchAgents"
    logs_dir = tmp_path / "logs"

    exit_code = cli.main(
        [
            "install-launchagent",
            "--launchagents-dir",
            str(launchagents_dir),
            "--logs-dir",
            str(logs_dir),
            "--python-executable",
            "/usr/bin/python3",
            "--cli-path",
            "/tmp/cc_codex_bridge/cli.py",
            "--interval",
            "900",
        ]
    )

    assert exit_code == 0
    plist_paths = list(launchagents_dir.glob("*.plist"))
    assert len(plist_paths) == 1
    payload = plistlib.loads(plist_paths[0].read_bytes())
    assert payload["StartInterval"] == 900
    assert payload["ProgramArguments"][:3] == [
        "/usr/bin/python3",
        str(Path("/tmp/cc_codex_bridge/cli.py").resolve()),
        "reconcile-all",
    ]


def test_print_launchagent_cli_produces_global_plist(capsys: pytest.CaptureFixture[str]):
    """print-launchagent produces a global reconcile-all plist without requiring --project."""
    exit_code = cli.main(["print-launchagent"])

    captured = capsys.readouterr()
    payload = plistlib.loads(captured.out.encode())
    assert exit_code == 0
    assert "reconcile-all" in payload["Label"]
    assert "reconcile-all" in payload["ProgramArguments"]


def test_print_launchagent_rejects_pipeline_flags():
    """LaunchAgent commands should not accept pipeline-only flags."""
    # --project is a pipeline flag, not a LaunchAgent flag
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["print-launchagent", "--project", "/tmp/fake"])
    assert exc_info.value.code != 0


def test_reconcile_dry_run_with_diff_flag_reports_file_diff(
    make_project, make_plugin_version, tmp_path: Path, capsys
):
    """CLI reconcile --dry-run --diff returns a unified diff when managed text changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", agent_names=("reviewer",)
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nOld body.\n")
    codex_home = tmp_path / "codex-home"

    assert cli.main(
        [
            "reconcile",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    ) == 0
    capsys.readouterr()

    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nNew body.\n")
    exit_code = cli.main(
        [
            "reconcile",
            "--dry-run",
            "--diff",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "@@" in captured.out
    assert "+New body." in captured.out


def test_reconcile_diff_surfaces_non_utf8_managed_text_as_user_facing_error(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """Diff output rejects invalid UTF-8 managed files with a clean CLI error."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", agent_names=("reviewer",)
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n")
    codex_home = tmp_path / "codex-home"

    assert cli.main(
        [
            "reconcile",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    ) == 0
    capsys.readouterr()

    agent_toml_path = codex_home / "agents" / "reviewer.toml"
    agent_toml_path.write_bytes(b"\xff\xfebroken")

    exit_code = cli.main(
        [
            "reconcile",
            "--dry-run",
            "--diff",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unable to decode" in captured.err and "UTF-8" in captured.err


def test_reconcile_diff_requires_dry_run(make_project, make_plugin_version, tmp_path: Path, capsys):
    """CLI rejects `reconcile --diff` without `--dry-run`."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")

    exit_code = cli.main(
        [
            "reconcile",
            "--diff",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(tmp_path / "codex-home"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--diff requires --dry-run" in captured.err


def test_validate_surfaces_os_errors_as_user_facing_errors(monkeypatch: pytest.MonkeyPatch, capsys):
    """Filesystem errors during pipeline setup should not escape as tracebacks."""
    monkeypatch.setattr(cli, "build_project_desired_state", lambda *_a, **_kw: (_ for _ in ()).throw(PermissionError("boom")))

    exit_code = cli.main(["validate", "--project", "."])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: boom" in captured.err


def test_validate_surfaces_non_utf8_claude_md_as_user_facing_error(
    make_project,
    make_plugin_version,
    capsys,
):
    """Non-UTF-8 CLAUDE.md content fails cleanly during shim planning."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (project_root / "CLAUDE.md").write_bytes(b"\xff\xfebroken")

    exit_code = cli.main(
        ["validate", "--project", str(project_root), "--cache-dir", str(cache_root)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Unable to decode CLAUDE.md shim candidate as UTF-8" in captured.err


def test_validate_fails_for_unsupported_agent_tools(make_project, make_plugin_version, capsys):
    """Unsupported Claude tools block validation with an explicit diagnostic."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("broken",),
    )
    (version_dir / "agents" / "broken.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Review\n"
        "tools:\n"
        "  - Read\n"
        "  - NotebookEdit\n"
        "---\n\n"
        "Prompt body.\n"
    )

    exit_code = cli.main(
        ["validate", "--project", str(project_root), "--cache-dir", str(cache_root)]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unsupported Claude tools: NotebookEdit" in captured.err


def test_reconcile_fails_before_writing_for_unsupported_agent_tools(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """Unsupported Claude tools block reconcile before any outputs are written."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("broken",),
    )
    (version_dir / "agents" / "broken.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Review\n"
        "tools:\n"
        "  - NotebookEdit\n"
        "---\n\n"
        "Prompt body.\n"
    )
    codex_home = tmp_path / "codex-home"

    exit_code = cli.main(
        [
            "reconcile",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unsupported Claude tools: NotebookEdit" in captured.err
    assert not (project_root / ".codex").exists()
    assert not codex_home.exists()


def test_status_surfaces_os_errors_as_user_facing_errors(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    """Filesystem errors during diff/reporting should produce a clean CLI error."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    monkeypatch.setattr(
        cli,
        "diff_desired_state",
        lambda _desired: (_ for _ in ()).throw(PermissionError("boom")),
    )

    exit_code = cli.main(
        [
            "status",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(tmp_path / "codex-home"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Error: boom" in captured.err


def test_status_json_reports_invalid_translation_diagnostics(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """`status --json` reports invalid agent translation state instead of pending changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("broken",),
    )
    (version_dir / "agents" / "broken.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Review\n"
        "tools:\n"
        "  - NotebookEdit\n"
        "  - Read\n"
        "---\n\n"
        "Prompt body.\n"
    )

    exit_code = cli.main(
        [
            "status",
            "--json",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(tmp_path / "codex-home"),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "invalid"
    assert payload["pending_change_count"] == 0
    assert payload["diagnostics"] == [
        {
            "agent_name": "broken",
            "kind": "unsupported_agent_tools",
            "message": f"{version_dir / 'agents' / 'broken.md'}: unsupported Claude tools: NotebookEdit",
            "source_path": str(version_dir / "agents" / "broken.md"),
            "unsupported_tools": ["NotebookEdit"],
        }
    ]


def test_validate_succeeds_when_unsupported_agent_is_excluded(
    make_project,
    make_plugin_version,
    capsys,
):
    """Excluded unsupported agents no longer block generation commands."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("broken", "reviewer"),
    )
    (version_dir / "agents" / "broken.md").write_text(
        "---\n"
        "name: broken\n"
        "description: Review\n"
        "tools:\n"
        "  - NotebookEdit\n"
        "---\n\n"
        "Prompt body.\n"
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    exit_code = cli.main(
        [
            "validate",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--exclude-agent",
            "market/prompt-engineer/broken",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "GENERATED_AGENTS: 1" in captured.out


def test_status_cli_reports_pending_and_json(make_project, make_plugin_version, tmp_path: Path, capsys):
    """`status` reports pending changes and supports JSON output."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    pending_exit = cli.main(
        [
            "status",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )
    pending_captured = capsys.readouterr()

    assert pending_exit == 0
    assert "STATUS: pending_changes" in pending_captured.out
    assert "PENDING_CHANGES:" in pending_captured.out

    assert cli.main(
        [
            "reconcile",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    ) == 0
    capsys.readouterr()

    in_sync_exit = cli.main(
        [
            "status",
            "--json",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(codex_home),
        ]
    )
    in_sync_captured = capsys.readouterr()

    payload = json.loads(in_sync_captured.out)
    assert in_sync_exit == 0
    assert payload["status"] == "in_sync"
    assert payload["pending_change_count"] == 0
    assert payload["categorized_changes"]["project_files"]["create"] == []
    assert payload["categorized_changes"]["skills"]["create"] == []


def test_validate_honors_project_exclusion_config(make_project, make_plugin_version, capsys):
    """`validate` applies exclusions from `.codex/bridge.toml`."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("portable", "cc-only"),
        agent_names=("reviewer", "cc-reviewer"),
    )
    exclusions_path = project_root / ".codex" / "bridge.toml"
    exclusions_path.parent.mkdir(parents=True)
    exclusions_path.write_text(
        "[exclude]\n"
        'skills = ["market/prompt-engineer/cc-only"]\n'
        'agents = ["market/prompt-engineer/cc-reviewer"]\n'
    )

    exit_code = cli.main(
        ["validate", "--project", str(project_root), "--cache-dir", str(cache_root)]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "GENERATED_AGENTS: 1" in captured.out
    assert "GENERATED_SKILLS: 1" in captured.out
    assert "EXCLUDED_SKILL: market/prompt-engineer/cc-only" in captured.out
    assert "EXCLUDED_AGENT: market/prompt-engineer/cc-reviewer.md" in captured.out


def test_reconcile_exclude_skill_removes_previously_managed_output(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Excluding a previously generated skill removes it as stale managed output."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    generated_skill = codex_home / "skills" / "prompt-engineer"

    assert (
        cli.main(
            [
                "reconcile",
                "--project",
                str(project_root),
                "--cache-dir",
                str(cache_root),
                "--codex-home",
                str(codex_home),
            ]
        )
        == 0
    )
    assert generated_skill.exists()

    assert (
        cli.main(
            [
                "reconcile",
                "--project",
                str(project_root),
                "--cache-dir",
                str(cache_root),
                "--codex-home",
                str(codex_home),
                "--exclude-skill",
                "market/prompt-engineer/prompt-engineer",
            ]
        )
        == 0
    )
    assert not generated_skill.exists()


def test_status_json_reports_excluded_entities(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """`status --json` includes effective exclusions applied to this run."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )

    exit_code = cli.main(
        [
            "status",
            "--json",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--codex-home",
            str(tmp_path / "codex-home"),
            "--exclude-agent",
            "market/prompt-engineer/reviewer",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["excluded"]["plugins"] == []
    assert payload["excluded"]["skills"] == []
    assert payload["excluded"]["agents"] == ["market/prompt-engineer/reviewer.md"]


def test_cli_exclude_skill_flag_overrides_config_skills(make_project, make_plugin_version, capsys):
    """`--exclude-skill` replaces config skill exclusions for that run."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("portable", "cc-only"),
    )
    exclusions_path = project_root / ".codex" / "bridge.toml"
    exclusions_path.parent.mkdir(parents=True)
    exclusions_path.write_text(
        "[exclude]\n"
        'skills = ["market/prompt-engineer/portable"]\n'
    )

    exit_code = cli.main(
        [
            "validate",
            "--project",
            str(project_root),
            "--cache-dir",
            str(cache_root),
            "--exclude-skill",
            "market/prompt-engineer/cc-only",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "EXCLUDED_SKILL: market/prompt-engineer/cc-only" in captured.out
    assert "EXCLUDED_SKILL: market/prompt-engineer/portable" not in captured.out


def test_cli_handles_unsupported_command(
    make_project,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    """The fallback unsupported-command branch returns a non-zero exit code."""
    project_root, _agents_md = make_project()

    class FakeParser:
        def parse_args(self, argv):
            return argparse.Namespace(
                command="mystery",
                project=project_root,
                cache_dir=None,
                claude_home=None,
                codex_home=None,
            )

    monkeypatch.setattr(cli, "build_parser", lambda: FakeParser())

    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unsupported command" in captured.err


def _make_minimal_plugin(cache_root: Path, marketplace: str, plugin_name: str, version: str):
    """Create a bare-minimum plugin in the cache for CLI tests."""
    version_dir = cache_root / marketplace / plugin_name / version
    version_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = version_dir / "skills" / "minimal"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: minimal\ndescription: test\n---\n"
    )


def test_validate_with_claude_home_flag(make_project, tmp_path: Path, capsys):
    """The --claude-home flag overrides the Claude home for plugin discovery."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "custom-claude-home"
    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")

    exit_code = cli.main([
        "validate",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
    ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PLUGINS_FOUND: 1" in captured.out


def test_reconcile_includes_user_level_skills(make_project, tmp_path: Path, capsys):
    """Reconcile translates and installs user-level skills to ~/.codex/skills/."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"

    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")

    user_skill = claude_home / "skills" / "my-tool"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: my-tool\ndescription: A tool\n---\n\nUse this.\n"
    )

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0
    assert (codex_home / "skills" / "my-tool" / "SKILL.md").exists()


def test_reconcile_includes_project_level_skills(make_project, tmp_path: Path, capsys):
    """Reconcile translates and installs project-level skills to .codex/skills/."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"

    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")

    project_skill = project_root / ".claude" / "skills" / "run-tests"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text(
        "---\nname: run-tests\ndescription: Run the test suite\n---\n\nRun tests.\n"
    )

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0
    # Project skills go to project-local .codex/skills/ (raw name, no prefix)
    assert (project_root / ".codex" / "skills" / "run-tests" / "SKILL.md").exists()
    # NOT in global registry
    assert not (codex_home / "skills" / "run-tests" / "SKILL.md").exists()
    assert not (codex_home / "skills" / "project-run-tests" / "SKILL.md").exists()


def test_reconcile_includes_standalone_agents(make_project, tmp_path: Path, capsys):
    """Reconcile translates user-level and project-level agents into config."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"

    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")

    # User-level agent
    user_agents = claude_home / "agents"
    user_agents.mkdir(parents=True)
    (user_agents / "helper.md").write_text(
        "---\nname: helper\ndescription: Helps\ntools:\n  - Read\n---\n\nYou help.\n"
    )

    # Project-level agent
    project_agents = project_root / ".claude" / "agents"
    project_agents.mkdir(parents=True)
    (project_agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews\ntools:\n  - Read\n  - Grep\n---\n\nYou review.\n"
    )

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0
    # User-level agent installed globally in codex_home/agents/
    assert (codex_home / "agents" / "helper.toml").exists()
    # Project-level agent installed locally in .codex/agents/
    assert (project_root / ".codex" / "agents" / "reviewer.toml").exists()


def test_module_entrypoint_invokes_cli_main(monkeypatch: pytest.MonkeyPatch):
    """`python -m cc_codex_bridge` delegates to the CLI main entrypoint."""
    calls: list[list[str] | None] = []

    def fake_main(argv=None):
        calls.append(argv)
        return 0

    monkeypatch.setattr(cli, "main", fake_main)

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("cc_codex_bridge", run_name="__main__")

    assert excinfo.value.code == 0
    assert calls == [None]


def test_clean_command_succeeds(make_project, make_plugin_version, tmp_path: Path):
    """clean command removes managed artifacts and exits 0."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("review",), agent_names=("checker",),
    )
    codex_home = tmp_path / "codex-home"

    # First reconcile to create artifacts
    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0
    assert (project_root / ".codex" / "claude-code-bridge-state.json").exists()

    # Now clean
    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0
    assert not (project_root / ".codex" / "claude-code-bridge-state.json").exists()
    assert not (project_root / "CLAUDE.md").exists()


def test_clean_dry_run_command(make_project, make_plugin_version, tmp_path: Path):
    """clean --dry-run reports changes without deleting."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("review",), agent_names=("checker",),
    )
    codex_home = tmp_path / "codex-home"

    cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ])

    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
        "--codex-home", str(codex_home),
        "--dry-run",
    ])
    assert exit_code == 0
    # Artifacts still exist after dry-run
    assert (project_root / ".codex" / "claude-code-bridge-state.json").exists()
    assert (project_root / "CLAUDE.md").exists()


def test_clean_no_state_exits_zero(make_project, tmp_path: Path):
    """clean on a project with no bridge state exits 0."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"

    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0


def test_clean_succeeds_when_agents_md_missing(make_project, make_plugin_version, tmp_path: Path):
    """clean succeeds using bridge state even when AGENTS.md has been removed."""
    project_root, agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("review",), agent_names=("checker",),
    )
    codex_home = tmp_path / "codex-home"

    # Reconcile to create artifacts
    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0
    assert (project_root / ".codex" / "claude-code-bridge-state.json").exists()

    # Remove AGENTS.md to simulate a partially broken project
    agents_md.unlink()

    # Clean should still work using bridge state
    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0
    assert not (project_root / ".codex" / "claude-code-bridge-state.json").exists()


def test_uninstall_command_succeeds(make_project, make_plugin_version, tmp_path: Path):
    """uninstall removes all bridge artifacts from all discovered projects."""
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    # Reconcile both projects
    for project in (project_a, project_b):
        assert cli.main([
            "reconcile",
            "--project", str(project),
            "--cache-dir", str(cache_root),
            "--codex-home", str(codex_home),
        ]) == 0

    assert (codex_home / "skills" / "review").exists()

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0

    # Project artifacts gone
    for project in (project_a, project_b):
        assert not (project / ".codex" / "claude-code-bridge-state.json").exists()
        assert not (project / "CLAUDE.md").exists()

    # Global artifacts gone
    assert not (codex_home / "skills" / "review").exists()
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
    assert not (codex_home / GLOBAL_REGISTRY_FILENAME).exists()


def test_uninstall_skips_missing_project(make_project, make_plugin_version, tmp_path: Path):
    """uninstall skips inaccessible project roots and cleans the rest."""
    import shutil
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    for project in (project_a, project_b):
        assert cli.main([
            "reconcile",
            "--project", str(project),
            "--cache-dir", str(cache_root),
            "--codex-home", str(codex_home),
        ]) == 0

    # Delete project A entirely
    shutil.rmtree(project_a)

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0

    # Project B was cleaned
    assert not (project_b / ".codex" / "claude-code-bridge-state.json").exists()
    # Global skills removed (force-cleaned even though project A was skipped)
    assert not (codex_home / "skills" / "review").exists()


def test_uninstall_exits_nonzero_on_cleanup_error(make_project, tmp_path: Path, capsys):
    """uninstall returns exit code 1 when a project cleanup fails."""
    from cc_codex_bridge.state import BridgeState
    from cc_codex_bridge.reconcile import STATE_RELATIVE_PATH
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME, GlobalSkillRegistry

    project_root, _ = make_project()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()

    # Set up state with a corrupted managed path
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        managed_project_files=("AGENTS.md",),  # invalid
    )
    state_path.write_text(state.to_json())

    # Register the project
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (codex_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "SKIPPED" in captured.out
    assert "Summary:" in captured.out


def test_uninstall_report_includes_summary_line(make_project, make_plugin_version, tmp_path: Path, capsys):
    """uninstall text output ends with a summary line."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    assert cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ]) == 0

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "Summary: 1 cleaned." in captured.out


def test_uninstall_removes_launchagent_plists(make_project, make_plugin_version, tmp_path: Path):
    """uninstall removes bridge LaunchAgent plists."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    assert cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ]) == 0

    # Plant a bridge plist
    (la_dir / "com.openai.codex-bridge.myproject.abc123.plist").write_bytes(b"<plist/>")
    # Plant a non-bridge plist (should survive)
    (la_dir / "com.apple.something.plist").write_bytes(b"<plist/>")

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
        "--launchagents-dir", str(la_dir),
    ])
    assert exit_code == 0

    assert not (la_dir / "com.openai.codex-bridge.myproject.abc123.plist").exists()
    assert (la_dir / "com.apple.something.plist").exists()


def test_uninstall_removes_global_agents_md(make_project, tmp_path: Path):
    """uninstall removes ~/.codex/AGENTS.md."""
    project_root, _ = make_project()
    codex_home = tmp_path / "codex-home"

    # Simulate a prior reconcile that created global AGENTS.md
    codex_home.mkdir(parents=True, exist_ok=True)
    from cc_codex_bridge.reconcile import GLOBAL_INSTRUCTIONS_SENTINEL
    (codex_home / "AGENTS.md").write_text("# Global instructions\n" + GLOBAL_INSTRUCTIONS_SENTINEL)

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0
    assert not (codex_home / "AGENTS.md").exists()


def test_uninstall_dry_run_json(make_project, make_plugin_version, tmp_path: Path, capsys):
    """uninstall --dry-run --json produces valid structured JSON output."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    (la_dir / "com.openai.codex-bridge.test.abc.plist").write_bytes(b"<plist/>")

    assert cli.main([
        "reconcile",
        "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ]) == 0
    capsys.readouterr()  # discard reconcile output

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
        "--launchagents-dir", str(la_dir),
        "--dry-run",
        "--json",
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "projects" in data
    assert "global" in data
    assert "launchagents" in data
    assert len(data["projects"]) >= 1
    assert data["projects"][0]["status"] in ("will_clean", "not_found")


def test_uninstall_json_includes_global_agents(
    make_project, make_plugin_version, tmp_path: Path, capsys,
):
    """Uninstall JSON output includes global agent file removals."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nReview.\n"
    )
    codex_home = tmp_path / "codex-home"
    cli.main(["reconcile", "--project", str(project_root), "--cache-dir", str(cache_root), "--codex-home", str(codex_home)])
    capsys.readouterr()

    exit_code = cli.main(["uninstall", "--codex-home", str(codex_home), "--dry-run", "--json"])
    output = json.loads(capsys.readouterr().out)

    assert "agents" in output["global"]
    assert any("reviewer" in path for path in output["global"]["agents"])


def test_find_bridge_launchagents_discovers_matching_plists(tmp_path: Path):
    """find_bridge_launchagents returns plists matching the bridge label pattern."""
    from cc_codex_bridge.install_launchagent import find_bridge_launchagents

    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    # Bridge plists
    (la_dir / "com.openai.codex-bridge.myproject.abc123.plist").write_bytes(b"<plist/>")
    (la_dir / "com.openai.codex-bridge.other.def456.plist").write_bytes(b"<plist/>")

    # Non-bridge plists (should be ignored)
    (la_dir / "com.apple.something.plist").write_bytes(b"<plist/>")
    (la_dir / "com.openai.codex.plist").write_bytes(b"<plist/>")

    results = find_bridge_launchagents(launchagents_dir=la_dir)
    assert len(results) == 2
    names = sorted(r.name for r in results)
    assert names == [
        "com.openai.codex-bridge.myproject.abc123.plist",
        "com.openai.codex-bridge.other.def456.plist",
    ]


def test_find_bridge_launchagents_empty_dir(tmp_path: Path):
    """find_bridge_launchagents returns empty tuple when no plists match."""
    from cc_codex_bridge.install_launchagent import find_bridge_launchagents

    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()
    (la_dir / "com.apple.something.plist").write_bytes(b"<plist/>")

    results = find_bridge_launchagents(launchagents_dir=la_dir)
    assert results == ()


def test_find_bridge_launchagents_missing_dir(tmp_path: Path):
    """find_bridge_launchagents returns empty tuple when dir doesn't exist."""
    from cc_codex_bridge.install_launchagent import find_bridge_launchagents

    results = find_bridge_launchagents(launchagents_dir=tmp_path / "nonexistent")
    assert results == ()


def test_print_launchagent_renders_global_plist(tmp_path: Path, capsys):
    """print-launchagent produces a global reconcile-all plist."""
    exit_code = cli.main(["print-launchagent"])
    assert exit_code == 0

    captured = capsys.readouterr()
    payload = plistlib.loads(captured.out.encode() if isinstance(captured.out, str) else captured.out)
    assert "reconcile-all" in payload["Label"]
    assert "reconcile-all" in payload["ProgramArguments"]
    assert payload["StartInterval"] == 1800


def test_install_launchagent_warns_about_per_project_plists(tmp_path: Path, capsys):
    """install-launchagent warns when existing per-project plists are found."""
    la_dir = tmp_path / "home" / "Library" / "LaunchAgents"
    la_dir.mkdir(parents=True)
    (la_dir / "com.openai.codex-bridge.old-project.abc123.plist").write_bytes(b"<plist/>")

    exit_code = cli.main([
        "install-launchagent",
        "--launchagents-dir", str(la_dir),
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    assert "per-project" in captured.out.lower() or "bootout" in captured.out.lower()


def test_reconcile_all_command_dispatches(
    make_project, make_plugin_version, tmp_path: Path,
):
    """reconcile-all command runs without error on a registered project."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
    )
    codex_home = tmp_path / "codex-home"

    # First reconcile to register the project
    exit_code = cli.main([
        "reconcile", "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0

    # Now run reconcile-all
    exit_code = cli.main([
        "reconcile-all",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0


def test_reconcile_all_dry_run_json(
    make_project, make_plugin_version, tmp_path: Path, capsys,
):
    """reconcile-all --dry-run --json produces valid JSON."""
    import json as json_mod

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
    )
    codex_home = tmp_path / "codex-home"

    cli.main([
        "reconcile", "--project", str(project_root),
        "--cache-dir", str(cache_root),
        "--codex-home", str(codex_home),
    ])
    capsys.readouterr()  # discard

    exit_code = cli.main([
        "reconcile-all",
        "--codex-home", str(codex_home),
        "--dry-run", "--json",
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json_mod.loads(captured.out)
    assert "projects" in data
    assert isinstance(data["projects"], list)


def test_reconcile_all_rejects_unused_flags():
    """reconcile-all does not accept --project, --claude-home, or --cache-dir."""
    with pytest.raises(SystemExit, match="2"):
        cli.main(["reconcile-all", "--project", "/tmp/fake"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(["reconcile-all", "--claude-home", "/tmp/fake"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(["reconcile-all", "--cache-dir", "/tmp/fake"])


def test_uninstall_rejects_unused_flags():
    """uninstall does not accept --project, --claude-home, or --cache-dir."""
    with pytest.raises(SystemExit, match="2"):
        cli.main(["uninstall", "--project", "/tmp/fake"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(["uninstall", "--claude-home", "/tmp/fake"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(["uninstall", "--cache-dir", "/tmp/fake"])


def test_clean_rejects_unused_flags():
    """clean does not accept --claude-home or --cache-dir."""
    with pytest.raises(SystemExit, match="2"):
        cli.main(["clean", "--claude-home", "/tmp/fake"])

    with pytest.raises(SystemExit, match="2"):
        cli.main(["clean", "--cache-dir", "/tmp/fake"])


def test_validate_works_without_plugins(make_project, tmp_path: Path, capsys):
    """Validate succeeds with no plugins when user-level sources exist."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    (claude_home / "plugins" / "cache").mkdir(parents=True)

    user_skill = claude_home / "skills" / "my-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n")

    exit_code = cli.main([
        "validate",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
    ])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "PLUGINS_FOUND: 0" in captured.out
    assert "GENERATED_SKILLS: 1" in captured.out


def test_status_reports_bootstrap_needed(tmp_path: Path, capsys):
    """status exits with error when CLAUDE.md exists without AGENTS.md."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# My instructions\n")

    exit_code = cli.main([
        "status", "--project", str(project_root),
        "--codex-home", str(tmp_path / "codex-home"),
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Bootstrap required" in captured.err


def test_validate_reports_bootstrap_needed(tmp_path: Path, capsys):
    """validate exits with error when CLAUDE.md exists without AGENTS.md."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# My instructions\n")

    exit_code = cli.main([
        "validate", "--project", str(project_root),
        "--codex-home", str(tmp_path / "codex-home"),
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Bootstrap required" in captured.err


def test_reconcile_dry_run_reports_bootstrap_needed(tmp_path: Path, capsys):
    """reconcile --dry-run exits with error when bootstrap needed."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# My instructions\n")

    exit_code = cli.main([
        "reconcile", "--dry-run", "--project", str(project_root),
        "--codex-home", str(tmp_path / "codex-home"),
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Bootstrap required" in captured.err


def test_reconcile_executes_bootstrap(tmp_path: Path, capsys):
    """reconcile copies CLAUDE.md to AGENTS.md and proceeds."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    claude_content = "# My instructions\n"
    (project_root / "CLAUDE.md").write_text(claude_content)

    exit_code = cli.main([
        "reconcile", "--project", str(project_root),
        "--codex-home", str(tmp_path / "codex-home"),
    ])

    assert exit_code == 0
    assert (project_root / "AGENTS.md").read_text() == claude_content
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"


def test_reconcile_bootstrap_reports_error_on_symlinked_agents_md(tmp_path: Path, capsys):
    """Bootstrap failure (symlinked AGENTS.md) is reported cleanly, not as a traceback."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# Real instructions\n")
    (project_root / "AGENTS.md").symlink_to(tmp_path / "external.md")

    exit_code = cli.main([
        "reconcile", "--project", str(project_root),
        "--codex-home", str(tmp_path / "codex-home"),
    ])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
