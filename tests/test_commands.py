"""Tests for command-to-skill translation."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.discover import discover_latest_plugins
from cc_codex_bridge.model import TranslationError
from cc_codex_bridge.translate_commands import (
    translate_installed_commands,
    translate_standalone_commands,
)


def test_translate_command_produces_generated_skill(make_plugin_version):
    """A command markdown file translates into a GeneratedSkill."""
    cache_root, version_dir = make_plugin_version(
        "market", "pirategoat-tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "code-review.md").write_text(
        "---\n"
        "description: Review code incrementally\n"
        "---\n\n"
        "Review the code.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    assert len(result.skills) == 1

    skill = result.skills[0]
    assert skill.marketplace == "market"
    assert skill.plugin_name == "pirategoat-tools"
    assert skill.install_dir_name == "cmd-code-review"
    assert skill.original_skill_name == "cmd-code-review"

    skill_md = next(f for f in skill.files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()
    assert "name: cmd-code-review" in content
    assert "description: Review code incrementally" in content
    assert "Review the code." in content


def test_translate_command_replaces_arguments_variable(make_plugin_version):
    """$ARGUMENTS is replaced with the generic cue."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "optimize.md").write_text(
        "---\n"
        "description: Optimize prompt\n"
        "---\n\n"
        "Optimize: $ARGUMENTS\n\n"
        "If $ARGUMENTS contains a hint, use it.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    replacement = "<use any user-provided details; otherwise infer from context>"
    assert replacement in content
    assert "$ARGUMENTS" not in content


def test_translate_command_replaces_indexed_arguments(make_plugin_version):
    """$ARGUMENTS[0], $0, $ARGUMENTS[1] are all replaced."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "cmd.md").write_text(
        "---\ndescription: Test\n---\n\n"
        "First: $ARGUMENTS[0]\nSecond: $1\nAll: $ARGUMENTS\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    replacement = "<use any user-provided details; otherwise infer from context>"
    assert "$ARGUMENTS" not in content
    assert content.count(replacement) == 3


def test_translate_command_replaces_plugin_root_variable(make_plugin_version):
    """${CLAUDE_PLUGIN_ROOT} is replaced with the resolved plugin path."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\ndescription: Run review\n---\n\n"
        'Run: "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.py"\n'
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert "${CLAUDE_PLUGIN_ROOT}" not in content
    assert str(version_dir.resolve()) in content


def test_translate_command_appends_provenance_marker(make_plugin_version):
    """Translated commands include a provenance marker at the end."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "cmd.md").write_text(
        "---\ndescription: Test\n---\n\nDo things.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert content.rstrip().endswith("<!-- translated from Claude Code command -->")


def test_translate_command_drops_argument_hint_frontmatter(make_plugin_version):
    """argument-hint is dropped from the generated SKILL.md frontmatter."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "cmd.md").write_text(
        "---\n"
        "description: Test\n"
        "argument-hint: '[PR_URL]'\n"
        "---\n\nDo things.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert "argument-hint" not in content


def test_translate_command_drops_allowed_tools_frontmatter(make_plugin_version):
    """allowed-tools is dropped from the generated SKILL.md frontmatter."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "cmd.md").write_text(
        "---\n"
        "description: Test\n"
        "allowed-tools: Bash, Read\n"
        "---\n\nDo things.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert "allowed-tools" not in content


def test_translate_command_derives_description_from_filename(make_plugin_version):
    """Commands without description derive it from the filename stem."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "consolidate-ai-memory.md").write_text(
        "---\nname: consolidate\n---\n\nConsolidate things.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    assert len(result.skills) == 1

    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()
    assert "description: consolidate ai memory" in content


def test_translate_command_without_frontmatter(make_plugin_version):
    """Commands with no frontmatter at all are translated with derived description."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "log-experience.md").write_text(
        "# Log Experience\n\nLog the experience.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    assert len(result.skills) == 1

    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()
    assert "description: log experience" in content
    assert "# Log Experience" in content


def test_translate_command_rejects_symlinked_file(make_plugin_version, tmp_path: Path):
    """Symlinked command files are rejected."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    real_file = tmp_path / "real.md"
    real_file.write_text("---\ndescription: test\n---\n")
    (commands_dir / "link.md").symlink_to(real_file)

    plugins = discover_latest_plugins(cache_root)
    with pytest.raises(TranslationError, match="symlink"):
        translate_installed_commands(plugins)


def test_translate_standalone_commands(tmp_path: Path):
    """User-level commands are translated with scope markers."""
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "my-cmd.md").write_text(
        "---\ndescription: My command\n---\n\nDo my thing.\n"
    )

    result = translate_standalone_commands(
        [commands_dir / "my-cmd.md"], scope="user"
    )
    assert len(result.skills) == 1
    assert result.skills[0].marketplace == "_user"
    assert result.skills[0].plugin_name == "personal"
    assert result.skills[0].install_dir_name == "cmd-my-cmd"


def test_translate_standalone_project_commands(tmp_path: Path):
    """Project-level commands use 'local' plugin_name."""
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "build.md").write_text(
        "---\ndescription: Build project\n---\n\nRun build.\n"
    )

    result = translate_standalone_commands(
        [commands_dir / "build.md"], scope="project"
    )
    assert len(result.skills) == 1
    assert result.skills[0].marketplace == "_project"
    assert result.skills[0].plugin_name == "local"


def test_translate_command_no_plugin_root_leaves_variable(make_plugin_version):
    """Standalone commands (no plugin_root) leave ${CLAUDE_PLUGIN_ROOT} as-is."""
    cmd_dir = make_plugin_version("m", "p", "1.0.0")[1].parent.parent.parent / "standalone"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_file = cmd_dir / "cmd.md"
    cmd_file.write_text(
        "---\ndescription: Test\n---\n\n"
        'Run: "${CLAUDE_PLUGIN_ROOT}/scripts/foo.sh"\n'
    )

    result = translate_standalone_commands([cmd_file], scope="user")
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()
    # For standalone commands, plugin_root is None, so variable stays
    assert "${CLAUDE_PLUGIN_ROOT}" in content


def test_translate_command_skill_name_has_cmd_prefix(make_plugin_version):
    """Command-derived skills are prefixed with cmd- to avoid namespace collisions."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "code-review.md").write_text(
        "---\ndescription: Review code\n---\n\nReview the code.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    assert len(result.skills) == 1

    skill = result.skills[0]
    assert skill.install_dir_name == "cmd-code-review"
    assert skill.original_skill_name == "cmd-code-review"
    assert skill.codex_skill_name == "cmd-code-review"

    skill_md = next(f for f in skill.files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()
    assert "name: cmd-code-review" in content


def test_translate_standalone_command_has_cmd_prefix(tmp_path):
    """Standalone commands also get the cmd- prefix."""
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "optimize.md").write_text(
        "---\ndescription: Optimize\n---\n\nOptimize things.\n"
    )

    result = translate_standalone_commands((commands_dir / "optimize.md",), scope="user")
    assert len(result.skills) == 1
    assert result.skills[0].install_dir_name == "cmd-optimize"


def test_translate_command_claude_plugin_root_uses_vendored_path(make_plugin_version, tmp_path):
    """${CLAUDE_PLUGIN_ROOT} points to vendored location when bridge_home is provided."""
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\ndescription: Run review\n---\n\n"
        'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pipeline.py"\n'
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "pipeline.py").write_text("print('review')\n")

    bridge = tmp_path / "bridge-home"
    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins, bridge_home=bridge)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert str(version_dir.resolve()) not in content
    assert str(bridge / "plugins" / "market-tools") in content
    assert len(result.plugin_resources) == 1
    assert result.plugin_resources[0].target_dir_name == "scripts"


def test_translate_command_plugin_root_variable_rewritten(make_plugin_version, tmp_path):
    """$PLUGIN_ROOT in commands is rewritten to vendored path."""
    cache_root, version_dir = make_plugin_version("market", "context-a8c", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "digest.md").write_text(
        "---\ndescription: Generate digest\n---\n\n"
        "$PLUGIN_ROOT/scripts/migrate-config.sh\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "migrate-config.sh").write_text("#!/bin/sh\necho migrate\n")

    bridge = tmp_path / "bridge-home"
    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins, bridge_home=bridge)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert '$PLUGIN_ROOT' not in content
    assert str(bridge / "plugins" / "market-context-a8c") in content
    assert len(result.plugin_resources) >= 1


def test_translate_command_discovery_block_removed(make_plugin_version, tmp_path):
    """The runtime discovery block is removed from command content."""
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "run.md").write_text(
        "---\ndescription: Run tool\n---\n\n"
        "PLUGIN_ROOT=$(cat /tmp/.tools-root 2>/dev/null)\n"
        '[ -z "$PLUGIN_ROOT" ] || [ ! -d "$PLUGIN_ROOT/scripts" ] && '
        'PLUGIN_ROOT=$(find ~/.claude -path "*/tools/*/scripts/run.py" '
        "-type f 2>/dev/null | sort | tail -1 | xargs dirname | xargs dirname)\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('run')\n")

    bridge = tmp_path / "bridge-home"
    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins, bridge_home=bridge)
    skill_md = next(f for f in result.skills[0].files if f.relative_path == Path("SKILL.md"))
    content = skill_md.content.decode()

    assert 'find ~/.claude' not in content
    assert '/tmp/.tools-root' not in content
    assert 'run.py' in content
