"""Exclusion config and filtering for Codex interop sync."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from cc_codex_bridge.model import DiscoveryResult, InstalledPlugin, ReconcileError
from cc_codex_bridge.text import read_utf8_text


DEFAULT_CONFIG_RELATIVE_PATH = Path(".codex") / "bridge.toml"


@dataclass(frozen=True)
class SyncExclusions:
    """Normalized exclusion sets by entity kind."""

    plugins: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExclusionReport:
    """Entities actually excluded from one discovery result."""

    plugins: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()


def load_project_exclusions(
    project_root: str | Path,
    *,
    config_relative_path: str | Path = DEFAULT_CONFIG_RELATIVE_PATH,
) -> SyncExclusions:
    """Load exclusions from `.codex/bridge.toml` if present."""
    root = Path(project_root).expanduser().resolve()
    config_path = root / config_relative_path
    if not config_path.exists():
        return SyncExclusions()
    if not config_path.is_file():
        raise ReconcileError(f"Exclusion config path is not a file: {config_path}")

    try:
        payload = tomllib.loads(
            read_utf8_text(config_path, label="exclusion config", error_type=ReconcileError)
        )
    except tomllib.TOMLDecodeError as exc:
        raise ReconcileError(f"Invalid TOML exclusion config: {config_path}") from exc

    exclude_table = payload.get("exclude", {})
    if not isinstance(exclude_table, dict):
        raise ReconcileError(f"`exclude` must be a TOML table in: {config_path}")

    return SyncExclusions(
        plugins=_normalize_id_list(
            _read_string_list(exclude_table, "plugins", config_path),
            kind="plugin",
        ),
        skills=_normalize_id_list(
            _read_string_list(exclude_table, "skills", config_path),
            kind="skill",
        ),
        agents=_normalize_id_list(
            _read_string_list(exclude_table, "agents", config_path),
            kind="agent",
        ),
    )


def resolve_effective_exclusions(
    config: SyncExclusions,
    *,
    cli_exclude_plugins: list[str] | None = None,
    cli_exclude_skills: list[str] | None = None,
    cli_exclude_agents: list[str] | None = None,
) -> SyncExclusions:
    """Resolve per-kind exclusions, letting CLI values override config values."""
    plugin_values = (
        config.plugins
        if cli_exclude_plugins is None
        else _normalize_id_list(cli_exclude_plugins, kind="plugin")
    )
    skill_values = (
        config.skills
        if cli_exclude_skills is None
        else _normalize_id_list(cli_exclude_skills, kind="skill")
    )
    agent_values = (
        config.agents
        if cli_exclude_agents is None
        else _normalize_id_list(cli_exclude_agents, kind="agent")
    )
    return SyncExclusions(plugins=plugin_values, skills=skill_values, agents=agent_values)


def apply_sync_exclusions(
    discovery: DiscoveryResult,
    exclusions: SyncExclusions,
) -> tuple[DiscoveryResult, ExclusionReport]:
    """Filter discovered plugins/skills/agents by configured exclusions."""
    excluded_plugins: list[str] = []
    excluded_skills: list[str] = []
    excluded_agents: list[str] = []
    filtered_plugins: list[InstalledPlugin] = []

    excluded_plugin_set = set(exclusions.plugins)
    excluded_skill_set = set(exclusions.skills)
    excluded_agent_set = set(exclusions.agents)

    for plugin in discovery.plugins:
        plugin_id = _plugin_id(plugin.marketplace, plugin.plugin_name)
        if plugin_id in excluded_plugin_set:
            excluded_plugins.append(plugin_id)
            continue

        kept_skills = []
        for skill_path in plugin.skills:
            skill_id = _skill_id(plugin.marketplace, plugin.plugin_name, skill_path.name)
            if skill_id in excluded_skill_set:
                excluded_skills.append(skill_id)
                continue
            kept_skills.append(skill_path)

        kept_agents = []
        for agent_path in plugin.agents:
            agent_id = _agent_id(plugin.marketplace, plugin.plugin_name, agent_path.name)
            if agent_id in excluded_agent_set:
                excluded_agents.append(agent_id)
                continue
            kept_agents.append(agent_path)

        filtered_plugins.append(
            InstalledPlugin(
                marketplace=plugin.marketplace,
                plugin_name=plugin.plugin_name,
                version_text=plugin.version_text,
                version=plugin.version,
                installed_path=plugin.installed_path,
                source_path=plugin.source_path,
                skills=tuple(kept_skills),
                agents=tuple(kept_agents),
            )
        )

    filtered_result = DiscoveryResult(
        project=discovery.project,
        plugins=tuple(filtered_plugins),
        user_skills=discovery.user_skills,
        user_agents=discovery.user_agents,
        project_skills=discovery.project_skills,
        project_agents=discovery.project_agents,
    )
    report = ExclusionReport(
        plugins=tuple(sorted(set(excluded_plugins))),
        skills=tuple(sorted(set(excluded_skills))),
        agents=tuple(sorted(set(excluded_agents))),
    )
    return filtered_result, report


def _read_string_list(table: dict[str, object], key: str, config_path: Path) -> list[str]:
    """Read one string-list key from the `[exclude]` table."""
    raw = table.get(key, [])
    if raw is None:
        return []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ReconcileError(f"`exclude.{key}` must be a list of strings in: {config_path}")
    return [item.strip() for item in raw if item.strip()]


def _normalize_id_list(values: list[str] | tuple[str, ...], *, kind: str) -> tuple[str, ...]:
    """Normalize and validate a list of exclusion ids."""
    return tuple(sorted({_normalize_entity_id(value, kind=kind) for value in values}))


def _normalize_entity_id(value: str, *, kind: str) -> str:
    """Normalize one exclusion entity id and validate shape."""
    raw = value.strip()
    parts = [part.strip() for part in raw.split("/")]
    required_parts = 2 if kind == "plugin" else 3
    if len(parts) != required_parts or any(not part for part in parts):
        raise ReconcileError(
            f"Invalid exclusion id `{value}` for kind `{kind}`; expected "
            f"{'marketplace/plugin' if kind == 'plugin' else 'marketplace/plugin/name'}"
        )

    if kind == "agent" and not parts[2].endswith(".md"):
        parts[2] = f"{parts[2]}.md"
    return "/".join(parts)


def _plugin_id(marketplace: str, plugin_name: str) -> str:
    """Build canonical plugin exclusion id."""
    return f"{marketplace}/{plugin_name}"


def _skill_id(marketplace: str, plugin_name: str, skill_name: str) -> str:
    """Build canonical skill exclusion id."""
    return f"{marketplace}/{plugin_name}/{skill_name}"


def _agent_id(marketplace: str, plugin_name: str, agent_filename: str) -> str:
    """Build canonical agent exclusion id."""
    return f"{marketplace}/{plugin_name}/{agent_filename}"
