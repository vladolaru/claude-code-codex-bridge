"""Discovery for Codex bridge generation."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import shutil
import subprocess

from cc_codex_bridge.model import (
    DiscoveryError,
    DiscoveryResult,
    InstalledPlugin,
    InstalledPluginVersion,
    ProjectContext,
    SemVer,
)
from cc_codex_bridge.text import read_utf8_text


AGENTS_MD = "AGENTS.md"
DEFAULT_CLAUDE_HOME = Path.home() / ".claude"
CLAUDE_PLUGIN_CACHE_DIR = DEFAULT_CLAUDE_HOME / "plugins" / "cache"


def resolve_project_root(start_path: str | Path | None = None) -> ProjectContext:
    """Resolve a project root from cwd or an explicit path.

    Searches upward from the selected path until it finds ``AGENTS.md``.
    Falls back to ``CLAUDE.md`` as a project marker when ``AGENTS.md`` is
    absent, returning a context whose *agents_md_path* points to the
    not-yet-created ``AGENTS.md`` so the bootstrap step can create it.
    """
    candidate = Path(start_path or Path.cwd()).resolve()
    if candidate.is_file():
        candidate = candidate.parent

    for directory in (candidate, *candidate.parents):
        agents_md_path = directory / AGENTS_MD
        if agents_md_path.is_file():
            return ProjectContext(root=directory, agents_md_path=agents_md_path)
        # Fallback: accept CLAUDE.md as project marker for bootstrap
        # at the same directory level, so a nearer CLAUDE.md wins over
        # a farther AGENTS.md in an ancestor.
        claude_md_path = directory / "CLAUDE.md"
        if claude_md_path.is_file():
            return ProjectContext(root=directory, agents_md_path=directory / AGENTS_MD)

    raise DiscoveryError(
        f"Could not resolve a project root with {AGENTS_MD} from: {candidate}"
    )


def _resolve_cache_dir(
    cache_dir: str | Path | None = None,
    claude_home: str | Path | None = None,
) -> Path:
    """Resolve the plugin cache directory.

    An explicit *cache_dir* takes priority.  Otherwise the cache path is
    derived from *claude_home* (defaulting to ``DEFAULT_CLAUDE_HOME``).
    """
    if cache_dir is not None:
        return Path(cache_dir).expanduser().resolve()
    home = Path(claude_home or DEFAULT_CLAUDE_HOME).expanduser().resolve()
    return home / "plugins" / "cache"


def query_enabled_plugin_ids(
    project_root: Path,
) -> frozenset[str]:
    """Query the Claude CLI for enabled plugin IDs.

    Returns a frozenset of ``marketplace/plugin_name`` strings for
    plugins that are currently enabled. Uses *project_root* as the
    working directory so project-scoped enabledPlugins settings apply.

    Raises DiscoveryError if the ``claude`` CLI is not available.
    """
    claude_path = shutil.which("claude")
    if claude_path is None:
        raise DiscoveryError(
            "Claude CLI not found on PATH. "
            "The bridge requires Claude Code to be installed. "
            "See https://docs.anthropic.com/en/docs/claude-code"
        )

    try:
        proc = subprocess.run(
            [claude_path, "plugins", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_root),
        )
    except subprocess.TimeoutExpired:
        raise DiscoveryError("Claude CLI timed out after 30 seconds")
    except OSError as exc:
        raise DiscoveryError(f"Failed to run Claude CLI: {exc}")

    if proc.returncode != 0:
        raise DiscoveryError(
            f"Claude CLI failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )

    try:
        raw_plugins = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DiscoveryError(f"Claude CLI returned invalid JSON: {exc}")

    return _parse_enabled_plugin_ids(raw_plugins)


def _parse_enabled_plugin_ids(
    raw_plugins: list[dict],
) -> frozenset[str]:
    """Extract enabled plugin IDs from parsed CLI JSON.

    Returns IDs in ``marketplace/plugin_name`` format to match the
    bridge's internal convention.
    """
    enabled: set[str] = set()
    for entry in raw_plugins:
        if not entry.get("enabled", False):
            continue
        plugin_id = entry["id"]  # "plugin-name@marketplace"
        at_index = plugin_id.index("@")
        plugin_name = plugin_id[:at_index]
        marketplace = plugin_id[at_index + 1:]
        enabled.add(f"{marketplace}/{plugin_name}")
    return frozenset(enabled)


def discover(
    project_path: str | Path | None = None,
    cache_dir: str | Path | None = None,
    claude_home: str | Path | None = None,
) -> DiscoveryResult:
    """Resolve the target project and latest installed Claude plugins."""
    project = resolve_project_root(project_path)
    enabled_ids = query_enabled_plugin_ids(project.root)
    plugins = discover_latest_plugins(
        cache_dir=cache_dir,
        claude_home=claude_home,
        enabled_ids=enabled_ids,
    )
    return DiscoveryResult(
        project=project,
        plugins=plugins,
        user_skills=discover_user_skills(claude_home),
        user_agents=discover_user_agents(claude_home),
        user_commands=discover_user_commands(claude_home),
        project_skills=discover_project_skills(project.root),
        project_agents=discover_project_agents(project.root),
        project_commands=discover_project_commands(project.root),
        user_claude_md=discover_user_claude_md(claude_home),
    )


def discover_latest_plugins(
    cache_dir: str | Path | None = None,
    claude_home: str | Path | None = None,
    *,
    enabled_ids: frozenset[str] | None = None,
) -> tuple[InstalledPlugin, ...]:
    """Discover the latest installed version of each Claude plugin."""
    root = _resolve_cache_dir(cache_dir, claude_home)
    if not root.is_dir():
        return ()  # No cache directory — no plugins

    grouped_versions: dict[tuple[str, str], list[InstalledPluginVersion]] = defaultdict(list)

    for marketplace_dir in sorted(_iter_dirs(root)):
        for plugin_dir in sorted(_iter_dirs(marketplace_dir)):
            versions = list(_collect_plugin_versions(marketplace_dir.name, plugin_dir))
            if not versions:
                raise DiscoveryError(
                    "No valid semantic versions found for installed Claude plugin "
                    f"{marketplace_dir.name}/{plugin_dir.name}"
                )
            grouped_versions[(marketplace_dir.name, plugin_dir.name)].extend(versions)

    if not grouped_versions:
        return ()  # Empty cache — no plugins

    latest_plugins = []
    for key in sorted(grouped_versions):
        latest = sorted(grouped_versions[key], key=lambda item: item.version)[-1]
        latest_plugins.append(
            InstalledPlugin(
                marketplace=latest.marketplace,
                plugin_name=latest.plugin_name,
                version_text=latest.version_text,
                version=latest.version,
                installed_path=latest.installed_path,
                source_path=latest.resolved_path,
                skills=tuple(_discover_skills(latest.resolved_path)),
                agents=tuple(_discover_agents(latest.resolved_path)),
                commands=tuple(_discover_commands(latest.resolved_path)),
            )
        )

    if enabled_ids is not None:
        latest_plugins = [
            p for p in latest_plugins
            if f"{p.marketplace}/{p.plugin_name}" in enabled_ids
        ]

    return tuple(latest_plugins)


def discover_user_skills(claude_home: str | Path | None = None) -> tuple[Path, ...]:
    """Discover user-level skills from ~/.claude/skills/."""
    home = Path(claude_home or DEFAULT_CLAUDE_HOME).expanduser().resolve()
    skills_dir = home / "skills"
    if not skills_dir.is_dir():
        return ()
    return tuple(sorted(
        skill_dir for skill_dir in _iter_dirs(skills_dir)
        if (skill_dir / "SKILL.md").is_file()
    ))


def discover_user_agents(claude_home: str | Path | None = None) -> tuple[Path, ...]:
    """Discover user-level agents from ~/.claude/agents/."""
    home = Path(claude_home or DEFAULT_CLAUDE_HOME).expanduser().resolve()
    agents_dir = home / "agents"
    if not agents_dir.is_dir():
        return ()
    return tuple(sorted(
        path for path in agents_dir.iterdir()
        if path.is_file() and path.suffix == ".md"
    ))


def discover_project_skills(project_root: Path) -> tuple[Path, ...]:
    """Discover project-level skills from .claude/skills/."""
    skills_dir = project_root / ".claude" / "skills"
    if not skills_dir.is_dir():
        return ()
    return tuple(sorted(
        skill_dir for skill_dir in _iter_dirs(skills_dir)
        if (skill_dir / "SKILL.md").is_file()
    ))


def discover_project_agents(project_root: Path) -> tuple[Path, ...]:
    """Discover project-level agents from .claude/agents/."""
    agents_dir = project_root / ".claude" / "agents"
    if not agents_dir.is_dir():
        return ()
    return tuple(sorted(
        path for path in agents_dir.iterdir()
        if path.is_file() and path.suffix == ".md"
    ))


def discover_user_commands(claude_home: str | Path | None = None) -> tuple[Path, ...]:
    """Discover user-level commands from ~/.claude/commands/."""
    home = Path(claude_home or DEFAULT_CLAUDE_HOME).expanduser().resolve()
    commands_dir = home / "commands"
    if not commands_dir.is_dir():
        return ()
    return tuple(sorted(
        path for path in commands_dir.iterdir()
        if path.is_file() and path.suffix == ".md"
    ))


def discover_project_commands(project_root: Path) -> tuple[Path, ...]:
    """Discover project-level commands from .claude/commands/."""
    commands_dir = project_root / ".claude" / "commands"
    if not commands_dir.is_dir():
        return ()
    return tuple(sorted(
        path for path in commands_dir.iterdir()
        if path.is_file() and path.suffix == ".md"
    ))


def discover_user_claude_md(claude_home: str | Path | None = None) -> str | None:
    """Read user-level CLAUDE.md content if present."""
    home = Path(claude_home or DEFAULT_CLAUDE_HOME).expanduser().resolve()
    claude_md = home / "CLAUDE.md"
    if not claude_md.is_file():
        return None
    return read_utf8_text(claude_md, label="user-level CLAUDE.md", error_type=DiscoveryError)


def _collect_plugin_versions(
    marketplace: str,
    plugin_dir: Path,
) -> list[InstalledPluginVersion]:
    """Collect valid semantic-version installs for one plugin directory."""
    versions = []
    for version_dir in sorted(_iter_dirs(plugin_dir)):
        try:
            version = SemVer.parse(version_dir.name)
        except ValueError:
            continue

        versions.append(
            InstalledPluginVersion(
                marketplace=marketplace,
                plugin_name=plugin_dir.name,
                version_text=version_dir.name,
                version=version,
                installed_path=version_dir,
                resolved_path=version_dir.resolve(),
            )
        )
    return versions


def _discover_skills(plugin_path: Path) -> list[Path]:
    """Return skill directories that contain SKILL.md."""
    skills_dir = plugin_path / "skills"
    if not skills_dir.is_dir():
        return []

    return sorted(
        skill_dir for skill_dir in _iter_dirs(skills_dir) if (skill_dir / "SKILL.md").is_file()
    )


def _discover_agents(plugin_path: Path) -> list[Path]:
    """Return top-level agent markdown files for a plugin."""
    agents_dir = plugin_path / "agents"
    if not agents_dir.is_dir():
        return []

    return sorted(
        path for path in agents_dir.iterdir() if path.is_file() and path.suffix == ".md"
    )


def _discover_commands(plugin_path: Path) -> list[Path]:
    """Return top-level command markdown files for a plugin."""
    commands_dir = plugin_path / "commands"
    if not commands_dir.is_dir():
        return []

    return sorted(
        path for path in commands_dir.iterdir() if path.is_file() and path.suffix == ".md"
    )


def _iter_dirs(path: Path):
    """Yield direct child directories in deterministic order."""
    return (entry for entry in path.iterdir() if entry.is_dir())
