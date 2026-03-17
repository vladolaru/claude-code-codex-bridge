"""Translation from Claude agent markdown to Codex agent .toml files."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    AgentTranslationDiagnostic,
    AgentTranslationResult,
    GeneratedAgentFile,
    InstalledPlugin,
    TranslationError,
)
from cc_codex_bridge.render_agent_toml import READ_TOOLS, WRITE_TOOLS, derive_sandbox_mode


# Union of all Claude tools that have a meaningful Codex mapping.
RECOGNIZED_TOOLS = WRITE_TOOLS | READ_TOOLS

# Keep TOOL_TRANSLATIONS for backwards-compatibility with translate_tools().
TOOL_TRANSLATIONS = {
    "Read": "read",
    "Edit": "edit",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write",
    "Bash": "bash",
    "WebSearch": "web_search",
}

PROMPT_COMPONENT_RE = re.compile(r"[^A-Za-z0-9-]+")


def validate_merged_agents(agents: tuple[GeneratedAgentFile, ...]) -> None:
    """Validate uniqueness of agent_name and install_filename across all merged agents.

    Call this after merging plugin, user, and project agent results.
    Raises TranslationError on collision.
    """
    seen_names: dict[str, Path] = {}
    seen_filenames: dict[str, Path] = {}

    for agent in agents:
        if agent.agent_name in seen_names:
            raise TranslationError(
                f"Duplicate agent name after merging all agent scopes: {agent.agent_name} "
                f"(from {agent.source_path}, previously from {seen_names[agent.agent_name]})"
            )
        seen_names[agent.agent_name] = agent.source_path

        if agent.install_filename in seen_filenames:
            raise TranslationError(
                f"Duplicate install filename after merging all agent scopes: {agent.install_filename} "
                f"(from {agent.source_path}, previously from {seen_filenames[agent.install_filename]})"
            )
        seen_filenames[agent.install_filename] = agent.source_path


def translate_standalone_agents(
    agent_paths: Iterable[Path],
    *,
    scope: str,
) -> AgentTranslationResult:
    """Translate user-level or project-level Claude agent files into Codex agent files.

    Standalone agents use a scope prefix (user or project) for agent names
    and install filenames.  User agents get scope="global" (installed to
    ~/.codex/agents/), project agents get scope="project" (installed to
    .codex/agents/).
    """
    agents: list[GeneratedAgentFile] = []
    diagnostics: list[AgentTranslationDiagnostic] = []
    seen_agent_names: set[str] = set()
    seen_filenames: set[str] = set()

    install_scope = "global" if scope == "user" else "project"

    for agent_path in agent_paths:
        parsed_fm, body = frontmatter.parse_markdown_with_frontmatter(agent_path)
        agent_name = str(parsed_fm.get("name", "")).strip()
        if not agent_name:
            raise TranslationError(f"Agent missing required name frontmatter: {agent_path}")

        description = str(parsed_fm.get("description", "")).strip()
        if not description:
            raise TranslationError(f"Agent missing required description frontmatter: {agent_path}")

        unsupported = _unsupported_tools(parsed_fm.get("tools"))
        if unsupported:
            diagnostics.append(
                AgentTranslationDiagnostic(
                    source_path=agent_path,
                    agent_name=agent_name,
                    unsupported_tools=unsupported,
                )
            )
            continue

        claude_tools = _extract_tool_names(parsed_fm.get("tools"))
        sandbox_mode = derive_sandbox_mode(claude_tools)

        normalized_agent = _normalize_prompt_component(agent_name, kind="agent name")
        codex_agent_name = f"{scope}_{_normalize_name(agent_name)}"
        install_filename = f"{scope}-{normalized_agent}.toml"

        if codex_agent_name in seen_agent_names:
            raise TranslationError(
                f"Generated duplicate agent name: {codex_agent_name} "
                f"(from {scope} agent '{agent_name}' at {agent_path})"
            )
        seen_agent_names.add(codex_agent_name)

        if install_filename in seen_filenames:
            raise TranslationError(
                f"Generated duplicate install filename: {install_filename} "
                f"(from {scope} agent '{agent_name}' at {agent_path})"
            )
        seen_filenames.add(install_filename)

        prompt_body = body.strip() + ("\n" if body.strip() else "")

        agents.append(
            GeneratedAgentFile(
                source_path=agent_path,
                scope=install_scope,
                agent_name=codex_agent_name,
                install_filename=install_filename,
                description=description,
                developer_instructions=prompt_body,
                sandbox_mode=sandbox_mode,
                original_model_hint=_optional_str(parsed_fm.get("model")),
            )
        )

    return AgentTranslationResult(
        agents=tuple(sorted(agents, key=lambda a: a.agent_name)),
        diagnostics=tuple(sorted(diagnostics, key=lambda d: str(d.source_path))),
    )


def translate_installed_agents(
    plugins: Iterable[InstalledPlugin],
) -> tuple[GeneratedAgentFile, ...]:
    """Translate installed Claude agent files into Codex agent file definitions."""
    result = translate_installed_agents_with_diagnostics(plugins)
    if result.diagnostics:
        raise TranslationError(format_agent_translation_diagnostics(result.diagnostics))
    return result.agents


def translate_installed_agents_with_diagnostics(
    plugins: Iterable[InstalledPlugin],
) -> AgentTranslationResult:
    """Translate installed Claude agent files into Codex agent file definitions."""
    agents: list[GeneratedAgentFile] = []
    diagnostics: list[AgentTranslationDiagnostic] = []
    seen_agent_names: set[str] = set()
    seen_filenames: set[str] = set()

    for plugin in plugins:
        for agent_path in plugin.agents:
            parsed_frontmatter, body = frontmatter.parse_markdown_with_frontmatter(agent_path)
            agent_name = str(parsed_frontmatter.get("name", "")).strip()
            if not agent_name:
                raise TranslationError(f"Agent missing required name frontmatter: {agent_path}")

            description = str(parsed_frontmatter.get("description", "")).strip()
            if not description:
                raise TranslationError(
                    f"Agent missing required description frontmatter: {agent_path}"
                )

            unsupported_tools = _unsupported_tools(parsed_frontmatter.get("tools"))
            if unsupported_tools:
                diagnostics.append(
                    AgentTranslationDiagnostic(
                        source_path=agent_path,
                        agent_name=agent_name,
                        unsupported_tools=unsupported_tools,
                    )
                )
                continue

            claude_tools = _extract_tool_names(parsed_frontmatter.get("tools"))
            sandbox_mode = derive_sandbox_mode(claude_tools)

            marketplace_ns = _normalize_role_namespace(plugin.marketplace, kind="marketplace")
            plugin_ns = _normalize_role_namespace(plugin.plugin_name, kind="plugin name")
            agent_id = _normalize_name(agent_name)
            codex_agent_name = f"{marketplace_ns}_{plugin_ns}_{agent_id}"

            if codex_agent_name in seen_agent_names:
                raise TranslationError(f"Generated duplicate agent name: {codex_agent_name}")
            seen_agent_names.add(codex_agent_name)

            marketplace_pc = _normalize_prompt_component(plugin.marketplace, kind="marketplace")
            plugin_pc = _normalize_prompt_component(plugin.plugin_name, kind="plugin name")
            agent_pc = _normalize_prompt_component(agent_name, kind="agent name")
            install_filename = f"{marketplace_pc}-{plugin_pc}-{agent_pc}.toml"

            if install_filename in seen_filenames:
                raise TranslationError(
                    f"Generated duplicate install filename: {install_filename} "
                    f"(from plugin '{plugin.plugin_name}' agent '{agent_name}' "
                    f"at {agent_path})"
                )
            seen_filenames.add(install_filename)

            prompt_body = body.strip() + ("\n" if body.strip() else "")

            agents.append(
                GeneratedAgentFile(
                    source_path=agent_path,
                    scope="global",
                    agent_name=codex_agent_name,
                    install_filename=install_filename,
                    description=description,
                    developer_instructions=prompt_body,
                    sandbox_mode=sandbox_mode,
                    original_model_hint=_optional_str(parsed_frontmatter.get("model")),
                )
            )

    return AgentTranslationResult(
        agents=tuple(sorted(agents, key=lambda a: a.agent_name)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: str(item.source_path))),
    )


def translate_tools(raw_tools: object) -> tuple[str, ...]:
    """Translate Claude tool names into Codex tool identifiers.

    Kept for backwards-compatibility. New code should use derive_sandbox_mode().
    """
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise TranslationError(f"Agent tools must be a list, got: {type(raw_tools).__name__}")

    translated: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str):
            raise TranslationError(f"Agent tool entry must be a string, got: {tool!r}")
        translated_tool = TOOL_TRANSLATIONS.get(tool)
        if translated_tool and translated_tool not in translated:
            translated.append(translated_tool)

    return tuple(sorted(translated))


def format_agent_translation_diagnostics(
    diagnostics: Iterable[AgentTranslationDiagnostic],
) -> str:
    """Render agent translation diagnostics as stable human-readable lines."""
    return "\n".join(
        _format_agent_translation_diagnostic(diagnostic)
        for diagnostic in diagnostics
    )


def _format_agent_translation_diagnostic(diagnostic: AgentTranslationDiagnostic) -> str:
    """Render one agent translation diagnostic."""
    return (
        f"{diagnostic.source_path}: unsupported Claude tools: "
        + ", ".join(diagnostic.unsupported_tools)
    )


def _unsupported_tools(raw_tools: object) -> tuple[str, ...]:
    """Return unsupported Claude tools after validating the frontmatter shape."""
    if raw_tools is None:
        return ()
    if not isinstance(raw_tools, list):
        raise TranslationError(f"Agent tools must be a list, got: {type(raw_tools).__name__}")

    unsupported = {
        tool
        for tool in raw_tools
        if isinstance(tool, str) and tool not in RECOGNIZED_TOOLS
    }
    return tuple(sorted(unsupported))


def _extract_tool_names(raw_tools: object) -> tuple[str, ...] | None:
    """Extract Claude tool names as a tuple for sandbox mode derivation."""
    if raw_tools is None:
        return None
    if not isinstance(raw_tools, list):
        return None
    return tuple(tool for tool in raw_tools if isinstance(tool, str))


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


ROLE_NAMESPACE_RE = re.compile(r"[^A-Za-z0-9_-]+")
ROLE_AGENT_RE = re.compile(r"[^A-Za-z0-9_]+")


def _normalize_role_namespace(value: str, *, kind: str) -> str:
    """Normalize marketplace/plugin names for TOML bare-key compatibility."""
    normalized = value.strip().replace(" ", "_")
    normalized = ROLE_NAMESPACE_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_-")
    if not normalized:
        raise TranslationError(f"{kind.capitalize()} normalizes to an empty role namespace: {value!r}")
    return normalized


def _normalize_name(value: str) -> str:
    """Normalize agent names for Codex role identifiers."""
    normalized = value.strip().replace("-", "_").replace(" ", "_")
    normalized = ROLE_AGENT_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise TranslationError(f"Agent name normalizes to an empty role identifier: {value!r}")
    return normalized


def _normalize_prompt_component(value: str, *, kind: str) -> str:
    """Normalize marketplace/plugin/agent names for safe file paths."""
    normalized = value.strip().replace("_", "-").replace(" ", "-")
    normalized = PROMPT_COMPONENT_RE.sub("-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise TranslationError(f"{kind.capitalize()} normalizes to an empty path segment: {value!r}")
    return normalized
