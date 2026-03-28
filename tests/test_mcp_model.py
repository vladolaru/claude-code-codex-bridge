"""Tests for MCP server dataclasses in the model module."""

from __future__ import annotations

import dataclasses

import pytest

from cc_codex_bridge.model import (
    DiscoveredMcpServer,
    GeneratedMcpServer,
    McpTranslationDiagnostic,
    McpTranslationResult,
)


# -- DiscoveredMcpServer -----------------------------------------------------

class TestDiscoveredMcpServer:
    """Tests for DiscoveredMcpServer dataclass."""

    def test_construction(self):
        config = {"command": "node", "args": ["server.js"]}
        server = DiscoveredMcpServer(
            name="my-server",
            scope="global",
            transport="stdio",
            source="user-global",
            config=config,
        )
        assert server.name == "my-server"
        assert server.scope == "global"
        assert server.transport == "stdio"
        assert server.source == "user-global"
        assert server.config == config

    def test_frozen(self):
        server = DiscoveredMcpServer(
            name="s",
            scope="project",
            transport="http",
            source="project-local",
            config={},
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            server.name = "changed"


# -- GeneratedMcpServer ------------------------------------------------------

class TestGeneratedMcpServer:
    """Tests for GeneratedMcpServer dataclass."""

    def test_construction(self):
        toml_table = {"type": "stdio", "command": "node", "args": ["s.js"]}
        server = GeneratedMcpServer(
            name="my-server",
            scope="project",
            toml_table=toml_table,
            source_description="project .mcp.json",
        )
        assert server.name == "my-server"
        assert server.scope == "project"
        assert server.toml_table == toml_table
        assert server.source_description == "project .mcp.json"

    def test_frozen(self):
        server = GeneratedMcpServer(
            name="s",
            scope="global",
            toml_table={},
            source_description="test",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            server.scope = "project"


# -- McpTranslationDiagnostic ------------------------------------------------

class TestMcpTranslationDiagnostic:
    """Tests for McpTranslationDiagnostic dataclass."""

    def test_construction(self):
        diag = McpTranslationDiagnostic(
            server_name="bad-server",
            message="unsupported transport: sse",
        )
        assert diag.server_name == "bad-server"
        assert diag.message == "unsupported transport: sse"

    def test_frozen(self):
        diag = McpTranslationDiagnostic(
            server_name="s",
            message="msg",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            diag.message = "other"


# -- McpTranslationResult ----------------------------------------------------

class TestMcpTranslationResult:
    """Tests for McpTranslationResult dataclass."""

    def test_construction(self):
        server = GeneratedMcpServer(
            name="srv",
            scope="global",
            toml_table={"type": "stdio"},
            source_description="test",
        )
        diag = McpTranslationDiagnostic(
            server_name="bad",
            message="skipped",
        )
        result = McpTranslationResult(
            servers=(server,),
            diagnostics=(diag,),
        )
        assert result.servers == (server,)
        assert result.diagnostics == (diag,)

    def test_frozen(self):
        result = McpTranslationResult(
            servers=(),
            diagnostics=(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.servers = ()
