"""Tests for MCP server reconciliation in the reconcile pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.model import (
    DiscoveredMcpServer,
    GeneratedMcpServer,
)
from cc_codex_bridge.reconcile import (
    build_desired_state,
    diff_desired_state,
    reconcile_desired_state,
)
from cc_codex_bridge.translate_mcp import translate_mcp_servers


def _make_project(tmp_path: Path) -> Path:
    """Create a minimal project with AGENTS.md."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Test\n")
    return project


def _write_claude_json(path: Path, servers: dict, project_root: Path | None = None, project_servers: dict | None = None) -> None:
    """Write a ~/.claude.json with MCP servers."""
    data: dict = {}
    if servers:
        data["mcpServers"] = servers
    if project_root and project_servers:
        data["projects"] = {
            str(project_root): {"mcpServers": project_servers}
        }
    path.write_text(json.dumps(data))


def _build_and_reconcile(
    project: Path,
    mcp_servers: tuple[GeneratedMcpServer, ...],
    codex_home: Path,
    bridge_home: Path,
):
    """Build desired state with MCP servers and reconcile."""
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.model import DiscoveryResult, ProjectContext

    discovery = DiscoveryResult(
        project=ProjectContext(root=project, agents_md_path=project / "AGENTS.md"),
        plugins=(),
    )
    shim = plan_claude_shim(discovery.project)
    desired = build_desired_state(
        discovery,
        shim,
        (),  # no skills
        codex_home=codex_home,
        bridge_home=bridge_home,
        mcp_servers=mcp_servers,
    )
    return reconcile_desired_state(desired)


class TestGlobalMcpReconcile:
    """Tests for global-scope MCP server reconciliation."""

    def test_fresh_reconcile_creates_global_config(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "/bin/bash", "args": ["-c", "npx wpcom"]},
                source_description="user-global",
            ),
        )

        report = _build_and_reconcile(project, servers, codex_home, bridge_home)
        assert report.applied

        config_path = codex_home / "config.toml"
        assert config_path.exists()
        content = config_path.read_text()
        assert "[mcp_servers.wpcom]" in content
        assert "npx wpcom" in content

    def test_idempotent_reconcile(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "/bin/bash", "args": ["-c", "npx wpcom"]},
                source_description="user-global",
            ),
        )

        _build_and_reconcile(project, servers, codex_home, bridge_home)
        # Second reconcile — should produce no MCP changes
        report = _build_and_reconcile(project, servers, codex_home, bridge_home)
        mcp_changes = [c for c in report.changes if c.resource_kind == "mcp_server"]
        assert len(mcp_changes) == 0

    def test_stale_server_removed(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "/bin/bash", "args": ["-c", "npx wpcom"]},
                source_description="user-global",
            ),
            GeneratedMcpServer(
                name="context7",
                scope="global",
                toml_table={"command": "npx", "args": ["context7"]},
                source_description="user-global",
            ),
        )

        r1 = _build_and_reconcile(project, servers, codex_home, bridge_home)
        mcp1 = [c for c in r1.changes if c.resource_kind == "mcp_server"]

        # Verify first run wrote both servers
        config_path = codex_home / "config.toml"
        assert config_path.exists(), "config.toml not created"
        assert "context7" in config_path.read_text(), "context7 missing after first run"

        # Remove context7
        reduced = (servers[0],)
        report = _build_and_reconcile(project, reduced, codex_home, bridge_home)
        mcp2 = [c for c in report.changes if c.resource_kind == "mcp_server"]

        content = config_path.read_text()
        assert "[mcp_servers.wpcom]" in content
        assert "context7" not in content, f"context7 still in config after removal. MCP changes: {[(c.kind, c.resource_kind) for c in mcp2]}"


class TestProjectMcpReconcile:
    """Tests for project-scope MCP server reconciliation."""

    def test_project_servers_in_project_config(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        report = _build_and_reconcile(project, servers, codex_home, bridge_home)
        assert report.applied

        project_config = project / ".codex" / "config.toml"
        assert project_config.exists()
        content = project_config.read_text()
        assert "[mcp_servers.figma]" in content
        assert "mcp.figma.com" in content

    def test_mixed_scopes_separate_files(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "npx", "args": ["wpcom"]},
                source_description="user-global",
            ),
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        _build_and_reconcile(project, servers, codex_home, bridge_home)

        global_config = codex_home / "config.toml"
        project_config = project / ".codex" / "config.toml"

        global_content = global_config.read_text()
        project_content = project_config.read_text()

        assert "wpcom" in global_content
        assert "figma" not in global_content
        assert "figma" in project_content
        assert "wpcom" not in project_content


class TestDryRun:
    """Tests for MCP dry-run behavior."""

    def test_dry_run_reports_without_writing(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        from cc_codex_bridge.claude_shim import plan_claude_shim
        from cc_codex_bridge.model import DiscoveryResult, ProjectContext

        discovery = DiscoveryResult(
            project=ProjectContext(root=project, agents_md_path=project / "AGENTS.md"),
            plugins=(),
        )
        shim = plan_claude_shim(discovery.project)
        desired = build_desired_state(
            discovery,
            shim,
            (),
            codex_home=codex_home,
            bridge_home=bridge_home,
            mcp_servers=(
                GeneratedMcpServer(
                    name="wpcom",
                    scope="global",
                    toml_table={"command": "npx", "args": ["wpcom"]},
                    source_description="user-global",
                ),
            ),
        )
        report = diff_desired_state(desired)
        assert not report.applied
        mcp_changes = [c for c in report.changes if c.resource_kind == "mcp_server"]
        assert len(mcp_changes) > 0

        # File should NOT exist
        config_path = codex_home / "config.toml"
        assert not config_path.exists()

    def test_no_servers_no_config_file(self, tmp_path):
        project = _make_project(tmp_path)
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        _build_and_reconcile(project, (), codex_home, bridge_home)

        config_path = codex_home / "config.toml"
        assert not config_path.exists()
