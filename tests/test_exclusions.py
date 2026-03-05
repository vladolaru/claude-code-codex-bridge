"""Tests for exclusion config loading and discovery filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.exclusions import (
    SyncExclusions,
    apply_sync_exclusions,
    load_project_exclusions,
    resolve_effective_exclusions,
)
from cc_codex_bridge.model import (
    DiscoveryResult,
    InstalledPlugin,
    ProjectContext,
    ReconcileError,
    SemVer,
)


def test_load_project_exclusions_reads_default_config_and_normalizes_agent_id(make_project):
    """Project exclusions load from `.codex/bridge.toml` when present."""
    project_root, agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[exclude]\n"
        'plugins = ["market/prompt-engineer"]\n'
        'skills = ["market/prompt-engineer/internal"]\n'
        'agents = ["market/prompt-engineer/reviewer"]\n'
    )

    exclusions = load_project_exclusions(project_root)

    assert exclusions.plugins == ("market/prompt-engineer",)
    assert exclusions.skills == ("market/prompt-engineer/internal",)
    assert exclusions.agents == ("market/prompt-engineer/reviewer.md",)
    assert agents_md.exists()


def test_load_project_exclusions_rejects_invalid_id_format(make_project):
    """Malformed exclusion ids fail fast with a clear error."""
    project_root, _agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[exclude]\nplugins = [\"broken\"]\n")

    with pytest.raises(ReconcileError, match="Invalid exclusion id"):
        load_project_exclusions(project_root)


def test_resolve_effective_exclusions_prefers_cli_values_per_kind():
    """CLI exclusions replace config exclusions for the same entity kind only."""
    config = SyncExclusions(
        plugins=("market/alpha",),
        skills=("market/alpha/one",),
        agents=("market/alpha/reviewer.md",),
    )

    resolved = resolve_effective_exclusions(
        config,
        cli_exclude_skills=["market/alpha/two"],
    )

    assert resolved.plugins == ("market/alpha",)
    assert resolved.skills == ("market/alpha/two",)
    assert resolved.agents == ("market/alpha/reviewer.md",)


def test_apply_sync_exclusions_filters_plugins_skills_and_agents(tmp_path: Path):
    """Filtering excludes whole plugins and individual nested entities."""
    plugin_alpha = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-alpha",
        source_path=tmp_path / "source-alpha",
        skills=(
            tmp_path / "source-alpha" / "skills" / "portable",
            tmp_path / "source-alpha" / "skills" / "cc-only",
        ),
        agents=(
            tmp_path / "source-alpha" / "agents" / "reviewer.md",
            tmp_path / "source-alpha" / "agents" / "helper.md",
        ),
    )
    plugin_beta = InstalledPlugin(
        marketplace="market",
        plugin_name="beta",
        version_text="2.0.0",
        version=SemVer.parse("2.0.0"),
        installed_path=tmp_path / "installed-beta",
        source_path=tmp_path / "source-beta",
        skills=(tmp_path / "source-beta" / "skills" / "tooling",),
        agents=(tmp_path / "source-beta" / "agents" / "auditor.md",),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin_alpha, plugin_beta),
    )
    exclusions = SyncExclusions(
        plugins=("market/beta",),
        skills=("market/alpha/cc-only",),
        agents=("market/alpha/reviewer.md",),
    )

    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.plugins) == 1
    assert filtered.plugins[0].plugin_name == "alpha"
    assert tuple(path.name for path in filtered.plugins[0].skills) == ("portable",)
    assert tuple(path.name for path in filtered.plugins[0].agents) == ("helper.md",)
    assert report.plugins == ("market/beta",)
    assert report.skills == ("market/alpha/cc-only",)
    assert report.agents == ("market/alpha/reviewer.md",)
