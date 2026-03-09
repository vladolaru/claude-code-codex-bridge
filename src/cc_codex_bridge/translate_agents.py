"""Translation from Claude agent markdown to Codex roles."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _RawAgentRole:
    """Intermediate translated agent before collision resolution."""

    marketplace: str
    plugin_name: str
    source_path: Path
    description: str
    original_model_hint: str | None
    tools: tuple[str, ...]
    prompt_body: str
    role_name_base: str
    role_name_prefix: str
    prompt_stem_base: str
    prompt_stem_prefix: str


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
    raw_roles: list[_RawAgentRole] = []
    diagnostics: list[AgentTranslationDiagnostic] = []

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

            raw_roles.append(
                _RawAgentRole(
                    marketplace=plugin.marketplace,
                    plugin_name=plugin.plugin_name,
                    source_path=agent_path,
                    description=description,
                    original_model_hint=_optional_str(parsed_frontmatter.get("model")),
                    tools=translated_tools,
                    prompt_body=body.strip() + ("\n" if body.strip() else ""),
                    role_name_base=(
                        f"{_normalize_role_namespace(plugin.plugin_name, kind='plugin name')}_"
                        f"{_normalize_name(agent_name)}"
                    ),
                    role_name_prefix=_normalize_role_namespace(
                        plugin.marketplace,
                        kind="marketplace",
                    ),
                    prompt_stem_base=(
                        f"{_normalize_prompt_component(plugin.plugin_name, kind='plugin name')}-"
                        f"{_normalize_prompt_component(agent_name, kind='agent name')}"
                    ),
                    prompt_stem_prefix=_normalize_prompt_component(
                        plugin.marketplace,
                        kind="marketplace",
                    ),
                )
            )

    role_names = _resolve_role_names(raw_roles)
    prompt_stems = _resolve_prompt_stems(raw_roles)
    roles = [
        GeneratedAgentRole(
            plugin_name=raw_role.plugin_name,
            source_path=raw_role.source_path,
            role_name=role_names[id(raw_role)],
            description=raw_role.description,
            original_model_hint=raw_role.original_model_hint,
            model=default_model,
            tools=raw_role.tools,
            prompt_relpath=Path("prompts") / "agents" / f"{prompt_stems[id(raw_role)]}.md",
            prompt_body=raw_role.prompt_body,
        )
        for raw_role in raw_roles
    ]

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


parse_markdown_with_frontmatter = frontmatter.parse_markdown_with_frontmatter
_parse_frontmatter_lines = frontmatter.parse_frontmatter_lines


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


def _resolve_role_names(raw_roles: list[_RawAgentRole]) -> dict[int, str]:
    """Resolve deterministic, collision-safe role names."""
    return _resolve_name_map(
        raw_roles,
        lambda raw_role: raw_role.role_name_base,
        lambda raw_role: f"{raw_role.role_name_prefix}_{raw_role.role_name_base}",
        error_label="role name",
    )


def _resolve_prompt_stems(raw_roles: list[_RawAgentRole]) -> dict[int, str]:
    """Resolve deterministic, collision-safe prompt file stems."""
    return _resolve_name_map(
        raw_roles,
        lambda raw_role: raw_role.prompt_stem_base,
        lambda raw_role: f"{raw_role.prompt_stem_prefix}-{raw_role.prompt_stem_base}",
        error_label="prompt file name",
    )


def _resolve_name_map(
    raw_roles: list[_RawAgentRole],
    base_name_getter,
    disambiguated_name_getter,
    *,
    error_label: str,
) -> dict[int, str]:
    """Resolve unique names, prefixing collisions by marketplace when needed."""
    grouped: dict[str, list[_RawAgentRole]] = defaultdict(list)
    for raw_role in raw_roles:
        grouped[base_name_getter(raw_role)].append(raw_role)

    resolved: dict[int, str] = {}
    seen_names: set[str] = set()
    for group_name in sorted(grouped):
        group = sorted(grouped[group_name], key=_raw_role_sort_key)
        if len(group) == 1:
            _assign_unique_name(
                resolved,
                seen_names,
                group[0],
                base_name_getter(group[0]),
                error_label=error_label,
            )
            continue

        for raw_role in group:
            _assign_unique_name(
                resolved,
                seen_names,
                raw_role,
                disambiguated_name_getter(raw_role),
                error_label=error_label,
            )

    return resolved


def _assign_unique_name(
    resolved: dict[int, str],
    seen_names: set[str],
    raw_role: _RawAgentRole,
    candidate: str,
    *,
    error_label: str,
) -> None:
    """Store one resolved name and fail when normalization still collides."""
    if candidate in seen_names:
        raise TranslationError(f"Generated duplicate {error_label}: {candidate}")
    seen_names.add(candidate)
    resolved[id(raw_role)] = candidate


def _raw_role_sort_key(raw_role: _RawAgentRole) -> tuple[str, str, str]:
    """Stable ordering for marketplace-based collision resolution."""
    return (raw_role.marketplace, raw_role.plugin_name, raw_role.source_path.name)


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
