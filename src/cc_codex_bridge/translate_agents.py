"""Translation from Claude agent markdown to Codex agent .toml files."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable

import cc_codex_bridge.frontmatter as frontmatter
from cc_codex_bridge.model import (
    AgentTranslationDiagnostic,
    AgentTranslationResult,
    GeneratedAgentFile,
    InstalledPlugin,
    TranslationError,
    VendoredPluginResource,
)
from cc_codex_bridge.render_agent_toml import READ_TOOLS, WRITE_TOOLS, derive_sandbox_mode


# Union of all Claude tools that have a meaningful Codex mapping.
RECOGNIZED_TOOLS = WRITE_TOOLS | READ_TOOLS

MAX_AGENT_NAME_LENGTH = 64


def assign_agent_names(
    agents: tuple[GeneratedAgentFile, ...],
) -> tuple[GeneratedAgentFile, ...]:
    """Assign collision-free install names using bare agent file stems.

    Priority: standalone agents (user/project) win bare names over plugin agents.
    Among plugins, sorted by (marketplace, plugin_name).
    Collisions get -alt, -alt-2, -alt-3 suffixes.
    """
    # Group agents by bare file stem (from source_path)
    groups: dict[str, list[GeneratedAgentFile]] = {}
    for agent in agents:
        bare_name = agent.source_path.stem
        groups.setdefault(bare_name, []).append(agent)

    result: list[GeneratedAgentFile] = []

    for bare_name in sorted(groups):
        candidates = groups[bare_name]

        if len(candidates) > 1:
            # Sort by priority: standalone agents first (marketplace starts with _),
            # then plugin agents by (marketplace, plugin_name)
            candidates.sort(key=lambda a: (
                0 if a.marketplace.startswith("_") else 1,
                a.marketplace,
                a.plugin_name,
            ))

        for index, agent in enumerate(candidates):
            if index == 0:
                assigned_name = bare_name
            elif index == 1:
                assigned_name = f"{bare_name}-alt"
            else:
                assigned_name = f"{bare_name}-alt-{index}"

            if len(assigned_name) > MAX_AGENT_NAME_LENGTH:
                raise TranslationError(
                    f"Generated agent name exceeds {MAX_AGENT_NAME_LENGTH} characters: "
                    f"'{assigned_name}' ({len(assigned_name)} chars) "
                    f"from agent file '{bare_name}'"
                )

            result.append(replace(
                agent,
                agent_name=assigned_name,
                install_filename=f"{assigned_name}.toml",
            ))

    return tuple(sorted(result, key=lambda a: a.agent_name))


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

    Agents use bare file stems for names and install filenames.  Collision
    resolution across scopes is handled by ``assign_agent_names()``.
    """
    agents: list[GeneratedAgentFile] = []
    diagnostics: list[AgentTranslationDiagnostic] = []

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

        stem = agent_path.stem
        prompt_body = body.strip() + ("\n" if body.strip() else "")

        agents.append(
            GeneratedAgentFile(
                marketplace=f"_{scope}",
                plugin_name="personal" if scope == "user" else "local",
                source_path=agent_path,
                scope=install_scope,
                agent_name=stem,
                install_filename=f"{stem}.toml",
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
    *,
    bridge_home: Path | None = None,
) -> tuple[GeneratedAgentFile, ...]:
    """Translate installed Claude agent files into Codex agent file definitions."""
    result = translate_installed_agents_with_diagnostics(plugins, bridge_home=bridge_home)
    if result.diagnostics:
        raise TranslationError(format_agent_translation_diagnostics(result.diagnostics))
    return result.agents


def translate_installed_agents_with_diagnostics(
    plugins: Iterable[InstalledPlugin],
    *,
    bridge_home: Path | None = None,
) -> AgentTranslationResult:
    """Translate installed Claude agent files into Codex agent file definitions."""
    from cc_codex_bridge.bridge_home import plugin_resource_dir
    from cc_codex_bridge.vendor_plugin import (
        detect_plugin_resource_dirs,
        detect_transitive_plugin_dirs,
        read_plugin_dir_files,
        rewrite_plugin_paths,
    )

    agents: list[GeneratedAgentFile] = []
    diagnostics: list[AgentTranslationDiagnostic] = []
    all_plugin_resources: list[VendoredPluginResource] = []

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

            stem = agent_path.stem
            prompt_body = body.strip() + ("\n" if body.strip() else "")

            # Detect and rewrite plugin resource paths
            if bridge_home is not None:
                detected_dirs = detect_plugin_resource_dirs(prompt_body)
                if detected_dirs:
                    vendored_root = plugin_resource_dir(
                        plugin.marketplace, plugin.plugin_name, bridge_home=bridge_home,
                    )
                    prompt_body = rewrite_plugin_paths(prompt_body, vendored_root)

                    agent_plugin_resources: list[VendoredPluginResource] = []
                    for dir_name in sorted(detected_dirs):
                        source_dir = plugin.source_path / dir_name
                        if not source_dir.is_dir():
                            continue
                        agent_plugin_resources.append(VendoredPluginResource(
                            marketplace=plugin.marketplace,
                            plugin_name=plugin.plugin_name,
                            source_dir=source_dir,
                            target_dir_name=dir_name,
                            files=read_plugin_dir_files(source_dir),
                        ))

                    # Detect transitive dependencies: vendored scripts may
                    # reference other plugin-level directories (e.g.,
                    # agents/shared/ protocols loaded by bootstrap scripts)
                    vendored_files = tuple(
                        f for r in agent_plugin_resources for f in r.files
                    )
                    transitive_dirs = detect_transitive_plugin_dirs(
                        vendored_files, plugin.source_path,
                    )
                    already_vendored = {
                        r.target_dir_name for r in agent_plugin_resources
                    }
                    for dir_name in sorted(transitive_dirs - already_vendored):
                        source_dir = plugin.source_path / dir_name
                        agent_plugin_resources.append(VendoredPluginResource(
                            marketplace=plugin.marketplace,
                            plugin_name=plugin.plugin_name,
                            source_dir=source_dir,
                            target_dir_name=dir_name,
                            files=read_plugin_dir_files(source_dir),
                        ))

                    all_plugin_resources.extend(agent_plugin_resources)

            agents.append(
                GeneratedAgentFile(
                    marketplace=plugin.marketplace,
                    plugin_name=plugin.plugin_name,
                    source_path=agent_path,
                    scope="global",
                    agent_name=stem,
                    install_filename=f"{stem}.toml",
                    description=description,
                    developer_instructions=prompt_body,
                    sandbox_mode=sandbox_mode,
                    original_model_hint=_optional_str(parsed_frontmatter.get("model")),
                )
            )

    return AgentTranslationResult(
        agents=tuple(sorted(agents, key=lambda a: a.agent_name)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: str(item.source_path))),
        plugin_resources=tuple(all_plugin_resources),
    )


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
