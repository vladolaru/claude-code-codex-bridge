"""Tests for MCP server reconciliation in the reconcile pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.model import GeneratedMcpServer
from cc_codex_bridge.reconcile import (
    build_desired_state,
    diff_desired_state,
    reconcile_desired_state,
)


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

    def test_fresh_reconcile_creates_global_config(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_idempotent_reconcile(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_stale_server_removed(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_release_emitted_when_shared_mcp_has_other_owners(
        self, tmp_path, make_project,
    ):
        """Dropping one owner of a shared MCP emits a release Change, not remove.

        The file in ~/.codex/config.toml stays because another project
        still owns the entry; the release Change surfaces the
        ownership-drop in the report so status doesn't look like a no-op.
        """
        first_project, _ = make_project("project-a")
        second_project, _ = make_project("project-b")
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

        # Both projects own the same MCP.
        _build_and_reconcile(first_project, servers, codex_home, bridge_home)
        _build_and_reconcile(second_project, servers, codex_home, bridge_home)

        # First project drops the MCP from its desired state (e.g. via
        # exclusion).
        report = _build_and_reconcile(first_project, (), codex_home, bridge_home)

        release_changes = [
            c for c in report.changes
            if c.kind == "release" and c.resource_kind == "mcp_server"
        ]
        assert len(release_changes) == 1, (
            f"expected one release Change, got: "
            f"{[(c.kind, c.resource_kind, c.label) for c in report.changes]}"
        )
        assert release_changes[0].label == "wpcom"

        # No remove for wpcom — second_project still owns it.
        assert all(
            not (c.kind == "remove" and c.resource_kind == "mcp_server" and c.label == "wpcom")
            for c in report.changes
        )

        # The entry stays in the global config.toml.
        config_path = codex_home / "config.toml"
        assert "[mcp_servers.wpcom]" in config_path.read_text()


class TestProjectMcpReconcile:
    """Tests for project-scope MCP server reconciliation."""

    def test_project_servers_in_project_config(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_mixed_scopes_separate_files(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_dry_run_reports_without_writing(self, tmp_path, make_project):
        project, _ = make_project()
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

    def test_no_servers_no_config_file(self, tmp_path, make_project):
        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        _build_and_reconcile(project, (), codex_home, bridge_home)

        config_path = codex_home / "config.toml"
        assert not config_path.exists()


class TestCleanMcpServers:
    """Tests for MCP server cleanup during clean_project."""

    def test_clean_removes_project_mcp_servers(self, tmp_path, make_project):
        """clean_project removes bridge-owned MCP entries from project config.toml."""
        from cc_codex_bridge.reconcile import clean_project

        project, _ = make_project()
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

        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Verify project config.toml has the server entry
        project_config = project / ".codex" / "config.toml"
        assert project_config.exists()
        assert "figma" in project_config.read_text()

        # Clean the project
        report = clean_project(project, bridge_home=bridge_home)
        assert report.applied

        # Project config.toml should no longer have bridge entries.
        # write_codex_config removes the file when content is empty.
        assert not project_config.exists()

    def test_clean_removes_global_mcp_when_last_owner(self, tmp_path, make_project):
        """clean_project removes global MCP entries when this is the last owner."""
        from cc_codex_bridge.reconcile import clean_project

        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "npx", "args": ["wpcom"]},
                source_description="user-global",
            ),
        )

        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Verify global config.toml has the server entry
        global_config = codex_home / "config.toml"
        assert global_config.exists()
        assert "wpcom" in global_config.read_text()

        # Clean the project (last owner)
        report = clean_project(project, bridge_home=bridge_home)
        assert report.applied
        assert report.ownership_released

        # Global config.toml should no longer have the wpcom entry.
        # Since it was the only entry, write_codex_config removes the file.
        assert not global_config.exists()

    def test_clean_preserves_global_mcp_with_other_owners(self, tmp_path, make_project):
        """clean_project preserves global MCP entries when another project still owns them."""
        from cc_codex_bridge.reconcile import clean_project
        from cc_codex_bridge.registry import GlobalResourceRegistry, GLOBAL_REGISTRY_FILENAME

        project_a, _ = make_project("project-a")
        project_b, _ = make_project("project-b")

        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "npx", "args": ["wpcom"]},
                source_description="user-global",
            ),
        )

        # Reconcile both projects with the same global server
        _build_and_reconcile(project_a, servers, codex_home, bridge_home)
        _build_and_reconcile(project_b, servers, codex_home, bridge_home)

        # Verify both projects own the server
        registry = GlobalResourceRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
        assert registry is not None
        assert "wpcom" in registry.mcp_servers
        assert len(registry.mcp_servers["wpcom"].owners) == 2

        # Clean project A
        report = clean_project(project_a, bridge_home=bridge_home)
        assert report.applied
        assert report.ownership_released

        # Global config.toml should still have wpcom (project B still owns it)
        global_config = codex_home / "config.toml"
        assert global_config.exists()
        assert "wpcom" in global_config.read_text()

        # Registry should still have wpcom with project B as owner
        registry = GlobalResourceRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
        assert registry is not None
        assert "wpcom" in registry.mcp_servers
        assert len(registry.mcp_servers["wpcom"].owners) == 1
        assert registry.mcp_servers["wpcom"].owners[0] == project_b.resolve()

    def test_clean_removes_mixed_scope_mcp_servers(self, tmp_path, make_project):
        """clean_project handles both global and project MCP server cleanup."""
        from cc_codex_bridge.reconcile import clean_project

        project, _ = make_project()
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

        # Verify both config files exist
        global_config = codex_home / "config.toml"
        project_config = project / ".codex" / "config.toml"
        assert global_config.exists()
        assert project_config.exists()

        # Clean the project
        report = clean_project(project, bridge_home=bridge_home)
        assert report.applied

        # Both config files should have MCP entries removed
        assert not global_config.exists()
        assert not project_config.exists()


class TestUninstallMcpServers:
    """Tests for MCP server cleanup during uninstall."""

    def test_uninstall_removes_global_mcp_from_config(self, tmp_path, make_project):
        """uninstall_all removes remaining MCP entries from global config.toml."""
        from cc_codex_bridge.reconcile import uninstall_all

        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"
        launchagents_dir = tmp_path / "LaunchAgents"
        launchagents_dir.mkdir()

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "npx", "args": ["wpcom"]},
                source_description="user-global",
            ),
        )

        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Verify global config has the entry
        global_config = codex_home / "config.toml"
        assert global_config.exists()
        assert "wpcom" in global_config.read_text()

        # Run full uninstall
        report = uninstall_all(
            codex_home=codex_home,
            bridge_home=bridge_home,
            launchagents_dir=launchagents_dir,
        )
        assert report.applied

        # Global config.toml should have MCP entries removed
        assert not global_config.exists()


class TestUserAuthoredMcpPreservation:
    """Verify that user-authored MCP entries are never adopted as managed."""

    def test_user_authored_project_entry_not_tracked_as_managed(self, tmp_path, make_project):
        """A pre-existing user-authored MCP entry with the same name as a
        bridge-discovered server must not be recorded in managed_mcp_servers.
        Otherwise ``clean`` would delete it even though the bridge never wrote it.
        """
        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        # Pre-create a user-authored config.toml with a figma entry
        project_codex = project / ".codex"
        project_codex.mkdir(parents=True)
        project_config = project_codex / "config.toml"
        project_config.write_text(
            '[mcp_servers.figma]\nurl = "https://user-authored.example.com/mcp"\n'
        )

        # Bridge discovers a figma server from Claude Code config
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

        # The user-authored entry should be preserved (not overwritten)
        content = project_config.read_text()
        assert "user-authored.example.com" in content
        assert "mcp.figma.com" not in content

        # Verify the state does NOT track figma as managed
        from cc_codex_bridge.bridge_home import project_state_dir
        from cc_codex_bridge.state import BridgeState
        state_path = project_state_dir(project, bridge_home=bridge_home) / "state.json"
        state = BridgeState.from_path(state_path)
        assert "figma" not in state.managed_mcp_servers

    def test_clean_preserves_user_authored_entry(self, tmp_path, make_project):
        """After reconcile skips a user-authored entry, clean must not remove it."""
        from cc_codex_bridge.reconcile import clean_project

        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        # Pre-create a user-authored config.toml with a figma entry
        project_codex = project / ".codex"
        project_codex.mkdir(parents=True)
        project_config = project_codex / "config.toml"
        project_config.write_text(
            '[mcp_servers.figma]\nurl = "https://user-authored.example.com/mcp"\n'
        )

        # Bridge discovers a figma server from Claude Code config
        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Now clean the project
        clean_project(project, bridge_home=bridge_home)

        # The user-authored entry must survive clean
        content = project_config.read_text()
        assert "user-authored.example.com" in content

    def test_user_authored_global_entry_not_tracked_in_registry(self, tmp_path, make_project):
        """A pre-existing user-authored global MCP entry with the same name
        as a bridge-discovered server must not be recorded in the registry.
        Otherwise ``clean`` would delete it.
        """
        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        # Pre-create a user-authored global config.toml with a wpcom entry
        codex_home.mkdir(parents=True)
        global_config = codex_home / "config.toml"
        global_config.write_text(
            '[mcp_servers.wpcom]\ncommand = "user-authored-cmd"\n'
        )

        # Bridge discovers a wpcom server from Claude Code config
        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "bridge-cmd"},
                source_description="user-global",
            ),
        )

        report = _build_and_reconcile(project, servers, codex_home, bridge_home)
        assert report.applied

        # The user-authored entry should be preserved (not overwritten)
        content = global_config.read_text()
        assert "user-authored-cmd" in content
        assert "bridge-cmd" not in content

        # Verify the registry does NOT track wpcom as owned by this project
        import json as _json
        registry_path = bridge_home / "registry.json"
        assert registry_path.exists(), "Registry file should exist after reconcile"
        registry_data = _json.loads(registry_path.read_text())
        mcp_servers = registry_data.get("mcp_servers", {})
        if "wpcom" in mcp_servers:
            owners = mcp_servers["wpcom"].get("owners", [])
            assert str(project) not in owners, (
                "Project should not be registered as owner of user-authored global MCP entry"
            )


class TestCorruptConfigTomlPlanning:
    """Corrupt config.toml must be caught during planning, not during apply."""

    def test_diff_with_corrupt_global_config_toml_raises_cleanly(self, tmp_path: Path, make_project) -> None:
        """Corrupt global config.toml must be caught during planning (diff), not during apply."""
        from cc_codex_bridge.claude_shim import plan_claude_shim
        from cc_codex_bridge.model import DiscoveryResult, ProjectContext

        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        bridge_home = tmp_path / "bridge-home"

        # Write corrupt global config.toml
        (codex_home / "config.toml").write_text("[broken\nnot valid", encoding="utf-8")

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
                    name="my-server",
                    scope="global",
                    toml_table={"command": "my-cmd"},
                    source_description="user-global",
                ),
            ),
        )

        # diff_desired_state runs the planning phase only (no apply).
        # It should raise because planning validates config.toml parseability.
        with pytest.raises(ValueError, match="invalid TOML"):
            diff_desired_state(desired)

    def test_diff_with_corrupt_project_config_toml_raises_cleanly(self, tmp_path: Path, make_project) -> None:
        """Corrupt project config.toml must be caught during planning (diff), not during apply."""
        from cc_codex_bridge.claude_shim import plan_claude_shim
        from cc_codex_bridge.model import DiscoveryResult, ProjectContext

        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        # Write corrupt project config.toml
        project_codex = project / ".codex"
        project_codex.mkdir(parents=True)
        (project_codex / "config.toml").write_text("[broken\nnot valid", encoding="utf-8")

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
                    name="my-server",
                    scope="project",
                    toml_table={"command": "my-cmd"},
                    source_description="project-local",
                ),
            ),
        )

        with pytest.raises(ValueError, match="invalid TOML"):
            diff_desired_state(desired)

    def test_reconcile_with_corrupt_global_config_toml_raises_before_registry_write(self, tmp_path: Path, make_project) -> None:
        """Corrupt config.toml must prevent registry writes — no orphaned ownership."""
        from cc_codex_bridge.claude_shim import plan_claude_shim
        from cc_codex_bridge.model import DiscoveryResult, ProjectContext
        from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME

        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        bridge_home = tmp_path / "bridge-home"

        # Write corrupt global config.toml
        (codex_home / "config.toml").write_text("[broken\nnot valid", encoding="utf-8")

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
                    name="my-server",
                    scope="global",
                    toml_table={"command": "my-cmd"},
                    source_description="user-global",
                ),
            ),
        )

        # reconcile should also fail during planning (before registry write)
        with pytest.raises(ValueError, match="invalid TOML"):
            reconcile_desired_state(desired)

        # Registry must not exist — no orphaned ownership claims
        registry_path = bridge_home / GLOBAL_REGISTRY_FILENAME
        assert not registry_path.exists(), (
            "Registry should not be written when planning fails on corrupt config.toml"
        )

    def test_project_sync_succeeds_with_corrupt_global_config(self, tmp_path, make_project):
        """Project-only MCP sync must not fail due to unrelated corrupt global config.toml."""
        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        codex_home.mkdir()
        bridge_home = tmp_path / "bridge-home"

        # Write corrupt global config.toml — irrelevant to project scope
        (codex_home / "config.toml").write_text("[broken\nnot valid", encoding="utf-8")

        # Reconcile with project-scoped server only — should succeed
        report = _build_and_reconcile(
            project,
            (
                GeneratedMcpServer(
                    name="my-server",
                    scope="project",
                    toml_table={"command": "my-cmd"},
                    source_description="project-local",
                ),
            ),
            codex_home,
            bridge_home,
        )

        project_config = project / ".codex" / "config.toml"
        assert project_config.exists()
        assert "my-server" in project_config.read_text()

    def test_global_sync_succeeds_with_corrupt_project_config(self, tmp_path, make_project):
        """Global-only MCP sync must not fail due to unrelated corrupt project config.toml."""
        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        # Write corrupt project config.toml — irrelevant to global scope
        project_codex = project / ".codex"
        project_codex.mkdir(parents=True)
        (project_codex / "config.toml").write_text("[broken\nnot valid", encoding="utf-8")

        # Reconcile with global-scoped server only — should succeed
        report = _build_and_reconcile(
            project,
            (
                GeneratedMcpServer(
                    name="my-server",
                    scope="global",
                    toml_table={"command": "my-cmd"},
                    source_description="user-global",
                ),
            ),
            codex_home,
            bridge_home,
        )

        global_config = codex_home / "config.toml"
        assert global_config.exists()
        assert "my-server" in global_config.read_text()


class TestMultiProjectGlobalOwnership:
    """Global MCP entries shared across multiple projects."""

    def test_second_project_updates_config_when_definition_changed(self, tmp_path, make_project):
        """When project B adopts a global server whose definition changed, config.toml must update."""
        project_a, _ = make_project("project-a")
        project_b, _ = make_project("project-b")
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        # Project A bridges wpcom v1
        _build_and_reconcile(
            project_a,
            (
                GeneratedMcpServer(
                    name="wpcom",
                    scope="global",
                    toml_table={"command": "wpcom-server", "args": ["--version=1"]},
                    source_description="user-global",
                ),
            ),
            codex_home,
            bridge_home,
        )

        global_config = codex_home / "config.toml"
        assert "version=1" in global_config.read_text()

        # Project B bridges wpcom v2 (definition changed)
        _build_and_reconcile(
            project_b,
            (
                GeneratedMcpServer(
                    name="wpcom",
                    scope="global",
                    toml_table={"command": "wpcom-server", "args": ["--version=2"]},
                    source_description="user-global",
                ),
            ),
            codex_home,
            bridge_home,
        )

        # Config must reflect v2, not stay on v1
        config_content = global_config.read_text()
        assert "version=2" in config_content, (
            "First-time adoption of changed global server must update config.toml"
        )
        assert "version=1" not in config_content


class TestDiskPresenceVerification:
    """Reconcile must restore MCP entries removed externally from config.toml."""

    def test_global_entry_restored_after_external_deletion(self, tmp_path, make_project):
        """If a bridge-owned global MCP entry is externally deleted, reconcile restores it."""
        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        servers = (
            GeneratedMcpServer(
                name="wpcom",
                scope="global",
                toml_table={"command": "wpcom-server"},
                source_description="user-global",
            ),
        )

        # First reconcile: creates the entry
        _build_and_reconcile(project, servers, codex_home, bridge_home)
        global_config = codex_home / "config.toml"
        assert "wpcom" in global_config.read_text()

        # Simulate external deletion: remove the entry from config.toml
        global_config.write_text("", encoding="utf-8")
        assert "wpcom" not in global_config.read_text()

        # Second reconcile: same desired state, unchanged hash — must restore
        _build_and_reconcile(project, servers, codex_home, bridge_home)
        assert "wpcom" in global_config.read_text(), (
            "Reconcile must restore bridge-owned entries removed externally"
        )

    def test_project_entry_restored_after_external_deletion(self, tmp_path, make_project):
        """If a bridge-owned project MCP entry is externally deleted, reconcile restores it."""
        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        # First reconcile
        _build_and_reconcile(project, servers, codex_home, bridge_home)
        project_config = project / ".codex" / "config.toml"
        assert "figma" in project_config.read_text()

        # Simulate external deletion
        project_config.write_text("", encoding="utf-8")

        # Second reconcile: must restore
        _build_and_reconcile(project, servers, codex_home, bridge_home)
        assert "figma" in project_config.read_text(), (
            "Reconcile must restore bridge-owned entries removed externally"
        )


class TestSymlinkContainment:
    """Project MCP config writes must not follow symlinks outside the project."""

    def test_symlinked_codex_dir_rejected_on_reconcile(self, tmp_path, make_project):
        """Reconcile must refuse to write through a symlinked .codex/ directory."""
        from cc_codex_bridge.reconcile import ReconcileError

        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        # Create a symlink: project/.codex -> /tmp/somewhere-else
        external_dir = tmp_path / "external-target"
        external_dir.mkdir()
        codex_link = project / ".codex"
        codex_link.symlink_to(external_dir)

        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        with pytest.raises(ReconcileError, match="resolves outside expected root"):
            _build_and_reconcile(project, servers, codex_home, bridge_home)

    def test_symlinked_codex_dir_rejected_on_clean(self, tmp_path, make_project):
        """clean_project must refuse to read through a symlinked .codex/ directory."""
        from cc_codex_bridge.reconcile import ReconcileError, clean_project

        project, _ = make_project()
        codex_home = tmp_path / "codex-home"
        bridge_home = tmp_path / "bridge-home"

        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )

        # First reconcile creates the project state with MCP entries
        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Replace .codex with a symlink to an external directory
        import shutil
        codex_dir = project / ".codex"
        shutil.rmtree(codex_dir)
        external_dir = tmp_path / "external-target"
        external_dir.mkdir(exist_ok=True)
        codex_dir.symlink_to(external_dir)

        with pytest.raises(ReconcileError, match="resolves outside expected root"):
            clean_project(project, bridge_home=bridge_home)


def _build_and_reconcile_degraded(
    project: Path,
    mcp_servers: tuple[GeneratedMcpServer, ...],
    codex_home: Path,
    bridge_home: Path,
):
    """Build desired state with degraded MCP discovery and reconcile."""
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.model import DiscoveryResult, ProjectContext

    discovery = DiscoveryResult(
        project=ProjectContext(root=project, agents_md_path=project / "AGENTS.md"),
        plugins=(),
        mcp_servers=mcp_servers,
        mcp_discovery_degraded=True,
    )
    shim = plan_claude_shim(discovery.project)
    desired = build_desired_state(
        discovery,
        shim,
        (),
        codex_home=codex_home,
        bridge_home=bridge_home,
        mcp_servers=mcp_servers,
        mcp_discovery_degraded=True,
    )
    return reconcile_desired_state(desired)


class TestDegradedDiscoveryPreservation:
    """Verify that corrupt config files don't trigger stale-entry removal."""

    def test_degraded_discovery_preserves_existing_mcp_entries(self, tmp_path, make_project):
        """When MCP discovery is degraded, previously-bridged entries survive."""
        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        # First reconcile: normal, creates MCP entries
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
        assert global_config.exists()
        assert project_config.exists()

        # Second reconcile: degraded (no MCP servers, but degraded=True)
        # Should preserve existing entries, not delete them
        report = _build_and_reconcile_degraded(
            project, (), codex_home, bridge_home
        )

        assert global_config.exists()
        assert "wpcom" in global_config.read_text()
        assert project_config.exists()
        assert "figma" in project_config.read_text()

    def test_degraded_discovery_with_new_server_preserves_ownership(self, tmp_path, make_project):
        """When degraded + new server arrives, previously-owned entries keep ownership."""
        import hashlib
        from cc_codex_bridge.state import BridgeState

        project, _ = make_project()
        codex_home = tmp_path / "codex"
        bridge_home = tmp_path / "bridge"

        # First reconcile: normal, creates MCP entries
        servers = (
            GeneratedMcpServer(
                name="figma",
                scope="project",
                toml_table={"url": "https://mcp.figma.com/mcp"},
                source_description="project-local",
            ),
        )
        _build_and_reconcile(project, servers, codex_home, bridge_home)

        # Verify first run tracked figma
        project_hash = hashlib.sha256(str(project).encode()).hexdigest()[:16]
        state_path = bridge_home / "projects" / project_hash / "state.json"
        state = BridgeState.from_path(state_path)
        assert "figma" in state.managed_mcp_servers

        # Second reconcile: degraded, but a new server appears (partially readable config)
        # figma is NOT in the discovered set (lost due to corruption),
        # but a new server "linear" is discovered.
        new_servers = (
            GeneratedMcpServer(
                name="linear",
                scope="project",
                toml_table={"url": "https://mcp.linear.app/mcp"},
                source_description="project-local",
            ),
        )
        _build_and_reconcile_degraded(project, new_servers, codex_home, bridge_home)

        # figma must still be in managed_mcp_servers (carried forward from state)
        state = BridgeState.from_path(state_path)
        assert "figma" in state.managed_mcp_servers, (
            "degraded discovery with new additions must carry forward previously-owned entries"
        )
        assert "linear" in state.managed_mcp_servers
