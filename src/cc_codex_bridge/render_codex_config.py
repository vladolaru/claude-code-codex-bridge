"""Rendering for generated Codex prompt files and inline config."""

from __future__ import annotations

import json
from pathlib import Path

from cc_codex_bridge.model import GeneratedAgentRole


def render_prompt_files(
    roles: tuple[GeneratedAgentRole, ...],
) -> dict[Path, str]:
    """Render generated prompt file contents keyed by project-relative path."""
    rendered: dict[Path, str] = {}
    for role in roles:
        rendered[Path(".codex") / role.prompt_relpath] = role.prompt_body
    return rendered


def render_inline_codex_config(
    roles: tuple[GeneratedAgentRole, ...],
) -> str:
    """Render inline `.codex/config.toml` agent role config deterministically."""
    lines = [
        "# GENERATED FILE - DO NOT EDIT",
        "# Source: cc_codex_bridge phase 2",
        "",
    ]

    for role in roles:
        lines.extend(
            [
                f"[agents.{role.role_name}]",
                f"description = {_render_string(role.description)}",
                f"model = {_render_string(role.model)}",
                f"prompt = {_render_string(f'.codex/{role.prompt_relpath.as_posix()}')}",
                f"tools = [{_render_tools(role.tools)}]",
            ]
        )
        if role.original_model_hint:
            lines.append(
                f"# original_claude_model_hint = {_render_string(role.original_model_hint)}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_tools(tools: tuple[str, ...]) -> str:
    """Render a deterministic TOML list of tool strings."""
    return ", ".join(_render_string(tool) for tool in tools)


def _render_string(value: str) -> str:
    """Render a TOML-compatible basic string with JSON-compatible escaping."""
    return json.dumps(value)
