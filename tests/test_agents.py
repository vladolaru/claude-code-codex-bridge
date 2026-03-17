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
from cc_codex_bridge.model import GeneratedAgentFile, GeneratedAgentRole, InstalledPlugin, SemVer, TranslationError
from cc_codex_bridge.render_agent_toml import derive_sandbox_mode, render_agent_toml
from cc_codex_bridge.render_codex_config import (
    render_inline_codex_config,
    render_prompt_files,
)
from cc_codex_bridge.translate_agents import (
    format_agent_translation_diagnostics,
    translate_standalone_agents,
    translate_tools,
    translate_installed_agents,
    translate_installed_agents_with_diagnostics,
    validate_merged_roles,
)

def test_translate_installed_agents_generates_deterministic_roles(make_plugin_version):
    """Claude agents translate to deterministic Codex role objects."""
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
    roles = translate_installed_agents(plugins)

    assert len(roles) == 1
    role = roles[0]
    assert role.role_name == "market_pirategoat-tools_architecture_reviewer"
    assert role.description == "Software architecture review"
    assert role.original_model_hint == "sonnet"
    assert role.model == "gpt-5.3-codex"
    assert role.tools == ("bash", "read", "web_search")
    assert role.prompt_relpath.as_posix() == "prompts/agents/market-pirategoat-tools-architecture-reviewer.md"
    assert role.prompt_body == "You are an architecture reviewer.\n"


def test_render_prompt_files_uses_dot_codex_relative_paths(make_plugin_version):
    """Rendered prompt files land under `.codex/prompts/agents/`."""
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", agent_names=("reviewer",)
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    roles = translate_installed_agents(discover_latest_plugins(cache_root))
    prompt_files = render_prompt_files(roles)

    assert prompt_files == {
        Path(".codex/prompts/agents/market-test-plugin-reviewer.md"): "Prompt body.\n"
    }


def test_render_inline_codex_config_is_deterministic(make_plugin_version):
    """Inline config rendering is stable and references generated prompt files."""
    cache_root, first = make_plugin_version(
        "market", "alpha", "1.0.0", agent_names=("b-reviewer",)
    )
    _, second = make_plugin_version(
        "market", "beta", "2.0.0", agent_names=("a-reviewer",)
    )
    (first / "agents" / "b-reviewer.md").write_text(
        "---\nname: b-reviewer\ndescription: B role\nmodel: sonnet\n---\n\nB prompt.\n"
    )
    (second / "agents" / "a-reviewer.md").write_text(
        "---\nname: a-reviewer\ndescription: A role\ntools:\n  - Read\n  - Write\n---\n\nA prompt.\n"
    )

    roles = translate_installed_agents(discover_latest_plugins(cache_root))
    rendered = render_inline_codex_config(roles)

    assert '[agents.market_alpha_b_reviewer]' in rendered
    assert '[agents.market_beta_a_reviewer]' in rendered
    assert 'prompt = ".codex/prompts/agents/market-alpha-b-reviewer.md"' in rendered
    assert 'prompt = ".codex/prompts/agents/market-beta-a-reviewer.md"' in rendered
    assert '# original_claude_model_hint = "sonnet"' in rendered
    assert 'tools = ["read", "write"]' in rendered


def test_render_inline_codex_config_escapes_multiline_strings(make_plugin_version):
    """Multiline frontmatter values still produce valid TOML config output."""
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", agent_names=("reviewer",)
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\n"
        "name: reviewer\n"
        "description: |\n"
        "  line one\n"
        "  line two\n"
        "---\n\n"
        "Prompt body.\n"
    )

    roles = translate_installed_agents(discover_latest_plugins(cache_root))
    rendered = render_inline_codex_config(roles)
    parsed = tomllib.loads(rendered)

    assert parsed["agents"]["market_test-plugin_reviewer"]["description"] == "line one\nline two"


def test_translate_tools_and_rendered_config_ignore_source_tool_order(make_plugin_version):
    """Equivalent tool sets produce the same translated order and config output."""
    cache_root, version_dir = make_plugin_version(
        "market",
        "test-plugin",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review\n"
        "tools:\n"
        "  - Write\n"
        "  - Read\n"
        "  - Bash\n"
        "---\n\n"
        "Prompt body.\n"
    )

    first_roles = translate_installed_agents(discover_latest_plugins(cache_root))
    first_render = render_inline_codex_config(first_roles)

    agent_path.write_text(
        "---\n"
        "name: reviewer\n"
        "description: Review\n"
        "tools:\n"
        "  - Bash\n"
        "  - Write\n"
        "  - Read\n"
        "---\n\n"
        "Prompt body.\n"
    )

    second_roles = translate_installed_agents(discover_latest_plugins(cache_root))
    second_render = render_inline_codex_config(second_roles)

    assert first_roles[0].tools == ("bash", "read", "write")
    assert second_roles[0].tools == ("bash", "read", "write")
    assert first_render == second_render


def test_translate_installed_agents_sanitizes_generated_names_and_paths(make_plugin_version):
    """Unsafe agent names are normalized before role and prompt paths are generated."""
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

    roles = translate_installed_agents(discover_latest_plugins(cache_root))

    assert roles[0].role_name == "market_test-plugin_tmp_pwn"
    assert roles[0].prompt_relpath.as_posix() == "prompts/agents/market-test-plugin-tmp-pwn.md"


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

    roles = translate_installed_agents(discover_latest_plugins(cache_root))

    assert [role.role_name for role in roles] == [
        "alpha_shared-plugin_reviewer",
        "beta_shared-plugin_reviewer",
    ]
    assert [role.prompt_relpath.as_posix() for role in roles] == [
        "prompts/agents/alpha-shared-plugin-reviewer.md",
        "prompts/agents/beta-shared-plugin-reviewer.md",
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

    assert result.roles == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].source_path == version_dir / "agents" / "broken.md"
    assert result.diagnostics[0].agent_name == "broken"
    assert result.diagnostics[0].unsupported_tools == ("NotebookEdit",)
    assert "unsupported Claude tools: NotebookEdit" in format_agent_translation_diagnostics(
        result.diagnostics
    )

    with pytest.raises(TranslationError, match="unsupported Claude tools: NotebookEdit"):
        translate_installed_agents(discover_latest_plugins(cache_root))


def test_translate_installed_agents_detects_duplicate_role_names(make_plugin_version):
    """Role-name collisions across plugins are rejected."""
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

    with pytest.raises(TranslationError, match="duplicate role name"):
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
    assert len(result.roles) == 1
    assert "edit" in result.roles[0].tools


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

    roles = translate_installed_agents(discover_latest_plugins(cache_root))

    assert len(roles) == 1
    assert roles[0].role_name == "market_test-plugin_reviewer"
    assert roles[0].description == "Review: carefully"
    assert roles[0].tools == ("read", "write")


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


def test_translate_user_agent(tmp_path: Path):
    """User-level agents are translated with a user_ role prefix."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "my-helper.md").write_text(
        "---\nname: my-helper\ndescription: Helps with tasks\ntools:\n  - Read\n  - Bash\n---\n\nYou help.\n"
    )

    result = translate_standalone_agents((agents_dir / "my-helper.md",), scope="user")

    assert len(result.roles) == 1
    role = result.roles[0]
    assert role.role_name == "user_my_helper"
    assert role.prompt_body == "You help.\n"
    assert "bash" in role.tools
    assert "read" in role.tools
    assert role.prompt_relpath.as_posix() == "prompts/agents/user-my-helper.md"


def test_translate_project_agent(tmp_path: Path):
    """Project-level agents are translated with a project_ role prefix."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "code-reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Reviews code\ntools:\n  - Read\n  - Grep\n---\n\nYou review code.\n"
    )

    result = translate_standalone_agents((agents_dir / "code-reviewer.md",), scope="project")

    assert len(result.roles) == 1
    role = result.roles[0]
    assert role.role_name == "project_code_reviewer"
    assert role.prompt_relpath.as_posix() == "prompts/agents/project-code-reviewer.md"
    assert role.tools == ("grep", "read")


def test_translate_standalone_agent_with_unsupported_tools(tmp_path: Path):
    """Standalone agents with unsupported tools produce diagnostics."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "broken.md").write_text(
        "---\nname: broken\ndescription: Review\ntools:\n  - Read\n  - NotebookEdit\n---\n\nPrompt.\n"
    )

    result = translate_standalone_agents((agents_dir / "broken.md",), scope="user")

    assert result.roles == ()
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].unsupported_tools == ("NotebookEdit",)


def test_translate_standalone_agents_rejects_duplicate_normalized_role_names(tmp_path: Path):
    """Standalone agents with names that normalize to the same role name are rejected."""
    a = tmp_path / "same-role.md"
    b = tmp_path / "same role.md"
    a.write_text(
        "---\nname: same-role\ndescription: First agent\n---\n\nPrompt A\n"
    )
    b.write_text(
        "---\nname: same role\ndescription: Second agent\n---\n\nPrompt B\n"
    )

    with pytest.raises(TranslationError, match="duplicate role name"):
        translate_standalone_agents((a, b), scope="user")


def test_translate_installed_agents_detects_duplicate_prompt_paths(make_plugin_version):
    """Prompt-path collisions across plugins are rejected even when role names differ.

    Plugin "a-b" with agent "c" and plugin "a" with agent "b-c" produce
    distinct role names (market_a-b_c vs market_a_b_c) but identical
    prompt path components (market-a-b-c.md), which would silently
    overwrite one prompt file.
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

    with pytest.raises(TranslationError, match="duplicate prompt path"):
        translate_installed_agents((first_plugin, second_plugin))


def test_translate_standalone_agent_empty_input():
    """Empty agent paths produce empty result."""
    result = translate_standalone_agents((), scope="user")
    assert result.roles == ()
    assert result.diagnostics == ()


def test_validate_merged_roles_detects_cross_scope_role_name_collision():
    """Cross-scope role name collisions are rejected after merging."""
    role_a = GeneratedAgentRole(
        plugin_name="plugin-a",
        source_path=Path("/plugins/a/agents/agent.md"),
        role_name="user_plugin_agent",
        description="From plugin scope",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=Path("prompts/agents/plugin-a-agent.md"),
        prompt_body="Prompt A.\n",
    )
    role_b = GeneratedAgentRole(
        plugin_name="_user",
        source_path=Path("/home/.claude/agents/plugin-agent.md"),
        role_name="user_plugin_agent",
        description="From user scope",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=Path("prompts/agents/user-plugin-agent.md"),
        prompt_body="Prompt B.\n",
    )

    with pytest.raises(TranslationError, match="Duplicate role name"):
        validate_merged_roles((role_a, role_b))


def test_validate_merged_roles_detects_cross_scope_prompt_path_collision():
    """Cross-scope prompt path collisions are rejected after merging."""
    shared_path = Path("prompts/agents/shared-prompt.md")
    role_a = GeneratedAgentRole(
        plugin_name="plugin-a",
        source_path=Path("/plugins/a/agents/agent.md"),
        role_name="role_alpha",
        description="First role",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=shared_path,
        prompt_body="Prompt A.\n",
    )
    role_b = GeneratedAgentRole(
        plugin_name="_user",
        source_path=Path("/home/.claude/agents/agent.md"),
        role_name="role_beta",
        description="Second role",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=shared_path,
        prompt_body="Prompt B.\n",
    )

    with pytest.raises(TranslationError, match="Duplicate prompt path"):
        validate_merged_roles((role_a, role_b))


def test_validate_merged_roles_accepts_unique_roles():
    """Unique role names and prompt paths pass validation."""
    role_a = GeneratedAgentRole(
        plugin_name="plugin-a",
        source_path=Path("/plugins/a/agents/agent.md"),
        role_name="plugin_agent_alpha",
        description="First role",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=Path("prompts/agents/plugin-a-alpha.md"),
        prompt_body="Prompt A.\n",
    )
    role_b = GeneratedAgentRole(
        plugin_name="_user",
        source_path=Path("/home/.claude/agents/beta.md"),
        role_name="user_agent_beta",
        description="Second role",
        original_model_hint=None,
        model="gpt-5.3-codex",
        tools=(),
        prompt_relpath=Path("prompts/agents/user-beta.md"),
        prompt_body="Prompt B.\n",
    )

    # Should not raise
    validate_merged_roles((role_a, role_b))


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
    import tomllib
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

    import tomllib
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

    import tomllib
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
