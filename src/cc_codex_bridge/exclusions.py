"""Exclusion config and filtering for Codex bridge sync."""

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
            if skill_id in excluded_skill_set or skill_path.name in excluded_skill_set:
                excluded_skills.append(skill_id)
                continue
            kept_skills.append(skill_path)

        kept_agents = []
        for agent_path in plugin.agents:
            agent_id = _agent_id(plugin.marketplace, plugin.plugin_name, agent_path.name)
            if agent_id in excluded_agent_set or agent_path.name in excluded_agent_set:
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
                commands=plugin.commands,
            )
        )

    # Filter standalone user skills
    kept_user_skills: list[Path] = []
    for skill_path in discovery.user_skills:
        if _matches_standalone_exclusion(skill_path.name, "user", excluded_skill_set):
            excluded_skills.append(f"user/{skill_path.name}")
        else:
            kept_user_skills.append(skill_path)

    # Filter standalone project skills
    kept_project_skills: list[Path] = []
    for skill_path in discovery.project_skills:
        if _matches_standalone_exclusion(skill_path.name, "project", excluded_skill_set):
            excluded_skills.append(f"project/{skill_path.name}")
        else:
            kept_project_skills.append(skill_path)

    # Filter standalone user agents
    kept_user_agents: list[Path] = []
    for agent_path in discovery.user_agents:
        if _matches_standalone_exclusion(agent_path.name, "user", excluded_agent_set):
            excluded_agents.append(f"user/{agent_path.name}")
        else:
            kept_user_agents.append(agent_path)

    # Filter standalone project agents
    kept_project_agents: list[Path] = []
    for agent_path in discovery.project_agents:
        if _matches_standalone_exclusion(agent_path.name, "project", excluded_agent_set):
            excluded_agents.append(f"project/{agent_path.name}")
        else:
            kept_project_agents.append(agent_path)

    filtered_result = DiscoveryResult(
        project=discovery.project,
        plugins=tuple(filtered_plugins),
        user_skills=tuple(kept_user_skills),
        user_agents=tuple(kept_user_agents),
        user_commands=discovery.user_commands,
        project_skills=tuple(kept_project_skills),
        project_agents=tuple(kept_project_agents),
        project_commands=discovery.project_commands,
        user_claude_md=discovery.user_claude_md,
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
    """Normalize one exclusion entity id and validate shape.

    Plugins require exactly 2 parts (marketplace/plugin).

    Skills and agents accept 1, 2, or 3 parts:
    - 1 part (``name``): matches by name against all scopes
    - 2 parts (``scope/name``): matches by scope + name (user, project)
    - 3 parts (``marketplace/plugin/name``): matches plugin sources
    """
    raw = value.strip()
    parts = [part.strip() for part in raw.split("/")]

    if kind == "plugin":
        if len(parts) != 2 or any(not part for part in parts):
            raise ReconcileError(
                f"Invalid exclusion id `{value}` for kind `{kind}`; expected "
                "marketplace/plugin"
            )
        return "/".join(parts)

    # skills and agents: 1, 2, or 3 parts
    if len(parts) not in (1, 2, 3) or any(not part for part in parts):
        raise ReconcileError(
            f"Invalid exclusion id `{value}` for kind `{kind}`; expected "
            "name, scope/name, or marketplace/plugin/name"
        )

    # Auto-append .md to the agent leaf name
    if kind == "agent":
        leaf_index = len(parts) - 1
        if not parts[leaf_index].endswith(".md"):
            parts[leaf_index] = f"{parts[leaf_index]}.md"

    return "/".join(parts)


def _matches_standalone_exclusion(name: str, scope: str, exclusion_set: set[str]) -> bool:
    """Check if a standalone entity matches any exclusion pattern.

    A standalone entity matches if the exclusion set contains:
    - the bare name (1-part match, applies to all scopes), or
    - the scoped name ``scope/name`` (2-part match, scope-specific).
    """
    # 1-part match: bare name matches all scopes
    if name in exclusion_set:
        return True
    # 2-part match: scope/name
    if f"{scope}/{name}" in exclusion_set:
        return True
    return False


def _plugin_id(marketplace: str, plugin_name: str) -> str:
    """Build canonical plugin exclusion id."""
    return f"{marketplace}/{plugin_name}"


def _skill_id(marketplace: str, plugin_name: str, skill_name: str) -> str:
    """Build canonical skill exclusion id."""
    return f"{marketplace}/{plugin_name}/{skill_name}"


def _agent_id(marketplace: str, plugin_name: str, agent_filename: str) -> str:
    """Build canonical agent exclusion id."""
    return f"{marketplace}/{plugin_name}/{agent_filename}"
