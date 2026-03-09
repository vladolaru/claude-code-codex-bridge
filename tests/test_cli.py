"""Tests for the Codex interop CLI against isolated fixtures."""

from __future__ import annotations

import argparse
import json
import runpy
from pathlib import Path
import plistlib

import pytest

from cc_codex_bridge import cli
from cc_codex_bridge.locking import acquire_global_registry_lock, acquire_project_lock


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
    assert (project_root / ".codex" / "config.toml").exists()
    assert (codex_home / "skills" / "prompt-engineer-prompt-engineer").exists()


def test_install_launchagent_cli_writes_plist(make_project, tmp_path: Path):
    """CLI install-launchagent writes a plist into the requested LaunchAgents directory."""
    project_root, _agents_md = make_project()
    launchagents_dir = tmp_path / "LaunchAgents"
    logs_dir = tmp_path / "logs"

    exit_code = cli.main(
        [
            "install-launchagent",
            "--project",
            str(project_root),
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
        "reconcile",
    ]


def test_print_launchagent_cli_requires_valid_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """LaunchAgent commands surface project validation errors."""
    project_root = tmp_path / "missing-agents"
    project_root.mkdir()

    exit_code = cli.main(["print-launchagent", "--project", str(project_root)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Could not resolve a project root with AGENTS.md" in captured.err


def test_print_launchagent_cli_resolves_project_root_from_nested_path(
    make_project,
    capsys: pytest.CaptureFixture[str],
):
    """LaunchAgent commands reuse project-root discovery instead of requiring the repo root."""
    project_root, _agents_md = make_project()
    nested_file = project_root / "nested" / "notes.txt"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("note\n")

    exit_code = cli.main(["print-launchagent", "--project", str(nested_file)])

    captured = capsys.readouterr()
    payload = plistlib.loads(captured.out.encode())
    assert exit_code == 0
    assert payload["WorkingDirectory"] == str(project_root)
    assert payload["ProgramArguments"][4] == str(project_root)


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

    prompt_path = project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    prompt_path.write_bytes(b"\xff\xfebroken")

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
    assert "Unable to decode managed text file as UTF-8" in captured.err


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


def test_status_and_validate_remain_usable_while_reconcile_locks_are_held(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """Read-only CLI commands do not require the reconcile locks."""
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

    with acquire_project_lock(project_root):
        with acquire_global_registry_lock(codex_home):
            validate_exit = cli.main(
                ["validate", "--project", str(project_root), "--cache-dir", str(cache_root)]
            )
            validate_captured = capsys.readouterr()
            status_exit = cli.main(
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
            status_captured = capsys.readouterr()

    assert validate_exit == 0
    assert "GENERATED_SKILLS: 1" in validate_captured.out
    assert status_exit == 0
    assert "STATUS: pending_changes" in status_captured.out


def test_reconcile_cli_surfaces_project_lock_errors(
    make_project,
    make_plugin_version,
    tmp_path: Path,
    capsys,
):
    """CLI reconcile prints a clean lock error and exits non-zero."""
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

    with acquire_project_lock(project_root):
        exit_code = cli.main(
            [
                "reconcile",
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
    assert "Project reconcile lock is already held" in captured.err


def test_validate_surfaces_os_errors_as_user_facing_errors(monkeypatch: pytest.MonkeyPatch, capsys):
    """Filesystem errors during pipeline setup should not escape as tracebacks."""
    monkeypatch.setattr(cli, "discover", lambda **_kwargs: (_ for _ in ()).throw(PermissionError("boom")))

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
    assert "GENERATED_ROLES: 1" in captured.out


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
    assert "GENERATED_ROLES: 1" in captured.out
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
    generated_skill = codex_home / "skills" / "prompt-engineer-prompt-engineer"

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
                codex_home=None,
            )

    monkeypatch.setattr(cli, "build_parser", lambda: FakeParser())

    exit_code = cli.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unsupported command" in captured.err


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
