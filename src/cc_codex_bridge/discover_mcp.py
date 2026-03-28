"""Discovery for MCP servers from Claude Code configuration."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from cc_codex_bridge.model import DiscoveredMcpServer


def discover_mcp_servers(
    *,
    project_root: Path,
    claude_json_path: Path | None = None,
    mcp_json_path: Path | None = None,
) -> tuple[DiscoveredMcpServer, ...]:
    """Discover MCP servers from Claude Code configuration files.

    Reads from three sources in precedence order (highest first):

    1. **Project-local**: ``~/.claude.json`` projects.<project_root>.mcpServers
    2. **Project-shared**: ``<project_root>/.mcp.json`` mcpServers
    3. **User-global**: ``~/.claude.json`` top-level mcpServers

    When the same server name appears at multiple scopes, highest
    precedence wins.  Returns a deduplicated tuple sorted by name.
    """
    if claude_json_path is None:
        claude_json_path = Path.home() / ".claude.json"
    if mcp_json_path is None:
        mcp_json_path = project_root / ".mcp.json"

    claude_data = _load_json(claude_json_path)
    mcp_data = _load_json(mcp_json_path)

    # Collect servers lowest-precedence first so higher-precedence
    # overwrites by name.
    by_name: dict[str, DiscoveredMcpServer] = {}

    # 3. User-global (lowest precedence)
    global_servers = claude_data.get("mcpServers", {})
    if isinstance(global_servers, dict):
        for server in _extract_servers(global_servers, "global", "user-global"):
            by_name[server.name] = server

    # 2. Project-shared
    shared_servers = mcp_data.get("mcpServers", {})
    if isinstance(shared_servers, dict):
        for server in _extract_servers(shared_servers, "project", "project-shared"):
            by_name[server.name] = server

    # 1. Project-local (highest precedence)
    projects = claude_data.get("projects", {})
    if isinstance(projects, dict):
        project_key = str(project_root)
        project_config = projects.get(project_key, {})
        if isinstance(project_config, dict):
            local_servers = project_config.get("mcpServers", {})
            if isinstance(local_servers, dict):
                for server in _extract_servers(local_servers, "project", "project-local"):
                    by_name[server.name] = server

    return tuple(sorted(by_name.values(), key=lambda s: s.name))


def _load_json(path: Path) -> dict:
    """Load a JSON file as a dict, returning ``{}`` on missing or malformed input."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _detect_transport(config: dict) -> str | None:
    """Detect the transport type from a server config dict.

    Returns ``"stdio"``, ``"http"``, or ``None`` (skip).
    """
    server_type = config.get("type")

    if server_type == "sse":
        return None
    if server_type == "http":
        return "http"
    if "command" in config:
        return "stdio"
    if "url" in config:
        return "http"
    return None


def _extract_servers(
    servers_dict: dict,
    scope: str,
    source: str,
) -> Iterator[DiscoveredMcpServer]:
    """Yield ``DiscoveredMcpServer`` objects from a servers mapping.

    Non-dict values and servers with undetectable transport are skipped.
    """
    for name, config in servers_dict.items():
        if not isinstance(config, dict):
            continue
        transport = _detect_transport(config)
        if transport is None:
            continue
        yield DiscoveredMcpServer(
            name=name,
            scope=scope,
            transport=transport,
            source=source,
            config=config,
        )
