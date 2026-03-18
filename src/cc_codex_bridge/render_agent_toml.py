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
    escaped_body = _escape_toml_multiline_string(developer_instructions)
    lines.append(f'developer_instructions = """\n{escaped_body}"""')
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


def _escape_toml_multiline_string(value: str) -> str:
    """Escape a string for TOML multiline basic string context.

    In a multiline basic string (delimited by triple double-quotes),
    runs of three or more consecutive unescaped double-quotes would
    prematurely close the string.  We break such runs by backslash-
    escaping every third consecutive quote.
    """
    result: list[str] = []
    consecutive_quotes = 0
    for char in value:
        if char == '"':
            consecutive_quotes += 1
            if consecutive_quotes == 3:
                # Break the run: replace the third quote with \"
                result.append('\\"')
                consecutive_quotes = 0
            else:
                result.append(char)
        else:
            consecutive_quotes = 0
            result.append(char)
    return "".join(result)
