"""Tests for agent translation and rendering."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from cc_codex_bridge.discover import discover_latest_plugins
from cc_codex_bridge.frontmatter import (
    parse_frontmatter_lines,
    parse_markdown_with_frontmatter,
)
from cc_codex_bridge.model import GeneratedAgentFile, InstalledPlugin, SemVer, TranslationError
from cc_codex_bridge.render_agent_toml import derive_sandbox_mode, render_agent_toml
from cc_codex_bridge.translate_agents import (
    format_agent_translation_diagnostics,
    translate_standalone_agents,
    translate_tools,
    translate_installed_agents,
    translate_installed_agents_with_diagnostics,
    validate_merged_agents,
)

def test_translate_installed_agents_produces_agent_files(make_plugin_version):
    """Plugin agents translate to GeneratedAgentFile with global scope."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        agent_names=("architecture-reviewer",),
    )
    agent_path = version_dir / "agents" / "architecture-reviewer.md"
    agent_path.write_text(
        "---\n"
        "name: architecture-reviewer\n"
        "description: Software architecture review\n"
        "model: sonnet\n"
        "tools:\n"
        "  - Read\n"
        "  - Bash\n"
        "  - WebSearch\n"
        "---\n\n"
        "You are an architecture reviewer.\n"
    )

    plugins = discover_latest_plugins(cache_root)
    agents = translate_installed_agents(plugins)

    assert len(agents) == 1
    agent = agents[0]
    assert isinstance(agent, GeneratedAgentFile)
    assert agent.agent_name == "market_pirategoat-tools_architecture_reviewer"
    assert agent.description == "Software architecture review"
    assert agent.original_model_hint == "sonnet"
    assert agent.scope == "global"
    assert agent.sandbox_mode == "workspace-write"
    assert agent.install_filename == "market-pirategoat-tools-architecture-reviewer.toml"
    assert agent.developer_instructions == "You are an architecture reviewer.\n"


def test_translate_standalone_agents_produces_agent_files_user_scope(tmp_path: Path):
    """User agents translate to GeneratedAgentFile with global scope."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "my-helper.md").write_text(
        "---\nname: my-helper\ndescription: Helps with tasks\ntools:\n  - Read\n  - Bash\n---\n\nYou help.\n"
    )

    result = translate_standalone_agents((agents_dir / "my-helper.md",), scope="user")

    assert len(result.agents) == 1
    agent = result.agents[0]
    assert agent.agent_name == "user_my_helper"
    assert agent.scope == "global"
    assert agent.sandbox_mode == "workspace-write"
    assert agent.install_filename == "user-my-helper.toml"
    assert agent.developer_instructions == "You help.\n"


def test_translate_standalone_agents_produces_agent_files_project_scope(tmp_path: Path):
    """Project agents translate to GeneratedAgentFile with project scope."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "code-reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews code\ntools:\n  - Read\n  - Grep\n---\n\nYou review code.\n"
    )

    result = translate_standalone_agents((agents_dir / "code-reviewer.md",), scope="project")

    assert len(result.agents) == 1
    agent = result.agents[0]
    assert agent.agent_name == "project_code_reviewer"
    assert agent.scope == "project"
    assert agent.sandbox_mode == "read-only"
    assert agent.install_filename == "project-code-reviewer.toml"
    assert agent.developer_instructions == "You review code.\n"


def test_agent_file_sandbox_mode_derived_from_tools(make_plugin_version):
    """sandbox_mode is derived from the Claude tool list."""
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", agent_names=("writer", "reader", "minimal"),
    )
    (version_dir / "agents" / "writer.md").write_text(
        "---\nname: writer\ndescription: Writes\ntools:\n  - Read\n  - Write\n  - Bash\n---\n\nWrite.\n"
    )
    (version_dir / "agents" / "reader.md").write_text(
        "---\nname: reader\ndescription: Reads\ntools:\n  - Read\n  - Grep\n---\n\nRead.\n"
    )
    (version_dir / "agents" / "minimal.md").write_text(
        "---\nname: minimal\ndescription: Minimal\n---\n\nMinimal.\n"
    )

    agents = translate_installed_agents(discover_latest_plugins(cache_root))
    by_name = {a.agent_name: a for a in agents}

    assert by_name["market_test-plugin_writer"].sandbox_mode == "workspace-write"
    assert by_name["market_test-plugin_reader"].sandbox_mode == "read-only"
    assert by_name["market_test-plugin_minimal"].sandbox_mode is None


def test_translate_installed_agents_sanitizes_generated_names_and_paths(make_plugin_version):
    """Unsafe agent names are normalized before agent names and install filenames are generated."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\n"
        "name: ../../../../tmp/pwn\n"
        "description: Review\n"
        "---\n\n"
        "Prompt body.\n"
    )

    agents = translate_installed_agents(discover_latest_plugins(cache_root))

    assert agents[0].agent_name == "market_test-plugin_tmp_pwn"
    assert agents[0].install_filename == "market-test-plugin-tmp-pwn.toml"


def test_translate_installed_agents_always_includes_marketplace_prefix(
    make_plugin_version,
):
    """Marketplace prefix is always included in generated agent names."""
    cache_root, alpha_dir = make_plugin_version(
        "alpha",
        "shared-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    _, beta_dir = make_plugin_version(
        "beta",
        "shared-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    for agent_path in (alpha_dir / "agents" / "reviewer.md", beta_dir / "agents" / "reviewer.md"):
        agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nPrompt.\n")

    agents = translate_installed_agents(discover_latest_plugins(cache_root))

    assert [a.agent_name for a in agents] == [
        "alpha_shared-plugin_reviewer",
        "beta_shared-plugin_reviewer",
    ]
    assert [a.install_filename for a in agents] == [
        "alpha-shared-plugin-reviewer.toml",
        "beta-shared-plugin-reviewer.toml",
    ]


def test_parse_markdown_with_frontmatter_supports_folded_scalars_and_nested_maps(tmp_path: Path):
    """The shared frontmatter parser accepts folded scalars and nested YAML mappings."""
    path = tmp_path / "skill.md"
    path.write_text(
        "---\n"
        "name: knowledge-capture\n"
        "description: >\n"
        "  Shared dex logic for project discovery,\n"
        "  CLAUDE.md budget management, and promotion flow.\n"
        "metadata:\n"
        "  prompts:\n"
        "    short-description: Shared dex guidance\n"
        "    references:\n"
        "      guide: references/guide.md\n"
        "---\n\n"
        "Body.\n"
    )

    frontmatter, body = parse_markdown_with_frontmatter(path)

    assert frontmatter["name"] == "knowledge-capture"
    assert frontmatter["description"] == (
        "Shared dex logic for project discovery, "
        "CLAUDE.md budget management, and promotion flow."
    )
    assert frontmatter["metadata"] == {
        "prompts": {
            "short-description": "Shared dex guidance",
            "references": {"guide": "references/guide.md"},
        }
    }
    assert body == "\nBody."


def test_translate_installed_agents_requires_name_and_description(make_plugin_version):
    """Missing required frontmatter fields fail clearly."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("broken",),
    )
    (version_dir / "agents" / "broken.md").write_text("---\ndescription: Missing name\n---\n")

    with pytest.raises(TranslationError, match="missing required name"):
        translate_installed_agents(discover_latest_plugins(cache_root))

    (version_dir / "agents" / "broken.md").write_text("---\nname: broken\n---\n")

    with pytest.raises(TranslationError, match="missing required description"):
        translate_installed_agents(discover_latest_plugins(cache_root))


def test_translate_installed_agents_reports_unsupported_tools(make_plugin_version):
    """Unsupported Claude tools become explicit diagnostics and strict translation errors."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
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
        "  - Bash\n"
        "---\n\n"
        "Prompt body.\n"
    )

    result = translate_installed_agents_with_diagnostics(discover_latest_plugins(cache_root))

    assert result.agents == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].source_path == version_dir / "agents" / "broken.md"
    assert result.diagnostics[0].agent_name == "broken"
    assert result.diagnostics[0].unsupported_tools == ("NotebookEdit",)
    assert "unsupported Claude tools: NotebookEdit" in format_agent_translation_diagnostics(
        result.diagnostics
    )

    with pytest.raises(TranslationError, match="unsupported Claude tools: NotebookEdit"):
        translate_installed_agents(discover_latest_plugins(cache_root))


def test_translate_installed_agents_detects_duplicate_agent_names(make_plugin_version):
    """Agent-name collisions across plugins are rejected."""
    cache_root, version_dir = make_plugin_version("market", "alpha", "1.0.0", agent_names=("same",))
    first_agent = version_dir / "agents" / "same.md"
    first_agent.write_text("---\nname: same-role\ndescription: First\n---\n\nPrompt.\n")
    second_agent = version_dir / "agents" / "same-again.md"
    second_agent.write_text("---\nname: same role\ndescription: Second\n---\n\nPrompt.\n")

    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=version_dir,
        source_path=version_dir,
        skills=(),
        agents=(first_agent, second_agent),
    )

    with pytest.raises(TranslationError, match="duplicate agent name"):
        translate_installed_agents((plugin,))


def test_parse_markdown_with_frontmatter_handles_literal_blocks_and_errors(tmp_path: Path):
    """Parser covers literal blocks and malformed frontmatter."""
    literal = tmp_path / "literal.md"
    literal.write_text(
        "---\n"
        "name: literal\n"
        "description: |\n"
        "  line one\n"
        "  line two\n"
        "---\n\n"
        "Body.\n"
    )
    frontmatter, body = parse_markdown_with_frontmatter(literal)
    assert frontmatter["description"] == "line one\nline two"
    assert body == "\nBody."

    no_frontmatter = tmp_path / "plain.md"
    no_frontmatter.write_text("Plain body.\n")
    frontmatter, body = parse_markdown_with_frontmatter(no_frontmatter)
    assert frontmatter == {}
    assert body == "Plain body.\n"

    unclosed = tmp_path / "unclosed.md"
    unclosed.write_text("---\nname: broken\n")
    with pytest.raises(TranslationError, match="Unclosed frontmatter"):
        parse_markdown_with_frontmatter(unclosed)


def test_parse_markdown_with_frontmatter_rejects_malformed_yaml_syntax(tmp_path: Path):
    """Malformed YAML frontmatter surfaces as a translation error."""
    broken = tmp_path / "broken.md"
    broken.write_text(
        "---\n"
        "name: [reviewer\n"
        "description: Review\n"
        "---\n"
    )

    with pytest.raises(TranslationError, match="Malformed frontmatter YAML"):
        parse_markdown_with_frontmatter(broken)


def test_translate_tools_rejects_invalid_shapes():
    """Tool translation handles invalid non-list or non-string inputs."""
    assert translate_tools(None) == ()
    assert translate_tools(["Write", "Read", "Read", "Unknown"]) == ("read", "write")

    with pytest.raises(TranslationError, match="must be a list"):
        translate_tools("Read")

    with pytest.raises(TranslationError, match="must be a string"):
        translate_tools(["Read", 1])


def test_translate_tools_maps_edit_to_codex_edit():
    """The Claude Edit tool translates to the Codex edit tool."""
    assert translate_tools(["Read", "Edit", "Write"]) == ("edit", "read", "write")
    assert translate_tools(["Edit"]) == ("edit",)


def test_translate_installed_agents_accepts_edit_tool(make_plugin_version):
    """Agents using the Edit tool translate successfully instead of producing diagnostics."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("mutation-tester",),
    )
    (version_dir / "agents" / "mutation-tester.md").write_text(
        "---\n"
        "name: mutation-tester\n"
        "description: Mutation testing\n"
        "tools:\n"
        "  - Read\n"
        "  - Edit\n"
        "  - Write\n"
        "  - Bash\n"
        "---\n\n"
        "You mutate code.\n"
    )

    result = translate_installed_agents_with_diagnostics(discover_latest_plugins(cache_root))

    assert result.diagnostics == ()
    assert len(result.agents) == 1
    assert result.agents[0].sandbox_mode == "workspace-write"


def test_translate_installed_agents_accepts_quoted_fields_and_inline_tool_lists(
    make_plugin_version,
):
    """Quoted scalars and inline lists in frontmatter still translate cleanly."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\n"
        'name: "reviewer"\n'
        "description: 'Review: carefully'\n"
        "tools: [Write, Read]\n"
        "---\n\n"
        "Prompt body.\n"
    )

    agents = translate_installed_agents(discover_latest_plugins(cache_root))

    assert len(agents) == 1
    assert agents[0].agent_name == "market_test-plugin_reviewer"
    assert agents[0].description == "Review: carefully"
    assert agents[0].sandbox_mode == "workspace-write"


def test_parse_frontmatter_lines_accepts_quoted_strings_and_inline_lists():
    """Low-level frontmatter parsing keeps quoted strings and inline lists usable."""
    parsed = parse_frontmatter_lines(
        [
            'name: "reviewer"',
            "description: 'Review: carefully'",
            "tools: [Write, Read]",
        ]
    )

    assert parsed == {
        "name": "reviewer",
        "description": "Review: carefully",
        "tools": ["Write", "Read"],
    }


def test_parse_frontmatter_lines_normalizes_yaml_scalars_to_strings():
    """Low-level frontmatter parsing keeps scalar runtime values string-shaped."""
    parsed = parse_frontmatter_lines(
        [
            "model: true",
            "priority: 3",
            "released: 2026-03-09",
        ]
    )

    assert parsed == {
        "model": "true",
        "priority": "3",
        "released": "2026-03-09",
    }


def test_parse_frontmatter_lines_rejects_non_mapping_payloads():
    """Low-level frontmatter parsing rejects unsupported top-level YAML payloads."""
    with pytest.raises(TranslationError, match="Frontmatter must be a YAML mapping"):
        parse_frontmatter_lines(["- Read"])

    with pytest.raises(TranslationError, match="Frontmatter must be a YAML mapping"):
        parse_frontmatter_lines(["reviewer"])


def test_parse_frontmatter_lines_rejects_unsupported_yaml_runtime_shapes():
    """Low-level frontmatter validation rejects non-string/list/mapping YAML values."""
    with pytest.raises(
        TranslationError,
        match="Unsupported frontmatter value at frontmatter.options: set",
    ):
        parse_frontmatter_lines(["options: !!set {Read: null}"])


def test_translate_standalone_agent_with_unsupported_tools(tmp_path: Path):
    """Standalone agents with unsupported tools produce diagnostics."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "broken.md").write_text(
        "---\nname: broken\ndescription: Review\ntools:\n  - Read\n  - NotebookEdit\n---\n\nPrompt.\n"
    )

    result = translate_standalone_agents((agents_dir / "broken.md",), scope="user")

    assert result.agents == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].unsupported_tools == ("NotebookEdit",)


def test_translate_standalone_agents_rejects_duplicate_normalized_agent_names(tmp_path: Path):
    """Standalone agents with names that normalize to the same agent name are rejected."""
    a = tmp_path / "same-role.md"
    b = tmp_path / "same role.md"
    a.write_text(
        "---\nname: same-role\ndescription: First agent\n---\n\nPrompt A\n"
    )
    b.write_text(
        "---\nname: same role\ndescription: Second agent\n---\n\nPrompt B\n"
    )

    with pytest.raises(TranslationError, match="duplicate agent name"):
        translate_standalone_agents((a, b), scope="user")


def test_translate_installed_agents_detects_duplicate_install_filenames(make_plugin_version):
    """Install-filename collisions across plugins are rejected even when agent names differ.

    Plugin "a-b" with agent "c" and plugin "a" with agent "b-c" produce
    distinct agent names (market_a-b_c vs market_a_b_c) but identical
    install filenames (market-a-b-c.toml), which would silently
    overwrite one agent file.
    """
    cache_root, first_dir = make_plugin_version(
        "market", "a-b", "1.0.0", agent_names=("c",)
    )
    first_agent = first_dir / "agents" / "c.md"
    first_agent.write_text(
        "---\nname: c\ndescription: First\n---\n\nPrompt.\n"
    )

    _, second_dir = make_plugin_version(
        "market", "a", "1.0.0", agent_names=("b-c",)
    )
    second_agent = second_dir / "agents" / "b-c.md"
    second_agent.write_text(
        "---\nname: b-c\ndescription: Second\n---\n\nPrompt.\n"
    )

    first_plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="a-b",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=first_dir,
        source_path=first_dir,
        skills=(),
        agents=(first_agent,),
    )
    second_plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="a",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=second_dir,
        source_path=second_dir,
        skills=(),
        agents=(second_agent,),
    )

    with pytest.raises(TranslationError, match="duplicate install filename"):
        translate_installed_agents((first_plugin, second_plugin))


def test_translate_standalone_agent_empty_input():
    """Empty agent paths produce empty result."""
    result = translate_standalone_agents((), scope="user")
    assert result.agents == ()
    assert result.diagnostics == ()


def test_validate_merged_agents_detects_name_collision():
    """Duplicate agent_name across scopes is rejected."""
    agent_a = GeneratedAgentFile(
        source_path=Path("/plugins/a/agents/agent.md"),
        scope="global",
        agent_name="user_plugin_agent",
        install_filename="plugin-a-agent.toml",
        description="From plugin scope",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        source_path=Path("/home/.claude/agents/plugin-agent.md"),
        scope="global",
        agent_name="user_plugin_agent",
        install_filename="user-plugin-agent.toml",
        description="From user scope",
        developer_instructions="Prompt B.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )

    with pytest.raises(TranslationError, match="Duplicate agent name"):
        validate_merged_agents((agent_a, agent_b))


def test_validate_merged_agents_detects_filename_collision():
    """Duplicate install_filename across scopes is rejected."""
    agent_a = GeneratedAgentFile(
        source_path=Path("/plugins/a/agents/agent.md"),
        scope="global",
        agent_name="agent_alpha",
        install_filename="shared-agent.toml",
        description="First agent",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        source_path=Path("/home/.claude/agents/agent.md"),
        scope="global",
        agent_name="agent_beta",
        install_filename="shared-agent.toml",
        description="Second agent",
        developer_instructions="Prompt B.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )

    with pytest.raises(TranslationError, match="Duplicate install filename"):
        validate_merged_agents((agent_a, agent_b))


def test_validate_merged_agents_accepts_unique_agents():
    """Unique agent names and install filenames pass validation."""
    agent_a = GeneratedAgentFile(
        source_path=Path("/plugins/a/agents/agent.md"),
        scope="global",
        agent_name="plugin_agent_alpha",
        install_filename="plugin-a-alpha.toml",
        description="First agent",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        source_path=Path("/home/.claude/agents/beta.md"),
        scope="global",
        agent_name="user_agent_beta",
        install_filename="user-beta.toml",
        description="Second agent",
        developer_instructions="Prompt B.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )

    # Should not raise
    validate_merged_agents((agent_a, agent_b))


# --- render_agent_toml and derive_sandbox_mode tests ---


def test_render_agent_toml_produces_valid_toml():
    """Agent .toml rendering includes all required fields."""
    result = render_agent_toml(
        "my-agent",
        "A helpful agent",
        "You are a helpful agent.\n",
    )
    assert 'name = "my-agent"' in result
    assert 'description = "A helpful agent"' in result
    assert 'developer_instructions = """\nYou are a helpful agent.\n"""' in result
    assert "sandbox_mode" not in result
    assert result.startswith("# GENERATED FILE")

    # Verify it parses as valid TOML
    parsed = tomllib.loads(result)
    assert parsed["name"] == "my-agent"
    assert parsed["description"] == "A helpful agent"
    assert parsed["developer_instructions"] == "You are a helpful agent.\n"


def test_render_agent_toml_includes_sandbox_mode_when_present():
    """sandbox_mode is rendered when set."""
    result = render_agent_toml(
        "writer-agent",
        "Writes code",
        "You write code.\n",
        sandbox_mode="workspace-write",
    )
    assert 'sandbox_mode = "workspace-write"' in result

    parsed = tomllib.loads(result)
    assert parsed["sandbox_mode"] == "workspace-write"


def test_render_agent_toml_omits_sandbox_mode_when_none():
    """sandbox_mode is omitted when not set (inherit from parent)."""
    result = render_agent_toml(
        "reader-agent",
        "Reads code",
        "You read code.\n",
        sandbox_mode=None,
    )
    assert "sandbox_mode" not in result


def test_render_agent_toml_escapes_multiline_instructions():
    """Multiline developer_instructions are rendered as TOML multiline strings."""
    result = render_agent_toml(
        "multi-agent",
        "Multi-line desc with \"quotes\"",
        "Line one.\nLine two.\nLine three.\n",
    )
    # Description quotes should be escaped in the basic string
    assert 'description = "Multi-line desc with \\"quotes\\""' in result

    parsed = tomllib.loads(result)
    assert parsed["description"] == 'Multi-line desc with "quotes"'
    assert parsed["developer_instructions"] == "Line one.\nLine two.\nLine three.\n"


def test_derive_sandbox_mode_write_tools():
    """Write-capable tools produce workspace-write."""
    assert derive_sandbox_mode(("Read", "Bash", "Write")) == "workspace-write"
    assert derive_sandbox_mode(("Edit",)) == "workspace-write"
    assert derive_sandbox_mode(("Bash",)) == "workspace-write"
    assert derive_sandbox_mode(("Write",)) == "workspace-write"


def test_derive_sandbox_mode_read_only_tools():
    """Read-only tools produce read-only."""
    assert derive_sandbox_mode(("Read", "Grep", "Glob")) == "read-only"
    assert derive_sandbox_mode(("Read",)) == "read-only"
    assert derive_sandbox_mode(("WebSearch",)) == "read-only"


def test_derive_sandbox_mode_no_tools():
    """No tools returns None (inherit from parent)."""
    assert derive_sandbox_mode(None) is None
    assert derive_sandbox_mode(()) is None
