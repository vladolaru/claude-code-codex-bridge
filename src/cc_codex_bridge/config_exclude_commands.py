"""Config exclude add/remove/list command handlers with discovery validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_codex_bridge.config_writer import (
    add_to_string_list,
    read_config_data,
    remove_from_string_list,
    write_config_data,
)
from cc_codex_bridge.exclusions import normalize_entity_id
from cc_codex_bridge.model import DiscoveryResult, ReconcileError

KIND_TO_KEY = {
    "plugin": "plugins",
    "skill": "skills",
    "agent": "agents",
    "command": "commands",
}


@dataclass(frozen=True)
class ExcludeCommandResult:
    """Result of an exclude add or remove operation."""

    success: bool
    message: str


@dataclass(frozen=True)
class ExcludeListResult:
    """Current exclusion lists for all entity kinds."""

    plugins: tuple[str, ...]
    skills: tuple[str, ...]
    agents: tuple[str, ...]
    commands: tuple[str, ...]


def list_discoverable_entities(discovery: DiscoveryResult) -> dict[str, list[str]]:
    """Build a dict of all discoverable entity IDs, keyed by kind.

    Keys: "plugin", "skill", "agent", "command".
    Plugin entities use ``marketplace/plugin_name`` as the ID prefix.
    Standalone user/project entities use ``user/name`` or ``project/name``.
    All lists are sorted.
    """
    plugins: list[str] = []
    skills: list[str] = []
    agents: list[str] = []
    commands: list[str] = []

    for plugin in discovery.plugins:
        prefix = f"{plugin.marketplace}/{plugin.plugin_name}"
        plugins.append(prefix)

        for skill_path in plugin.skills:
            skills.append(f"{prefix}/{skill_path.name}")

        for agent_path in plugin.agents:
            agents.append(f"{prefix}/{agent_path.name}")

        for command_path in plugin.commands:
            commands.append(f"{prefix}/{command_path.name}")

    # Standalone user entities
    for skill_path in discovery.user_skills:
        skills.append(f"user/{skill_path.name}")
    for agent_path in discovery.user_agents:
        agents.append(f"user/{agent_path.name}")
    for command_path in discovery.user_commands:
        commands.append(f"user/{command_path.name}")

    # Standalone project entities
    for skill_path in discovery.project_skills:
        skills.append(f"project/{skill_path.name}")
    for agent_path in discovery.project_agents:
        agents.append(f"project/{agent_path.name}")
    for command_path in discovery.project_commands:
        commands.append(f"project/{command_path.name}")

    return {
        "plugin": sorted(plugins),
        "skill": sorted(skills),
        "agent": sorted(agents),
        "command": sorted(commands),
    }


def _matches_any(normalized: str, known: list[str], kind: str) -> bool:
    """Check if *normalized* matches any entry in *known*.

    - Exact match returns True.
    - A 1-part ID (no ``/``) matches if any known entry ends with
      ``/<normalized>``.
    - Otherwise returns False.
    """
    if normalized in known:
        return True

    # 1-part ID: match by suffix
    if "/" not in normalized:
        suffix = f"/{normalized}"
        return any(entry.endswith(suffix) for entry in known)

    return False


def handle_exclude_add(
    *,
    kind: str,
    entity_id: str,
    config_path: Path,
    discovery: DiscoveryResult,
) -> ExcludeCommandResult:
    """Add an entity exclusion to the config file.

    1. Validate *kind* is one of: plugin, skill, agent, command.
    2. Normalize *entity_id* via the exclusions module.
    3. Validate the normalized ID matches a discovered entity.
    4. Read config, add to ``exclude.<kind_plural>``, write back.
    5. Return success/failure result.
    """
    if kind not in KIND_TO_KEY:
        valid = ", ".join(sorted(KIND_TO_KEY))
        return ExcludeCommandResult(
            success=False,
            message=f"Invalid kind '{kind}'; expected one of: {valid}",
        )

    try:
        normalized = normalize_entity_id(entity_id, kind=kind)
    except ReconcileError as exc:
        return ExcludeCommandResult(success=False, message=str(exc))

    # Validate against discovered entities
    known = list_discoverable_entities(discovery)
    if not _matches_any(normalized, known[kind], kind):
        return ExcludeCommandResult(
            success=False,
            message=f"{kind} '{entity_id}' not found in discovered entities",
        )

    # Read, modify, write
    data = read_config_data(config_path)
    exclude_table: dict = data.setdefault("exclude", {})
    key = KIND_TO_KEY[kind]
    added = add_to_string_list(exclude_table, key, normalized)

    if not added:
        return ExcludeCommandResult(
            success=False,
            message=f"{kind} '{normalized}' is already excluded",
        )

    write_config_data(config_path, data)

    return ExcludeCommandResult(
        success=True,
        message=f"Added {kind} exclusion: {normalized}",
    )


def handle_exclude_remove(
    *,
    kind: str,
    entity_id: str,
    config_path: Path,
) -> ExcludeCommandResult:
    """Remove an entity exclusion from the config file.

    1. Validate *kind*.
    2. Normalize *entity_id*.
    3. Read config, remove from ``exclude.<kind_plural>``.
    4. Return success/failure result.
    """
    if kind not in KIND_TO_KEY:
        valid = ", ".join(sorted(KIND_TO_KEY))
        return ExcludeCommandResult(
            success=False,
            message=f"Invalid kind '{kind}'; expected one of: {valid}",
        )

    try:
        normalized = normalize_entity_id(entity_id, kind=kind)
    except ReconcileError as exc:
        return ExcludeCommandResult(success=False, message=str(exc))

    data = read_config_data(config_path)
    exclude_table: dict = data.get("exclude", {})
    key = KIND_TO_KEY[kind]
    removed = remove_from_string_list(exclude_table, key, normalized)

    if not removed:
        return ExcludeCommandResult(
            success=False,
            message=f"{kind} '{normalized}' not found in exclusion list",
        )

    write_config_data(config_path, data)

    return ExcludeCommandResult(
        success=True,
        message=f"Removed {kind} exclusion: {normalized}",
    )


def handle_exclude_list(
    *,
    config_path: Path,
) -> ExcludeListResult:
    """List current exclusions for all entity kinds from the config file."""
    data = read_config_data(config_path)
    exclude_table = data.get("exclude", {})

    return ExcludeListResult(
        plugins=tuple(sorted(str(v) for v in exclude_table.get("plugins", []))),
        skills=tuple(sorted(str(v) for v in exclude_table.get("skills", []))),
        agents=tuple(sorted(str(v) for v in exclude_table.get("agents", []))),
        commands=tuple(sorted(str(v) for v in exclude_table.get("commands", []))),
    )
