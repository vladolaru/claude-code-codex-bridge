"""Rendering for generated Codex agent .toml files."""

from __future__ import annotations


WRITE_TOOLS = frozenset({"Bash", "Write", "Edit"})
READ_TOOLS = frozenset({"Read", "Grep", "Glob", "WebSearch"})


def derive_sandbox_mode(claude_tools: tuple[str, ...] | None) -> str | None:
    """Derive Codex sandbox_mode from Claude tool names.

    Returns "workspace-write" if any write-capable tool is present,
    "read-only" if only read tools are present, or None if no tools
    are specified (inherit from parent session).
    """
    if not claude_tools:
        return None
    tool_set = set(claude_tools)
    if tool_set & WRITE_TOOLS:
        return "workspace-write"
    if tool_set & READ_TOOLS:
        return "read-only"
    return None


def render_agent_toml(
    agent_name: str,
    description: str,
    developer_instructions: str,
    *,
    sandbox_mode: str | None = None,
) -> str:
    """Render a Codex agent .toml file deterministically."""
    lines = [
        "# GENERATED FILE - DO NOT EDIT",
        "# Source: cc_codex_bridge",
        "",
        f'name = "{_escape_toml_string(agent_name)}"',
        f'description = "{_escape_toml_string(description)}"',
    ]
    if sandbox_mode is not None:
        lines.append(f'sandbox_mode = "{sandbox_mode}"')
    lines.append(f'developer_instructions = """\n{developer_instructions}"""')
    lines.append("")
    return "\n".join(lines)


def _escape_toml_string(value: str) -> str:
    """Escape a string for TOML basic string context."""
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
