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
    assign_agent_names,
    format_agent_translation_diagnostics,
    translate_standalone_agents,
    translate_installed_agents,
    translate_installed_agents_with_diagnostics,
    validate_merged_agents,
)

def test_translate_installed_agents_produces_agent_files(make_plugin_version):
    """Plugin agents translate to GeneratedAgentFile with global scope and bare stem names."""
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
    assert agent.agent_name == "architecture-reviewer"
    assert agent.install_filename == "architecture-reviewer.toml"
    assert agent.marketplace == "market"
    assert agent.plugin_name == "pirategoat-tools"
    assert agent.description == "Software architecture review"
    assert agent.original_model_hint == "sonnet"
    assert agent.scope == "global"
    assert agent.sandbox_mode == "workspace-write"
    assert agent.developer_instructions == "You are an architecture reviewer.\n"


def test_translate_standalone_agents_produces_agent_files_user_scope(tmp_path: Path):
    """User agents translate to GeneratedAgentFile with global scope and bare stem names."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "my-helper.md").write_text(
        "---\nname: my-helper\ndescription: Helps with tasks\ntools:\n  - Read\n  - Bash\n---\n\nYou help.\n"
    )

    result = translate_standalone_agents((agents_dir / "my-helper.md",), scope="user")

    assert len(result.agents) == 1
    agent = result.agents[0]
    assert agent.agent_name == "my-helper"
    assert agent.install_filename == "my-helper.toml"
    assert agent.marketplace == "_user"
    assert agent.plugin_name == "personal"
    assert agent.scope == "global"
    assert agent.sandbox_mode == "workspace-write"
    assert agent.developer_instructions == "You help.\n"


def test_translate_standalone_agents_produces_agent_files_project_scope(tmp_path: Path):
    """Project agents translate to GeneratedAgentFile with project scope and bare stem names."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "code-reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews code\ntools:\n  - Read\n  - Grep\n---\n\nYou review code.\n"
    )

    result = translate_standalone_agents((agents_dir / "code-reviewer.md",), scope="project")

    assert len(result.agents) == 1
    agent = result.agents[0]
    assert agent.agent_name == "code-reviewer"
    assert agent.install_filename == "code-reviewer.toml"
    assert agent.marketplace == "_project"
    assert agent.plugin_name == "local"
    assert agent.scope == "project"
    assert agent.sandbox_mode == "read-only"
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

    assert by_name["writer"].sandbox_mode == "workspace-write"
    assert by_name["reader"].sandbox_mode == "read-only"
    assert by_name["minimal"].sandbox_mode is None


def test_translate_installed_agents_uses_file_stem_not_frontmatter_name(make_plugin_version):
    """Agent identity derives from the file stem, not the frontmatter name.

    A malicious frontmatter name like a path-traversal attempt has no effect
    on generated agent names or install filenames — the file stem is used.
    """
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

    # File stem is 'reviewer' regardless of frontmatter name
    assert agents[0].agent_name == "reviewer"
    assert agents[0].install_filename == "reviewer.toml"


def test_assign_agent_names_resolves_collisions_with_alt_suffix():
    """Same-stem agents from different plugins get -alt suffixes.

    Standalone agents (marketplace starts with _) win bare names.
    """
    user_agent = GeneratedAgentFile(
        marketplace="_user",
        plugin_name="personal",
        source_path=Path("/home/.claude/agents/reviewer.md"),
        scope="global",
        agent_name="reviewer",
        install_filename="reviewer.toml",
        description="User reviewer",
        developer_instructions="User prompt.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    plugin_agent = GeneratedAgentFile(
        marketplace="market",
        plugin_name="alpha",
        source_path=Path("/plugins/alpha/agents/reviewer.md"),
        scope="global",
        agent_name="reviewer",
        install_filename="reviewer.toml",
        description="Plugin reviewer",
        developer_instructions="Plugin prompt.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )

    result = assign_agent_names((user_agent, plugin_agent))

    assert len(result) == 2
    by_name = {a.agent_name: a for a in result}
    # User agent wins the bare name
    assert "reviewer" in by_name
    assert by_name["reviewer"].marketplace == "_user"
    # Plugin agent gets -alt suffix
    assert "reviewer-alt" in by_name
    assert by_name["reviewer-alt"].marketplace == "market"
    assert by_name["reviewer-alt"].install_filename == "reviewer-alt.toml"


def test_assign_agent_names_multiple_collisions():
    """Three-way collision produces bare, -alt, and -alt-2."""
    agents = tuple(
        GeneratedAgentFile(
            marketplace=f"market-{i}",
            plugin_name=f"plugin-{i}",
            source_path=Path(f"/plugins/{i}/agents/reviewer.md"),
            scope="global",
            agent_name="reviewer",
            install_filename="reviewer.toml",
            description=f"Reviewer {i}",
            developer_instructions=f"Prompt {i}.\n",
            sandbox_mode=None,
            original_model_hint=None,
        )
        for i in range(3)
    )

    result = assign_agent_names(agents)

    names = sorted(a.agent_name for a in result)
    assert names == ["reviewer", "reviewer-alt", "reviewer-alt-2"]


def test_assign_agent_names_rejects_overlong_names():
    """Agent names exceeding MAX_AGENT_NAME_LENGTH are rejected."""
    from cc_codex_bridge.translate_agents import MAX_AGENT_NAME_LENGTH

    long_stem = "a" * (MAX_AGENT_NAME_LENGTH + 1)
    agent = GeneratedAgentFile(
        marketplace="market",
        plugin_name="plugin",
        source_path=Path(f"/plugins/agents/{long_stem}.md"),
        scope="global",
        agent_name=long_stem,
        install_filename=f"{long_stem}.toml",
        description="Too long",
        developer_instructions="Prompt.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )

    with pytest.raises(TranslationError, match="exceeds"):
        assign_agent_names((agent,))


def test_assign_agent_names_no_collision():
    """Unique stems pass through without modification."""
    agents = tuple(
        GeneratedAgentFile(
            marketplace="market",
            plugin_name="plugin",
            source_path=Path(f"/plugins/agents/{name}.md"),
            scope="global",
            agent_name=name,
            install_filename=f"{name}.toml",
            description=f"Agent {name}",
            developer_instructions="Prompt.\n",
            sandbox_mode=None,
            original_model_hint=None,
        )
        for name in ("alpha", "beta", "gamma")
    )

    result = assign_agent_names(agents)

    assert sorted(a.agent_name for a in result) == ["alpha", "beta", "gamma"]


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


def test_translate_installed_agents_accepts_unrecognized_tools(make_plugin_version):
    """Unrecognized Claude tools are accepted — sandbox mode is derived from recognized tools only."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("mixed-tools",),
    )
    (version_dir / "agents" / "mixed-tools.md").write_text(
        "---\n"
        "name: mixed-tools\n"
        "description: Review\n"
        "tools:\n"
        "  - Read\n"
        "  - NotebookEdit\n"
        "  - Bash\n"
        "---\n\n"
        "Prompt body.\n"
    )

    result = translate_installed_agents_with_diagnostics(discover_latest_plugins(cache_root))

    assert len(result.agents) == 1
    assert result.diagnostics == ()
    # sandbox_mode derived from Read + Bash (write tool present)
    assert result.agents[0].sandbox_mode == "workspace-write"


def test_assign_agent_names_handles_same_stem_across_marketplaces(make_plugin_version):
    """Same file stem in different marketplaces gets collision-free names via -alt suffixes."""
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
    resolved = assign_agent_names(agents)

    names = sorted(a.agent_name for a in resolved)
    assert names == ["reviewer", "reviewer-alt"]


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
    # agent_name derives from file stem 'reviewer', not frontmatter name
    assert agents[0].agent_name == "reviewer"
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


def test_unsupported_tools_coerces_comma_separated_string():
    """Comma-separated string is split into a list of tool names."""
    from cc_codex_bridge.translate_agents import _unsupported_tools
    result = _unsupported_tools("Read, Write, Edit")
    assert result == ()


def test_unsupported_tools_coerces_single_string():
    """Single tool name as string is treated as a one-element list."""
    from cc_codex_bridge.translate_agents import _unsupported_tools
    result = _unsupported_tools("Read")
    assert result == ()


def test_unsupported_tools_accepts_unrecognized():
    """Unrecognized tool names are silently accepted."""
    from cc_codex_bridge.translate_agents import _unsupported_tools
    result = _unsupported_tools("Read, CustomTool, Write")
    assert result == ()


def test_unsupported_tools_accepts_mcp_tools():
    """MCP tool names are silently accepted."""
    from cc_codex_bridge.translate_agents import _unsupported_tools
    result = _unsupported_tools([
        "Read", "Write",
        "mcp__plugin_context-a8c_context-a8c__context-a8c-load-provider",
        "mcp__plugin_context-a8c_context-a8c__context-a8c-execute-tool",
    ])
    assert result == ()


def test_extract_tool_names_coerces_string():
    """_extract_tool_names handles comma-separated string input."""
    from cc_codex_bridge.translate_agents import _extract_tool_names
    result = _extract_tool_names("Read, Write, Edit")
    assert result == ("Read", "Write", "Edit")


def test_extract_tool_names_single_string():
    """_extract_tool_names handles single tool string."""
    from cc_codex_bridge.translate_agents import _extract_tool_names
    result = _extract_tool_names("Bash")
    assert result == ("Bash",)


def test_translate_standalone_agent_accepts_unrecognized_tools(tmp_path: Path):
    """Standalone agents with unrecognized tools translate successfully."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "mixed.md").write_text(
        "---\nname: mixed\ndescription: Review\ntools:\n  - Read\n  - NotebookEdit\n---\n\nPrompt.\n"
    )

    result = translate_standalone_agents((agents_dir / "mixed.md",), scope="user")

    assert len(result.agents) == 1
    assert result.diagnostics == ()
    # Only Read is recognized → read-only sandbox
    assert result.agents[0].sandbox_mode == "read-only"


def test_translate_standalone_agent_empty_input():
    """Empty agent paths produce empty result."""
    result = translate_standalone_agents((), scope="user")
    assert result.agents == ()
    assert result.diagnostics == ()


def test_validate_merged_agents_detects_name_collision():
    """Duplicate agent_name across scopes is rejected."""
    agent_a = GeneratedAgentFile(
        marketplace="market",
        plugin_name="alpha",
        source_path=Path("/plugins/a/agents/agent.md"),
        scope="global",
        agent_name="reviewer",
        install_filename="reviewer.toml",
        description="From plugin scope",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        marketplace="_user",
        plugin_name="personal",
        source_path=Path("/home/.claude/agents/reviewer.md"),
        scope="global",
        agent_name="reviewer",
        install_filename="reviewer-copy.toml",
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
        marketplace="market",
        plugin_name="alpha",
        source_path=Path("/plugins/a/agents/agent.md"),
        scope="global",
        agent_name="agent-alpha",
        install_filename="shared-agent.toml",
        description="First agent",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        marketplace="_user",
        plugin_name="personal",
        source_path=Path("/home/.claude/agents/agent.md"),
        scope="global",
        agent_name="agent-beta",
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
        marketplace="market",
        plugin_name="alpha",
        source_path=Path("/plugins/a/agents/alpha.md"),
        scope="global",
        agent_name="alpha",
        install_filename="alpha.toml",
        description="First agent",
        developer_instructions="Prompt A.\n",
        sandbox_mode=None,
        original_model_hint=None,
    )
    agent_b = GeneratedAgentFile(
        marketplace="_user",
        plugin_name="personal",
        source_path=Path("/home/.claude/agents/beta.md"),
        scope="global",
        agent_name="beta",
        install_filename="beta.toml",
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


def test_render_agent_toml_escapes_triple_quotes_in_body():
    """Triple-quote sequences in developer_instructions don't break TOML."""
    body_with_triple_quotes = 'Example:\n```toml\nfoo = """bar"""\n```\n'
    result = render_agent_toml(
        "toml-example-agent",
        "Shows TOML examples",
        body_with_triple_quotes,
    )
    # Must parse as valid TOML
    parsed = tomllib.loads(result)
    assert parsed["developer_instructions"] == body_with_triple_quotes
    assert parsed["name"] == "toml-example-agent"


def test_render_agent_toml_escapes_long_quote_runs():
    """Runs of 4+ quotes are also handled correctly."""
    body = 'Four quotes: """" and five: """"".\n'
    result = render_agent_toml("quote-agent", "Quotes", body)
    parsed = tomllib.loads(result)
    assert parsed["developer_instructions"] == body


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


def test_agent_plugin_root_references_are_rewritten(make_plugin_version, tmp_path):
    """Agents referencing $PLUGIN_ROOT/scripts/ get paths rewritten."""
    cache_root, version_dir = make_plugin_version(
        "market", "pirategoat-tools", "1.0.0",
        agent_names=("security-reviewer",),
    )
    # Create plugin-level scripts
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "bootstrap-reviewer.py").write_text("print('bootstrap')\n")

    (version_dir / "agents" / "security-reviewer.md").write_text(
        "---\nname: security-reviewer\ndescription: Security review\ntools:\n  - Read\n  - Bash\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent security-reviewer\n"
    )

    bridge = tmp_path / "bridge-home"
    result = translate_installed_agents_with_diagnostics(
        discover_latest_plugins(cache_root),
        bridge_home=bridge,
    )
    assert len(result.agents) == 1

    agent = result.agents[0]
    assert '$PLUGIN_ROOT' not in agent.developer_instructions
    assert 'bootstrap-reviewer.py' in agent.developer_instructions

    # Plugin resources should be collected (scripts directly + agents transitively)
    assert len(result.plugin_resources) >= 1
    resource = next(r for r in result.plugin_resources if r.target_dir_name == "scripts")
    assert resource.marketplace == "market"
    assert resource.plugin_name == "pirategoat-tools"
    assert resource.target_dir_name == "scripts"


def test_agent_shared_protocols_are_vendored_transitively(make_plugin_version, tmp_path):
    """Agents with bootstrap scripts that reference agents/shared/ get those vendored too."""
    cache_root, version_dir = make_plugin_version(
        "market", "pirategoat-tools", "1.0.0",
        agent_names=("security-reviewer",),
    )
    # Create plugin-level scripts with bootstrap that references agents/shared/
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "bootstrap-reviewer.py").write_text(
        'import os\n'
        'plugin_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n'
        'protocol_path = os.path.join(plugin_root, "agents", "shared", "reviewer-protocol.md")\n'
    )

    # Create agents/shared/ protocols
    shared_dir = version_dir / "agents" / "shared"
    shared_dir.mkdir(parents=True, exist_ok=True)
    (shared_dir / "reviewer-protocol.md").write_text("# Protocol\nReview rules here.\n")

    (version_dir / "agents" / "security-reviewer.md").write_text(
        "---\nname: security-reviewer\ndescription: Security review\ntools:\n  - Read\n  - Bash\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/bootstrap-reviewer.py --agent security-reviewer\n"
    )

    bridge = tmp_path / "bridge-home"
    result = translate_installed_agents_with_diagnostics(
        discover_latest_plugins(cache_root),
        bridge_home=bridge,
    )

    # Should have scripts + agents vendored (transitive dep)
    assert len(result.plugin_resources) >= 2
    vendored_dirs = {r.target_dir_name for r in result.plugin_resources}
    assert "scripts" in vendored_dirs
    assert "agents" in vendored_dirs

    # Verify the agents/shared/ protocol file is in the vendored resources
    agents_resource = next(r for r in result.plugin_resources if r.target_dir_name == "agents")
    protocol_files = [f for f in agents_resource.files if "reviewer-protocol" in str(f.relative_path)]
    assert len(protocol_files) == 1
    assert b"Review rules here." in protocol_files[0].content
