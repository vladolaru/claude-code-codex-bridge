"""Tests for MCP server discovery from Claude Code configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.discover_mcp import (
    _detect_transport,
    _extract_servers,
    _load_json,
    discover_mcp_servers,
)
from cc_codex_bridge.model import DiscoveredMcpServer


# -- _load_json ----------------------------------------------------------------


class TestLoadJson:
    """Tests for the _load_json helper."""

    def test_returns_dict_for_valid_json(self, tmp_path: Path):
        path = tmp_path / "test.json"
        path.write_text('{"key": "value"}')
        assert _load_json(path) == {"key": "value"}

    def test_returns_empty_dict_for_missing_file(self, tmp_path: Path):
        path = tmp_path / "nonexistent.json"
        assert _load_json(path) == {}

    def test_returns_none_for_malformed_json(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        assert _load_json(path) is None

    def test_returns_none_for_unreadable_file(self, tmp_path: Path):
        path = tmp_path / "secret.json"
        path.write_text('{"key": "value"}')
        path.chmod(0o000)
        assert _load_json(path) is None
        path.chmod(0o644)  # restore for cleanup

    def test_returns_none_for_non_dict_json(self, tmp_path: Path):
        path = tmp_path / "array.json"
        path.write_text('["a", "b"]')
        assert _load_json(path) is None


# -- _detect_transport ---------------------------------------------------------


class TestDetectTransport:
    """Tests for transport detection from a server config dict."""

    def test_command_field_yields_stdio(self):
        assert _detect_transport({"command": "node", "args": ["server.js"]}) == "stdio"

    def test_type_http_yields_http(self):
        assert _detect_transport({"type": "http", "url": "http://localhost:8080"}) == "http"

    def test_url_without_type_yields_http(self):
        assert _detect_transport({"url": "http://localhost:8080"}) == "http"

    def test_type_sse_yields_none(self):
        assert _detect_transport({"type": "sse", "url": "http://localhost:8080/sse"}) is None

    def test_empty_config_yields_none(self):
        assert _detect_transport({}) is None

    def test_unrecognized_type_without_command_or_url_yields_none(self):
        assert _detect_transport({"type": "websocket"}) is None

    def test_type_http_without_url_yields_none(self):
        """type=http without url field should return None (skip), not 'http'."""
        assert _detect_transport({"type": "http"}) is None, "type=http without url must be skipped"


# -- _extract_servers ----------------------------------------------------------


class TestExtractServers:
    """Tests for extracting DiscoveredMcpServer objects from a servers dict."""

    def test_extracts_stdio_server(self):
        servers_dict = {
            "my-server": {"command": "node", "args": ["server.js"]},
        }
        result = list(_extract_servers(servers_dict, "global", "user-global"))
        assert len(result) == 1
        assert result[0].name == "my-server"
        assert result[0].scope == "global"
        assert result[0].transport == "stdio"
        assert result[0].source == "user-global"
        assert result[0].config == {"command": "node", "args": ["server.js"]}

    def test_extracts_http_server(self):
        servers_dict = {
            "api": {"type": "http", "url": "http://localhost:9090"},
        }
        result = list(_extract_servers(servers_dict, "global", "user-global"))
        assert len(result) == 1
        assert result[0].transport == "http"

    def test_skips_sse_server(self):
        servers_dict = {
            "sse-server": {"type": "sse", "url": "http://localhost:8080/sse"},
        }
        result = list(_extract_servers(servers_dict, "global", "user-global"))
        assert result == []

    def test_skips_non_dict_values(self):
        servers_dict = {
            "good": {"command": "node"},
            "bad-string": "not a dict",
            "bad-list": ["a", "b"],
            "bad-int": 42,
            "bad-none": None,
        }
        result = list(_extract_servers(servers_dict, "global", "user-global"))
        assert len(result) == 1
        assert result[0].name == "good"

    def test_skips_undetectable_transport(self):
        servers_dict = {
            "mystery": {"foo": "bar"},
        }
        result = list(_extract_servers(servers_dict, "project", "project-local"))
        assert result == []


# -- discover_mcp_servers (integration) ----------------------------------------


class TestDiscoverMcpServersGlobal:
    """Tests for discovering user-global MCP servers."""

    def test_discover_user_global_stdio_servers(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                },
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "filesystem"
        assert result[0].scope == "global"
        assert result[0].transport == "stdio"
        assert result[0].source == "user-global"

    def test_discover_user_global_http_servers(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "api-server": {
                    "type": "http",
                    "url": "http://localhost:8080/mcp",
                },
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "api-server"
        assert result[0].transport == "http"
        assert result[0].source == "user-global"


class TestDiscoverMcpServersProjectLocal:
    """Tests for discovering project-local MCP servers from ~/.claude.json projects section."""

    def test_discover_project_local_servers(self, tmp_path: Path):
        project_root = tmp_path / "my-project"
        project_root.mkdir()
        project_key = str(project_root)

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "projects": {
                project_key: {
                    "mcpServers": {
                        "local-db": {
                            "command": "python",
                            "args": ["-m", "db_server"],
                        },
                    },
                },
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "local-db"
        assert result[0].scope == "project"
        assert result[0].transport == "stdio"
        assert result[0].source == "project-local"


class TestDiscoverMcpServersProjectShared:
    """Tests for discovering project-shared MCP servers from .mcp.json."""

    def test_discover_project_shared_servers(self, tmp_path: Path):
        project_root = tmp_path / "shared-project"
        project_root.mkdir()
        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "shared-server": {
                    "command": "npx",
                    "args": ["shared-mcp"],
                },
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=tmp_path / "nonexistent.json",
            mcp_json_path=mcp_json,
        )
        assert len(result) == 1
        assert result[0].name == "shared-server"
        assert result[0].scope == "project"
        assert result[0].transport == "stdio"
        assert result[0].source == "project-shared"


class TestDiscoverMcpServersSkipSse:
    """Tests for skipping SSE servers."""

    def test_skip_sse_servers(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "sse-only": {
                    "type": "sse",
                    "url": "http://localhost:8080/sse",
                },
                "good-stdio": {
                    "command": "node",
                    "args": ["server.js"],
                },
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "good-stdio"


class TestDiscoverMcpServersPrecedence:
    """Tests for precedence: project-local > project-shared > user-global."""

    def test_project_local_overrides_user_global(self, tmp_path: Path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        project_key = str(project_root)

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "my-server": {
                    "command": "global-cmd",
                    "args": ["--global"],
                },
            },
            "projects": {
                project_key: {
                    "mcpServers": {
                        "my-server": {
                            "command": "local-cmd",
                            "args": ["--local"],
                        },
                    },
                },
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "my-server"
        assert result[0].source == "project-local"
        assert result[0].config["command"] == "local-cmd"

    def test_project_shared_overrides_user_global(self, tmp_path: Path):
        project_root = tmp_path / "project"
        project_root.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "my-server": {
                    "command": "global-cmd",
                },
            },
        }))

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "my-server": {
                    "command": "shared-cmd",
                },
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        assert len(result) == 1
        assert result[0].name == "my-server"
        assert result[0].source == "project-shared"
        assert result[0].config["command"] == "shared-cmd"

    def test_project_local_overrides_project_shared(self, tmp_path: Path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        project_key = str(project_root)

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "projects": {
                project_key: {
                    "mcpServers": {
                        "my-server": {
                            "command": "local-cmd",
                        },
                    },
                },
            },
        }))

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "my-server": {
                    "command": "shared-cmd",
                },
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        assert len(result) == 1
        assert result[0].name == "my-server"
        assert result[0].source == "project-local"
        assert result[0].config["command"] == "local-cmd"


class TestDiscoverMcpServersEdgeCases:
    """Tests for edge cases and missing files."""

    def test_empty_mcp_servers_returns_empty_tuple(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"mcpServers": {}}))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert result == ()

    def test_missing_mcp_servers_key_returns_empty_tuple(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({"someOtherKey": True}))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert result == ()

    def test_missing_claude_json_returns_empty_tuple(self, tmp_path: Path):
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=tmp_path / "nonexistent.json",
        )
        assert result == ()

    def test_missing_mcp_json_is_fine(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "global-srv": {"command": "node"},
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=tmp_path / "no-such-file.json",
        )
        assert len(result) == 1
        assert result[0].name == "global-srv"

    def test_non_dict_mcp_servers_values_are_skipped(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "good": {"command": "node"},
                "bad-string": "not a dict",
                "bad-int": 42,
                "bad-list": [1, 2],
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].name == "good"

    def test_results_sorted_by_name(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "zebra": {"command": "z"},
                "alpha": {"command": "a"},
                "middle": {"command": "m"},
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        names = [s.name for s in result]
        assert names == ["alpha", "middle", "zebra"]

    def test_transport_detection_url_without_type(self, tmp_path: Path):
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "url-server": {
                    "url": "http://localhost:3000/mcp",
                },
            },
        }))
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
        )
        assert len(result) == 1
        assert result[0].transport == "http"


class TestDiscoverMcpServersMultiSource:
    """Tests for combining servers from all three sources."""

    def test_all_three_sources_combined(self, tmp_path: Path):
        project_root = tmp_path / "project"
        project_root.mkdir()
        project_key = str(project_root)

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "global-only": {"command": "global"},
                "shared-name": {"command": "global-version"},
            },
            "projects": {
                project_key: {
                    "mcpServers": {
                        "local-only": {"command": "local"},
                        "shared-name": {"command": "local-version"},
                    },
                },
            },
        }))

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "shared-only": {"command": "shared"},
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )

        by_name = {s.name: s for s in result}
        assert len(by_name) == 4
        assert by_name["global-only"].source == "user-global"
        assert by_name["global-only"].scope == "global"
        assert by_name["shared-only"].source == "project-shared"
        assert by_name["shared-only"].scope == "project"
        assert by_name["local-only"].source == "project-local"
        assert by_name["local-only"].scope == "project"
        # shared-name: project-local wins over user-global
        assert by_name["shared-name"].source == "project-local"
        assert by_name["shared-name"].config["command"] == "local-version"

    def test_defaults_claude_json_to_home_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """When claude_json_path is None, defaults to ~/.claude.json."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        claude_json = fake_home / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "home-server": {"command": "node"},
            },
        }))

        project_root = tmp_path / "project"
        project_root.mkdir()

        result, _degraded = discover_mcp_servers(project_root=project_root)
        assert len(result) == 1
        assert result[0].name == "home-server"

    def test_defaults_mcp_json_to_project_root(self, tmp_path: Path):
        """When mcp_json_path is None, defaults to <project_root>/.mcp.json."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "project-server": {"command": "node"},
            },
        }))

        result, _degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=tmp_path / "nonexistent.json",
        )
        assert len(result) == 1
        assert result[0].name == "project-server"
        assert result[0].source == "project-shared"


# -- degraded discovery --------------------------------------------------------


class TestDegradedDiscovery:
    """Tests for the degraded flag when config files contain malformed JSON."""

    def test_corrupt_claude_json_sets_degraded(self, tmp_path: Path):
        """Corrupt ~/.claude.json sets degraded=True; servers from .mcp.json still found."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{corrupt json!!")

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "shared-srv": {"command": "node"},
            },
        }))

        result, degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        assert degraded is True
        assert len(result) == 1
        assert result[0].name == "shared-srv"

    def test_corrupt_mcp_json_sets_degraded(self, tmp_path: Path):
        """Corrupt .mcp.json sets degraded=True; servers from ~/.claude.json still found."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "global-srv": {"command": "node"},
            },
        }))

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text("not json at all")

        result, degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        assert degraded is True
        assert len(result) == 1
        assert result[0].name == "global-srv"

    def test_both_valid_not_degraded(self, tmp_path: Path):
        """Both valid config files produce degraded=False."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "global-srv": {"command": "node"},
            },
        }))

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "shared-srv": {"command": "python"},
            },
        }))

        result, degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        assert degraded is False
        assert len(result) == 2

    def test_unreadable_claude_json_sets_degraded(self, tmp_path: Path):
        """Unreadable ~/.claude.json (e.g. PermissionError) sets degraded=True."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "global-srv": {"command": "node"},
            },
        }))
        claude_json.chmod(0o000)

        mcp_json = project_root / ".mcp.json"
        mcp_json.write_text(json.dumps({
            "mcpServers": {
                "shared-srv": {"command": "python"},
            },
        }))

        result, degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=claude_json,
            mcp_json_path=mcp_json,
        )
        # Restore permissions for tmp_path cleanup
        claude_json.chmod(0o644)

        assert degraded is True
        # Global servers are lost (file unreadable), but shared servers survive
        assert len(result) == 1
        assert result[0].name == "shared-srv"

    def test_missing_files_not_degraded(self, tmp_path: Path):
        """Missing files (not corrupt) produce degraded=False."""
        project_root = tmp_path / "project"
        project_root.mkdir()

        result, degraded = discover_mcp_servers(
            project_root=project_root,
            claude_json_path=tmp_path / "nonexistent.json",
            mcp_json_path=tmp_path / "also-nonexistent.json",
        )
        assert degraded is False
        assert result == ()
