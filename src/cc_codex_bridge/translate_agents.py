"""Translation from Claude agent markdown to Codex roles."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    AgentTranslationDiagnostic,
    AgentTranslationResult,
    GeneratedAgentRole,
    InstalledPlugin,
    TranslationError,
)


TOOL_TRANSLATIONS = {
    "Read": "read",
    "Glob": "glob",
    "Grep": "grep",
    "Write": "write",
    "Bash": "bash",
    "WebSearch": "web_search",
}

ROLE_NAMESPACE_RE = re.compile(r"[^A-Za-z0-9_-]+")
ROLE_AGENT_RE = re.compile(r"[^A-Za-z0-9_]+")
PROMPT_COMPONENT_RE = re.compile(r"[^A-Za-z0-9-]+")


def translate_standalone_agents(
    agent_paths: Iterable[Path],
    *,
    scope: str,
    default_model: str = "gpt-5.3-codex",
) -> AgentTranslationResult:
    """Translate user-level or project-level Claude agent files into Codex roles.

    Standalone agents use a scope prefix (user_ or project_) instead of
    a marketplace prefix for role names and prompt file stems.
    """
    roles: list[GeneratedAgentRole] = []
    diagnostics: list[AgentTranslationDiagnostic] = []
    seen_role_names: set[str] = set()
    seen_prompt_paths: set[Path] = set()

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

        translated_tools = translate_tools(parsed_fm.get("tools"))
        normalized_name = _normalize_name(agent_name)
        role_name = f"{scope}_{normalized_name}"
        prompt_stem = f"{scope}-{_normalize_prompt_component(agent_name, kind='agent name')}"
        prompt_relpath = Path("prompts") / "agents" / f"{prompt_stem}.md"

        if role_name in seen_role_names:
            raise TranslationError(
                f"Generated duplicate role name: {role_name} "
                f"(from {scope} agent '{agent_name}' at {agent_path})"
            )
        seen_role_names.add(role_name)

        if prompt_relpath in seen_prompt_paths:
            raise TranslationError(
                f"Generated duplicate prompt path: {prompt_relpath} "
                f"(from {scope} agent '{agent_name}' at {agent_path})"
            )
        seen_prompt_paths.add(prompt_relpath)

        roles.append(
            GeneratedAgentRole(
                plugin_name=f"_{scope}",
                source_path=agent_path,
                role_name=role_name,
                description=description,
                original_model_hint=_optional_str(parsed_fm.get("model")),
                model=default_model,
                tools=translated_tools,
                prompt_relpath=prompt_relpath,
                prompt_body=body.strip() + ("\n" if body.strip() else ""),
            )
        )

    return AgentTranslationResult(
        roles=tuple(sorted(roles, key=lambda r: r.role_name)),
        diagnostics=tuple(sorted(diagnostics, key=lambda d: str(d.source_path))),
    )


def translate_installed_agents(
    plugins: Iterable[InstalledPlugin],
    *,
    default_model: str = "gpt-5.3-codex",
) -> tuple[GeneratedAgentRole, ...]:
    """Translate installed Claude agent files into Codex role definitions."""
    result = translate_installed_agents_with_diagnostics(
        plugins,
        default_model=default_model,
    )
    if result.diagnostics:
        raise TranslationError(format_agent_translation_diagnostics(result.diagnostics))
    return result.roles


def translate_installed_agents_with_diagnostics(
    plugins: Iterable[InstalledPlugin],
    *,
    default_model: str = "gpt-5.3-codex",
) -> AgentTranslationResult:
    """Translate installed Claude agent files into Codex role definitions."""
    roles: list[GeneratedAgentRole] = []
    diagnostics: list[AgentTranslationDiagnostic] = []
    seen_role_names: set[str] = set()

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

            translated_tools = translate_tools(parsed_frontmatter.get("tools"))
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

            marketplace_ns = _normalize_role_namespace(plugin.marketplace, kind="marketplace")
            plugin_ns = _normalize_role_namespace(plugin.plugin_name, kind="plugin name")
            agent_id = _normalize_name(agent_name)
            role_name = f"{marketplace_ns}_{plugin_ns}_{agent_id}"

            if role_name in seen_role_names:
                raise TranslationError(f"Generated duplicate role name: {role_name}")
            seen_role_names.add(role_name)

            marketplace_pc = _normalize_prompt_component(plugin.marketplace, kind="marketplace")
            plugin_pc = _normalize_prompt_component(plugin.plugin_name, kind="plugin name")
            agent_pc = _normalize_prompt_component(agent_name, kind="agent name")
            prompt_stem = f"{marketplace_pc}-{plugin_pc}-{agent_pc}"

            roles.append(
                GeneratedAgentRole(
                    plugin_name=plugin.plugin_name,
                    source_path=agent_path,
                    role_name=role_name,
                    description=description,
                    original_model_hint=_optional_str(parsed_frontmatter.get("model")),
                    model=default_model,
                    tools=translated_tools,
                    prompt_relpath=Path("prompts") / "agents" / f"{prompt_stem}.md",
                    prompt_body=body.strip() + ("\n" if body.strip() else ""),
                )
            )

    return AgentTranslationResult(
        roles=tuple(sorted(roles, key=lambda role: role.role_name)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: str(item.source_path))),
    )


def translate_tools(raw_tools: object) -> tuple[str, ...]:
    """Translate Claude tool names into Codex tool identifiers."""
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
        if isinstance(tool, str) and tool not in TOOL_TRANSLATIONS
    }
    return tuple(sorted(unsupported))


def _optional_str(value: object) -> str | None:
    """Return a stripped string or None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


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
    """Normalize marketplace/plugin/agent names for safe prompt file paths."""
    normalized = value.strip().replace("_", "-").replace(" ", "-")
    normalized = PROMPT_COMPONENT_RE.sub("-", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    if not normalized:
        raise TranslationError(f"{kind.capitalize()} normalizes to an empty prompt path segment: {value!r}")
    return normalized
