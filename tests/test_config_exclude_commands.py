"""Tests for config exclude add/remove/list command handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.config_exclude_commands import (
    ExcludeCommandResult,
    ExcludeListResult,
    _matches_any,
    handle_exclude_add,
    handle_exclude_list,
    handle_exclude_remove,
    list_discoverable_entities,
)
from cc_codex_bridge.config_writer import read_config_data, write_config_data
from cc_codex_bridge.model import (
    DiscoveryResult,
    InstalledPlugin,
    ProjectContext,
    SemVer,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_FAKE_VERSION = SemVer(major=1, minor=0, patch=0)


def _make_plugin(
    tmp_path: Path,
    *,
    marketplace: str = "market",
    plugin_name: str = "alpha",
    skill_names: tuple[str, ...] = (),
    agent_names: tuple[str, ...] = (),
    command_names: tuple[str, ...] = (),
) -> InstalledPlugin:
    """Build an InstalledPlugin with fake paths for skills/agents/commands."""
    source = tmp_path / "source" / marketplace / plugin_name
    skills = tuple(source / "skills" / name for name in skill_names)
    agents = tuple(source / "agents" / name for name in agent_names)
    commands = tuple(source / "commands" / name for name in command_names)
    return InstalledPlugin(
        marketplace=marketplace,
        plugin_name=plugin_name,
        version_text="1.0.0",
        version=_FAKE_VERSION,
        installed_path=tmp_path / "installed" / marketplace / plugin_name,
        source_path=source,
        skills=skills,
        agents=agents,
        commands=commands,
    )


def _make_discovery(
    tmp_path: Path,
    *,
    plugins: tuple[InstalledPlugin, ...] = (),
    user_skills: tuple[str, ...] = (),
    user_agents: tuple[str, ...] = (),
    user_commands: tuple[str, ...] = (),
    project_skills: tuple[str, ...] = (),
    project_agents: tuple[str, ...] = (),
    project_commands: tuple[str, ...] = (),
) -> DiscoveryResult:
    """Build a DiscoveryResult with fake paths for standalone entities."""
    user_base = tmp_path / "user"
    project_base = tmp_path / "project-local"
    return DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=plugins,
        user_skills=tuple(user_base / "skills" / n for n in user_skills),
        user_agents=tuple(user_base / "agents" / n for n in user_agents),
        user_commands=tuple(user_base / "commands" / n for n in user_commands),
        project_skills=tuple(project_base / "skills" / n for n in project_skills),
        project_agents=tuple(project_base / "agents" / n for n in project_agents),
        project_commands=tuple(project_base / "commands" / n for n in project_commands),
    )


# ---------------------------------------------------------------------------
# list_discoverable_entities
# ---------------------------------------------------------------------------


class TestListDiscoverableEntities:
    """Tests for list_discoverable_entities."""

    def test_returns_correct_structure_for_plugins(self, tmp_path: Path) -> None:
        """Plugin entities are listed under the correct kind keys."""
        plugin = _make_plugin(
            tmp_path,
            marketplace="market",
            plugin_name="alpha",
            skill_names=("code-review",),
            agent_names=("reviewer.md",),
            command_names=("lint.md",),
        )
        discovery = _make_discovery(tmp_path, plugins=(plugin,))

        entities = list_discoverable_entities(discovery)

        assert "market/alpha" in entities["plugin"]
        assert "market/alpha/code-review" in entities["skill"]
        assert "market/alpha/reviewer.md" in entities["agent"]
        assert "market/alpha/lint.md" in entities["command"]

    def test_returns_user_and_project_standalone(self, tmp_path: Path) -> None:
        """User and project standalone entities are listed correctly."""
        discovery = _make_discovery(
            tmp_path,
            user_skills=("my-tool",),
            user_agents=("helper.md",),
            user_commands=("deploy.md",),
            project_skills=("local-tool",),
            project_agents=("builder.md",),
            project_commands=("test.md",),
        )

        entities = list_discoverable_entities(discovery)

        assert "user/my-tool" in entities["skill"]
        assert "user/helper.md" in entities["agent"]
        assert "user/deploy.md" in entities["command"]
        assert "project/local-tool" in entities["skill"]
        assert "project/builder.md" in entities["agent"]
        assert "project/test.md" in entities["command"]

    def test_lists_are_sorted(self, tmp_path: Path) -> None:
        """All entity lists are sorted."""
        plugin_b = _make_plugin(
            tmp_path, marketplace="market", plugin_name="beta",
            skill_names=("z-skill", "a-skill"),
        )
        plugin_a = _make_plugin(
            tmp_path, marketplace="market", plugin_name="alpha",
        )
        discovery = _make_discovery(tmp_path, plugins=(plugin_b, plugin_a))

        entities = list_discoverable_entities(discovery)

        assert entities["plugin"] == sorted(entities["plugin"])
        assert entities["skill"] == sorted(entities["skill"])

    def test_empty_discovery(self, tmp_path: Path) -> None:
        """Empty discovery yields empty lists for all kinds."""
        discovery = _make_discovery(tmp_path)
        entities = list_discoverable_entities(discovery)

        assert entities == {"plugin": [], "skill": [], "agent": [], "command": [], "mcp_server": []}


# ---------------------------------------------------------------------------
# _matches_any
# ---------------------------------------------------------------------------


class TestMatchesAny:
    """Tests for _matches_any helper."""

    def test_exact_match(self) -> None:
        """Exact match in known list returns True."""
        known = ["market/alpha", "market/beta"]
        assert _matches_any("market/alpha", known, "plugin") is True

    def test_one_part_suffix_match(self) -> None:
        """A 1-part ID matches if any known entity ends with /normalized."""
        known = ["market/alpha/code-review", "market/beta/other"]
        assert _matches_any("code-review", known, "skill") is True

    def test_one_part_no_match(self) -> None:
        """A 1-part ID that does not match any suffix returns False."""
        known = ["market/alpha/code-review"]
        assert _matches_any("nonexistent", known, "skill") is False

    def test_multi_part_no_match(self) -> None:
        """A multi-part ID not in the known list returns False."""
        known = ["market/alpha"]
        assert _matches_any("market/beta", known, "plugin") is False


# ---------------------------------------------------------------------------
# handle_exclude_add
# ---------------------------------------------------------------------------


class TestHandleExcludeAdd:
    """Tests for handle_exclude_add."""

    def test_add_plugin_valid(self, tmp_path: Path) -> None:
        """Adding a valid plugin exclusion succeeds."""
        plugin = _make_plugin(tmp_path, marketplace="market", plugin_name="alpha")
        discovery = _make_discovery(tmp_path, plugins=(plugin,))
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="plugin",
            entity_id="market/alpha",
            config_path=config_path,
            discovery=discovery,
        )

        assert isinstance(result, ExcludeCommandResult)
        assert result.success is True

        # Verify persisted to config
        data = read_config_data(config_path)
        assert "market/alpha" in data.get("exclude", {}).get("plugins", [])

    def test_add_plugin_not_found(self, tmp_path: Path) -> None:
        """Adding a plugin not in discovery fails with 'not found'."""
        discovery = _make_discovery(tmp_path)
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="plugin",
            entity_id="market/nonexistent",
            config_path=config_path,
            discovery=discovery,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_add_skill_one_part_match(self, tmp_path: Path) -> None:
        """Adding a 1-part skill ID that matches via suffix succeeds."""
        plugin = _make_plugin(
            tmp_path,
            marketplace="market",
            plugin_name="alpha",
            skill_names=("code-review",),
        )
        discovery = _make_discovery(tmp_path, plugins=(plugin,))
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="skill",
            entity_id="code-review",
            config_path=config_path,
            discovery=discovery,
        )

        assert result.success is True
        data = read_config_data(config_path)
        assert "code-review" in data.get("exclude", {}).get("skills", [])

    def test_add_invalid_kind_fails(self, tmp_path: Path) -> None:
        """Adding with an invalid kind fails."""
        discovery = _make_discovery(tmp_path)
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="bogus",
            entity_id="something",
            config_path=config_path,
            discovery=discovery,
        )

        assert result.success is False
        assert "invalid kind" in result.message.lower()

    def test_add_duplicate_fails(self, tmp_path: Path) -> None:
        """Adding an entity that is already excluded fails."""
        plugin = _make_plugin(tmp_path, marketplace="market", plugin_name="alpha")
        discovery = _make_discovery(tmp_path, plugins=(plugin,))
        config_path = tmp_path / "config.toml"

        # Add once.
        result1 = handle_exclude_add(
            kind="plugin", entity_id="market/alpha",
            config_path=config_path, discovery=discovery,
        )
        assert result1.success is True

        # Add again.
        result2 = handle_exclude_add(
            kind="plugin", entity_id="market/alpha",
            config_path=config_path, discovery=discovery,
        )
        assert result2.success is False
        assert "already" in result2.message.lower()

    def test_add_agent_normalizes_md_extension(self, tmp_path: Path) -> None:
        """Adding an agent without .md extension gets it auto-appended."""
        plugin = _make_plugin(
            tmp_path,
            marketplace="market",
            plugin_name="alpha",
            agent_names=("reviewer.md",),
        )
        discovery = _make_discovery(tmp_path, plugins=(plugin,))
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="agent",
            entity_id="reviewer",
            config_path=config_path,
            discovery=discovery,
        )

        assert result.success is True
        data = read_config_data(config_path)
        # Stored as normalized with .md extension
        assert "reviewer.md" in data.get("exclude", {}).get("agents", [])

    def test_add_command_normalizes_md_extension(self, tmp_path: Path) -> None:
        """Adding a command without .md extension gets it auto-appended."""
        plugin = _make_plugin(
            tmp_path,
            marketplace="market",
            plugin_name="alpha",
            command_names=("deploy.md",),
        )
        discovery = _make_discovery(tmp_path, plugins=(plugin,))
        config_path = tmp_path / "config.toml"

        result = handle_exclude_add(
            kind="command",
            entity_id="deploy",
            config_path=config_path,
            discovery=discovery,
        )

        assert result.success is True
        data = read_config_data(config_path)
        assert "deploy.md" in data.get("exclude", {}).get("commands", [])


# ---------------------------------------------------------------------------
# handle_exclude_remove
# ---------------------------------------------------------------------------


class TestHandleExcludeRemove:
    """Tests for handle_exclude_remove."""

    def test_remove_existing(self, tmp_path: Path) -> None:
        """Removing an existing exclusion succeeds."""
        config_path = tmp_path / "config.toml"
        write_config_data(config_path, {
            "exclude": {"plugins": ["market/alpha", "market/beta"]},
        })

        result = handle_exclude_remove(
            kind="plugin",
            entity_id="market/alpha",
            config_path=config_path,
        )

        assert result.success is True
        data = read_config_data(config_path)
        assert "market/alpha" not in data["exclude"]["plugins"]
        assert "market/beta" in data["exclude"]["plugins"]

    def test_remove_not_found(self, tmp_path: Path) -> None:
        """Removing an entity not in the exclusion list fails."""
        config_path = tmp_path / "config.toml"
        write_config_data(config_path, {
            "exclude": {"plugins": ["market/alpha"]},
        })

        result = handle_exclude_remove(
            kind="plugin",
            entity_id="market/nonexistent",
            config_path=config_path,
        )

        assert result.success is False
        assert "not found" in result.message.lower()

    def test_remove_from_empty_config(self, tmp_path: Path) -> None:
        """Removing from an empty/missing config fails gracefully."""
        config_path = tmp_path / "config.toml"

        result = handle_exclude_remove(
            kind="skill",
            entity_id="my-skill",
            config_path=config_path,
        )

        assert result.success is False

    def test_remove_agent_normalizes_md(self, tmp_path: Path) -> None:
        """Remove normalizes agent name to include .md extension."""
        config_path = tmp_path / "config.toml"
        write_config_data(config_path, {
            "exclude": {"agents": ["reviewer.md"]},
        })

        result = handle_exclude_remove(
            kind="agent",
            entity_id="reviewer",
            config_path=config_path,
        )

        assert result.success is True
        data = read_config_data(config_path)
        assert "reviewer.md" not in data["exclude"]["agents"]

    def test_remove_invalid_kind_fails(self, tmp_path: Path) -> None:
        """Remove with invalid kind fails."""
        config_path = tmp_path / "config.toml"

        result = handle_exclude_remove(
            kind="bogus",
            entity_id="something",
            config_path=config_path,
        )

        assert result.success is False
        assert "invalid kind" in result.message.lower()


# ---------------------------------------------------------------------------
# handle_exclude_list
# ---------------------------------------------------------------------------


class TestHandleExcludeList:
    """Tests for handle_exclude_list."""

    def test_list_empty(self, tmp_path: Path) -> None:
        """Listing with no config returns empty tuples for all kinds."""
        config_path = tmp_path / "config.toml"
        result = handle_exclude_list(config_path=config_path)

        assert result.plugins == ()
        assert result.skills == ()
        assert result.agents == ()
        assert result.commands == ()

    def test_list_with_data(self, tmp_path: Path) -> None:
        """Listing returns all configured exclusions."""
        config_path = tmp_path / "config.toml"
        write_config_data(config_path, {
            "exclude": {
                "plugins": ["market/alpha"],
                "skills": ["my-tool", "market/beta/other"],
                "agents": ["reviewer.md"],
                "commands": ["deploy.md"],
            },
        })

        result = handle_exclude_list(config_path=config_path)

        assert result.plugins == ("market/alpha",)
        assert result.skills == ("market/beta/other", "my-tool")
        assert result.agents == ("reviewer.md",)
        assert result.commands == ("deploy.md",)

    def test_list_returns_frozen_dataclass(self, tmp_path: Path) -> None:
        """ExcludeListResult is a frozen dataclass."""
        config_path = tmp_path / "config.toml"
        result = handle_exclude_list(config_path=config_path)

        assert isinstance(result, ExcludeListResult)
        with pytest.raises(AttributeError):
            result.plugins = ("tampered",)  # type: ignore[misc]
