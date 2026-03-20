"""Tests for command-to-prompt translation."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.discover import discover_latest_plugins
from cc_codex_bridge.model import GeneratedPrompt
from cc_codex_bridge.translate_prompts import (
    PROVENANCE_MARKER,
    translate_installed_commands,
    translate_standalone_commands,
)


def test_translate_command_produces_prompt(make_plugin_version):
    """A command markdown file translates into a GeneratedPrompt."""
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
    assert len(result.prompts) == 1

    prompt = result.prompts[0]
    assert prompt.marketplace == "market"
    assert prompt.plugin_name == "pirategoat-tools"
    assert prompt.filename == "code-review.md"

    content = prompt.content.decode()
    assert "description: 'Review code incrementally'" in content
    assert "Review the code." in content


def test_translate_command_preserves_arguments(make_plugin_version):
    """$ARGUMENTS, $1, $2 pass through unchanged — Codex supports them natively."""
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
        "First arg: $1\nSecond arg: $2\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    content = result.prompts[0].content.decode()

    assert "$ARGUMENTS" in content
    assert "$1" in content
    assert "$2" in content


def test_translate_command_preserves_argument_hint(make_plugin_version):
    """argument-hint appears in the output prompt frontmatter."""
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
    content = result.prompts[0].content.decode()

    assert "argument-hint: '[PR_URL]'" in content


def test_translate_command_argument_hint_quoted_for_yaml_safety(make_plugin_version):
    """argument-hint with brackets is quoted to prevent YAML sequence interpretation."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "cmd.md").write_text(
        "---\n"
        "description: Test\n"
        "argument-hint: '[person] [month]'\n"
        "---\n\nDo things.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    content = result.prompts[0].content.decode()

    # Must be quoted so YAML parsers read it as a string, not a sequence
    assert "argument-hint: '[person] [month]'" in content


def test_translate_command_drops_allowed_tools(make_plugin_version):
    """allowed-tools is not present in the output prompt frontmatter."""
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
    content = result.prompts[0].content.decode()

    assert "allowed-tools" not in content


def test_translate_command_no_cmd_prefix(make_plugin_version):
    """Prompt filename is 'code-review.md', not 'cmd-code-review.md'."""
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
    assert result.prompts[0].filename == "code-review.md"
    assert not result.prompts[0].filename.startswith("cmd-")


def test_translate_command_appends_provenance_marker(make_plugin_version):
    """Translated commands include the bridge provenance marker at the end."""
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
    content = result.prompts[0].content.decode()

    assert content.rstrip().endswith(
        "<!-- bridge: translated from Claude Code command -->"
    )


def test_translate_command_replaces_plugin_root(make_plugin_version):
    """${CLAUDE_PLUGIN_ROOT} is replaced with the resolved plugin path (no bridge_home)."""
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
    content = result.prompts[0].content.decode()

    assert "${CLAUDE_PLUGIN_ROOT}" not in content
    assert str(version_dir.resolve()) in content


def test_translate_command_vendors_plugin_resources(make_plugin_version, tmp_path):
    """With bridge_home, vendoring works and ${CLAUDE_PLUGIN_ROOT} points to vendored location."""
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
    content = result.prompts[0].content.decode()

    assert str(version_dir.resolve()) not in content
    assert str(bridge / "plugins" / "market-tools") in content
    assert len(result.plugin_resources) == 1
    assert result.plugin_resources[0].target_dir_name == "scripts"


def test_translate_standalone_user_commands(tmp_path: Path):
    """User-level commands are translated with scope markers; filename is stem.md."""
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "my-cmd.md").write_text(
        "---\ndescription: My command\n---\n\nDo my thing.\n"
    )

    result = translate_standalone_commands(
        [commands_dir / "my-cmd.md"], scope="user"
    )
    assert len(result.prompts) == 1
    assert result.prompts[0].marketplace == "_user"
    assert result.prompts[0].plugin_name == "personal"
    assert result.prompts[0].filename == "my-cmd.md"


def test_translate_standalone_project_commands(tmp_path: Path):
    """Project scope with project_dir_name gets '--suffix' in filename."""
    commands_dir = tmp_path / "commands"
    commands_dir.mkdir()
    (commands_dir / "build.md").write_text(
        "---\ndescription: Build project\n---\n\nRun build.\n"
    )

    result = translate_standalone_commands(
        [commands_dir / "build.md"],
        scope="project",
        project_dir_name="my-app",
    )
    assert len(result.prompts) == 1
    assert result.prompts[0].marketplace == "_project"
    assert result.prompts[0].plugin_name == "local"
    assert result.prompts[0].filename == "build--my-app.md"


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
    assert len(result.prompts) == 1

    content = result.prompts[0].content.decode()
    assert "description: 'consolidate ai memory'" in content


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
    assert len(result.prompts) == 1

    content = result.prompts[0].content.decode()
    assert "description: 'log experience'" in content
    assert "# Log Experience" in content


def test_translate_command_follows_symlinked_file(make_plugin_version, tmp_path: Path):
    """Symlinked command files are followed during translation."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    real_file = tmp_path / "real.md"
    real_file.write_text("---\ndescription: test\n---\n\nDo the thing.\n")
    (commands_dir / "link.md").symlink_to(real_file)

    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins)
    assert len(result.prompts) == 1


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
    content = result.prompts[0].content.decode()

    assert "$PLUGIN_ROOT" not in content
    assert str(bridge / "plugins" / "market-context-a8c") in content
    assert len(result.plugin_resources) >= 1


def test_translate_command_vendors_root_level_files(make_plugin_version, tmp_path):
    """Root-level plugin files referenced via ${CLAUDE_PLUGIN_ROOT} are vendored."""
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "run.md").write_text(
        "---\ndescription: Run tool\n---\n\n"
        'python3 "${CLAUDE_PLUGIN_ROOT}/run.py" --help\n'
    )
    # Root-level file (not inside a subdirectory)
    (version_dir / "run.py").write_text("print('hello')\n")

    bridge = tmp_path / "bridge-home"
    plugins = discover_latest_plugins(cache_root)
    result = translate_installed_commands(plugins, bridge_home=bridge)
    content = result.prompts[0].content.decode()

    # Path should point to vendored _root dir
    vendored_root = str(bridge / "plugins" / "market-tools")
    assert f"{vendored_root}/_root/run.py" in content
    # Raw cache path should not be present
    assert str(version_dir.resolve()) not in content

    # A _root resource should be created
    root_resources = [r for r in result.plugin_resources if r.target_dir_name == "_root"]
    assert len(root_resources) == 1
    assert any(f.relative_path == Path("run.py") for f in root_resources[0].files)


def test_assign_prompt_names_no_collision():
    """Non-colliding prompts keep their filenames."""
    from cc_codex_bridge.translate_prompts import assign_prompt_names

    prompts = (
        GeneratedPrompt(
            filename="review.md",
            content=b"---\ndescription: A\n---\n\nA.\n",
            source_path=Path("/p1/commands/review.md"),
            marketplace="_user",
            plugin_name="personal",
        ),
        GeneratedPrompt(
            filename="build.md",
            content=b"---\ndescription: B\n---\n\nB.\n",
            source_path=Path("/p2/commands/build.md"),
            marketplace="market",
            plugin_name="tools",
        ),
    )
    result = assign_prompt_names(prompts)
    filenames = sorted(p.filename for p in result)
    assert filenames == ["build.md", "review.md"]


def test_assign_prompt_names_resolves_collisions():
    """Two prompts with same filename get -alt suffix."""
    from cc_codex_bridge.translate_prompts import assign_prompt_names

    prompts = (
        GeneratedPrompt(
            filename="review.md",
            content=b"---\ndescription: A\n---\n\nA.\n",
            source_path=Path("/p1/commands/review.md"),
            marketplace="_user",
            plugin_name="personal",
        ),
        GeneratedPrompt(
            filename="review.md",
            content=b"---\ndescription: B\n---\n\nB.\n",
            source_path=Path("/p2/commands/review.md"),
            marketplace="market",
            plugin_name="tools",
        ),
    )
    result = assign_prompt_names(prompts)
    filenames = sorted(p.filename for p in result)
    assert "review.md" in filenames
    assert "review-alt.md" in filenames


def test_assign_prompt_names_user_wins_bare_name():
    """User-scope prompts get the bare name over plugin prompts."""
    from cc_codex_bridge.translate_prompts import assign_prompt_names

    prompts = (
        GeneratedPrompt(
            filename="review.md",
            content=b"---\ndescription: Plugin\n---\n\nPlugin.\n",
            source_path=Path("/p1/commands/review.md"),
            marketplace="market",
            plugin_name="tools",
        ),
        GeneratedPrompt(
            filename="review.md",
            content=b"---\ndescription: User\n---\n\nUser.\n",
            source_path=Path("/p2/commands/review.md"),
            marketplace="_user",
            plugin_name="personal",
        ),
    )
    result = assign_prompt_names(prompts)
    user_prompt = next(p for p in result if p.marketplace == "_user")
    plugin_prompt = next(p for p in result if p.marketplace == "market")
    assert user_prompt.filename == "review.md"
    assert plugin_prompt.filename == "review-alt.md"


def test_assign_prompt_names_triple_collision():
    """Three-way collision gets -alt and -alt-2 suffixes."""
    from cc_codex_bridge.translate_prompts import assign_prompt_names

    prompts = (
        GeneratedPrompt(
            filename="review.md",
            content=b"A",
            source_path=Path("/p1/commands/review.md"),
            marketplace="_user",
            plugin_name="personal",
        ),
        GeneratedPrompt(
            filename="review.md",
            content=b"B",
            source_path=Path("/p2/commands/review.md"),
            marketplace="alpha",
            plugin_name="tools",
        ),
        GeneratedPrompt(
            filename="review.md",
            content=b"C",
            source_path=Path("/p3/commands/review.md"),
            marketplace="beta",
            plugin_name="tools",
        ),
    )
    result = assign_prompt_names(prompts)
    filenames = sorted(p.filename for p in result)
    assert filenames == ["review-alt-2.md", "review-alt.md", "review.md"]
