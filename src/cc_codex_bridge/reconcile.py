"""Desired-state reconcile engine for Codex bridge artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import shutil
from typing import Iterable
from uuid import uuid4

from cc_codex_bridge.model import (
    ClaudeShimDecision,
    DiscoveryResult,
    GeneratedSkill,
    ReconcileError,
)
from cc_codex_bridge.registry import (
    GLOBAL_REGISTRY_FILENAME,
    GlobalSkillEntry,
    GlobalSkillRegistry,
    hash_generated_skill,
)
from cc_codex_bridge.state import BridgeState
from cc_codex_bridge.text import read_utf8_text


DEFAULT_CODEX_HOME = Path.home() / ".codex"
STATE_RELATIVE_PATH = Path(".codex") / "claude-code-bridge-state.json"
CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
PROMPTS_RELATIVE_ROOT = Path(".codex") / "prompts" / "agents"

GLOBAL_INSTRUCTIONS_SENTINEL = "\n<!-- managed by cc-codex-bridge -->\n"


def _has_bridge_sentinel(content: bytes) -> bool:
    """Return True if content contains the bridge ownership sentinel."""
    return GLOBAL_INSTRUCTIONS_SENTINEL.encode() in content


@dataclass(frozen=True)
class DesiredState:
    """Full desired-state model for one project reconcile."""

    project_root: Path
    codex_home: Path
    project_files: tuple[tuple[Path, bytes], ...]
    preserved_project_files: tuple[Path, ...]
    skills: tuple[GeneratedSkill, ...]
    state_path: Path
    global_instructions: bytes | None = None
    project_skills: tuple[GeneratedSkill, ...] = ()


@dataclass(frozen=True)
class Change:
    """One file or directory level change."""

    kind: str
    path: Path
    resource_kind: str = ""


@dataclass(frozen=True)
class ReconcileReport:
    """Reconcile or dry-run result."""

    changes: tuple[Change, ...]
    applied: bool


@dataclass(frozen=True)
class LaunchAgentRemoval:
    """One LaunchAgent plist to remove."""

    path: Path
    bootout_command: str


@dataclass(frozen=True)
class UninstallProjectResult:
    """Result of cleaning one project during uninstall."""

    root: Path
    status: str  # "cleaned" | "skipped" | "no_state"
    changes: tuple[Change, ...]
    skip_reason: str = ""


@dataclass(frozen=True)
class UninstallReport:
    """Full uninstall result."""

    projects: tuple[UninstallProjectResult, ...]
    global_removals: tuple[Change, ...]
    launchagent_removals: tuple[LaunchAgentRemoval, ...]
    applied: bool


@dataclass(frozen=True)
class _RegistrySnapshot:
    """One loaded global-registry snapshot."""

    path: Path
    registry: GlobalSkillRegistry
    existed: bool


@dataclass(frozen=True)
class _RegistryWrite:
    """One staged global-registry write."""

    destination: Path
    content: bytes


@dataclass(frozen=True)
class _MutationPlan:
    """Planned file, skill, and registry mutations for one reconcile."""

    changes: tuple[Change, ...]
    registry_writes: tuple[_RegistryWrite, ...]


def build_desired_state(
    discovery: DiscoveryResult,
    shim_decision: ClaudeShimDecision,
    prompt_files: dict[Path, str],
    rendered_config: str,
    skills: Iterable[GeneratedSkill],
    *,
    codex_home: str | Path | None = None,
    extra_project_files: Iterable[tuple[Path, bytes]] | None = None,
    project_skills: Iterable[GeneratedSkill] | None = None,
) -> DesiredState:
    """Build the desired generated outputs for a project."""
    if shim_decision.action == "fail":
        raise ReconcileError(shim_decision.reason)

    project_root = discovery.project.root.resolve()
    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    project_files: list[tuple[Path, bytes]] = []
    preserved_project_files: list[Path] = []
    skills_tuple = tuple(skills)

    if shim_decision.content is not None:
        project_files.append(
            (
                _resolve_managed_project_path(project_root, Path("CLAUDE.md")),
                shim_decision.content.encode(),
            )
        )
    elif shim_decision.action == "preserve":
        preserved_project_files.append(
            _resolve_managed_project_path(project_root, Path("CLAUDE.md"))
        )

    project_files.append(
        (
            _resolve_managed_project_path(project_root, CONFIG_RELATIVE_PATH),
            rendered_config.encode(),
        )
    )
    for relpath, content in sorted(prompt_files.items(), key=lambda item: item[0].as_posix()):
        project_files.append(
            (
                _resolve_managed_project_path(project_root, relpath),
                content.encode(),
            )
        )

    if extra_project_files:
        for relpath, content in sorted(extra_project_files, key=lambda item: item[0].as_posix()):
            project_files.append(
                (
                    _resolve_managed_project_path(project_root, relpath),
                    content,
                )
            )

    global_instructions = None
    if discovery.user_claude_md is not None:
        global_instructions = (
            discovery.user_claude_md + GLOBAL_INSTRUCTIONS_SENTINEL
        ).encode()

    return DesiredState(
        project_root=project_root,
        codex_home=codex_home_path,
        project_files=tuple(project_files),
        preserved_project_files=tuple(sorted(set(preserved_project_files), key=str)),
        skills=skills_tuple,
        state_path=project_root / STATE_RELATIVE_PATH,
        global_instructions=global_instructions,
        project_skills=tuple(project_skills or ()),
    )


@dataclass(frozen=True)
class ProjectBuildResult:
    """Result of running the full project build pipeline."""

    desired_state: DesiredState | None
    discovery: DiscoveryResult
    shim_decision: ClaudeShimDecision
    role_count: int
    prompt_count: int
    skill_count: int
    exclusion_report: object  # ExclusionReport from exclusions module
    rendered_config: str
    diagnostics: tuple  # AgentTranslationDiagnostic tuple


def build_project_desired_state(
    project_root: str | Path | None = None,
    *,
    codex_home: str | Path | None = None,
    claude_home: str | Path | None = None,
    cache_dir: str | Path | None = None,
    exclude_plugins: Iterable[str] = (),
    exclude_skills: Iterable[str] = (),
    exclude_agents: Iterable[str] = (),
) -> ProjectBuildResult:
    """Run the full discover-translate-build pipeline for one project.

    Returns a ProjectBuildResult containing the desired state and all
    intermediate values both callers (CLI and reconcile_all) need.
    """
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.discover import discover
    from cc_codex_bridge.exclusions import (
        apply_sync_exclusions,
        load_project_exclusions,
        resolve_effective_exclusions,
    )
    from cc_codex_bridge.render_codex_config import render_inline_codex_config, render_prompt_files
    from cc_codex_bridge.translate_agents import (
        translate_installed_agents_with_diagnostics,
        translate_standalone_agents,
    )
    from cc_codex_bridge.translate_skills import translate_installed_skills, translate_standalone_skills

    result = discover(
        project_path=project_root,
        cache_dir=cache_dir,
        claude_home=claude_home,
    )
    config_exclusions = load_project_exclusions(result.project.root)
    exclusions = resolve_effective_exclusions(
        config_exclusions,
        cli_exclude_plugins=tuple(exclude_plugins) or None,
        cli_exclude_skills=tuple(exclude_skills) or None,
        cli_exclude_agents=tuple(exclude_agents) or None,
    )
    result, exclusion_report = apply_sync_exclusions(result, exclusions)
    shim_decision = plan_claude_shim(result.project)

    agent_result = translate_installed_agents_with_diagnostics(result.plugins)
    user_agent_result = translate_standalone_agents(result.user_agents, scope="user")
    project_agent_result = translate_standalone_agents(result.project_agents, scope="project")

    all_diagnostics = (
        *agent_result.diagnostics,
        *user_agent_result.diagnostics,
        *project_agent_result.diagnostics,
    )

    # Short-circuit before skill translation when agent diagnostics exist.
    # Skill translation may raise on unrelated errors (e.g. missing sibling
    # references), masking the diagnostics that callers need to inspect.
    if all_diagnostics:
        return ProjectBuildResult(
            desired_state=None,
            discovery=result,
            shim_decision=shim_decision,
            role_count=0,
            prompt_count=0,
            skill_count=0,
            exclusion_report=exclusion_report,
            rendered_config="",
            diagnostics=tuple(all_diagnostics),
        )

    all_roles = (*agent_result.roles, *user_agent_result.roles, *project_agent_result.roles)
    plugin_skills = translate_installed_skills(result.plugins)
    user_skills = translate_standalone_skills(result.user_skills, scope="user")
    all_global_skills = (*plugin_skills, *user_skills)
    project_skills = translate_standalone_skills(result.project_skills, scope="project")

    prompt_files = render_prompt_files(all_roles)
    rendered_config = render_inline_codex_config(all_roles)
    total_skill_count = len(all_global_skills) + len(project_skills)
    desired_state = build_desired_state(
        result, shim_decision, prompt_files, rendered_config,
        all_global_skills, codex_home=codex_home,
        project_skills=project_skills,
    )

    return ProjectBuildResult(
        desired_state=desired_state,
        discovery=result,
        shim_decision=shim_decision,
        role_count=len(all_roles),
        prompt_count=len(prompt_files),
        skill_count=total_skill_count,
        exclusion_report=exclusion_report,
        rendered_config=rendered_config,
        diagnostics=tuple(all_diagnostics),
    )


def diff_desired_state(desired: DesiredState) -> ReconcileReport:
    """Compare current outputs to desired state without writing."""
    previous_state = _load_previous_state(desired)
    plan = _plan_mutations(desired, previous_state)
    return ReconcileReport(changes=plan.changes, applied=False)


def reconcile_desired_state(desired: DesiredState) -> ReconcileReport:
    """Apply the desired state to disk."""
    previous_state = _load_previous_state(desired)
    plan = _plan_mutations(desired, previous_state)
    if not plan.changes and not plan.registry_writes and not _state_write_needed(desired):
        return ReconcileReport(changes=(), applied=True)

    _apply_changes(desired, plan)
    return ReconcileReport(changes=plan.changes, applied=True)


def clean_project(
    project_root: str | Path,
    *,
    codex_home: str | Path | None = None,
    dry_run: bool = False,
) -> ReconcileReport:
    """Remove all bridge-generated artifacts from one project.

    Loads the existing bridge state to determine what is managed, releases
    global skill registry claims, and deletes managed project files plus the
    state file.  Returns a report of what was (or would be) removed.
    """
    project_root_path = Path(project_root).expanduser().resolve()
    state_path = project_root_path / STATE_RELATIVE_PATH

    if state_path.is_symlink():
        raise ReconcileError(f"Refusing to use symlinked bridge state file: {state_path}")
    # Verify state file resolves within the project root (catches symlinked ancestors)
    _assert_path_contained(state_path, project_root_path, label="Bridge state file")
    previous_state = BridgeState.from_path(state_path)
    if previous_state is None:
        return ReconcileReport(changes=(), applied=True)

    if previous_state.project_root != project_root_path:
        raise ReconcileError(
            "Bridge state belongs to a different project root: "
            f"{previous_state.project_root}"
        )

    # Use the state-recorded codex_home — it is authoritative for where this
    # project's generated outputs live.
    codex_home_path = previous_state.codex_home

    changes: list[Change] = []

    # Remove managed project skill directories
    skill_dirs_to_remove: list[Path] = []
    for skill_dir_name in sorted(previous_state.managed_project_skill_dirs):
        skill_dir = project_root_path / SKILLS_RELATIVE_ROOT / skill_dir_name
        if skill_dir.exists() and not skill_dir.is_symlink():
            skill_dirs_to_remove.append(skill_dir)
            changes.append(Change("remove", skill_dir, resource_kind="project_skill"))

    # Remove managed project files (excluding those inside skill dirs)
    for relative in sorted(previous_state.managed_project_files):
        path = project_root_path / relative
        if any(path == sd or _is_under(path, sd) for sd in skill_dirs_to_remove):
            continue
        if path.exists() and not path.is_symlink():
            changes.append(Change("remove", path))

    # Release skill ownership claims from the global registry
    registry_path = codex_home_path / GLOBAL_REGISTRY_FILENAME
    if registry_path.is_symlink():
        raise ReconcileError(
            f"Refusing to use symlinked global skill registry file: {registry_path}"
        )
    registry = GlobalSkillRegistry.from_path(registry_path)

    registry_changed = False
    if registry is not None:
        updated_skills = dict(registry.skills)
        for install_dir_name in sorted(registry.skills):
            entry = registry.skills[install_dir_name]
            if project_root_path not in entry.owners:
                continue
            remaining_owners = tuple(
                owner for owner in entry.owners if owner != project_root_path
            )
            if remaining_owners:
                updated_skills[install_dir_name] = GlobalSkillEntry(
                    content_hash=entry.content_hash,
                    owners=remaining_owners,
                )
            else:
                del updated_skills[install_dir_name]
                skill_path = codex_home_path / "skills" / install_dir_name
                if skill_path.exists():
                    changes.append(Change("remove", skill_path, resource_kind="skill"))
            registry_changed = True

        # Remove project from the projects list
        updated_projects = tuple(
            p for p in registry.projects if p != project_root_path
        )
        if updated_projects != registry.projects:
            registry_changed = True

        if registry_changed:
            updated_registry = GlobalSkillRegistry(
                skills=updated_skills,
                projects=updated_projects,
            )
    else:
        updated_registry = None

    # Verify project-local clean targets resolve within project_root
    for change in changes:
        if change.path == state_path:
            continue
        if change.resource_kind == "skill":
            # Global skills live under codex_home, not project_root
            _assert_path_contained(change.path, codex_home_path, label="Clean target")
        else:
            _assert_path_contained(change.path, project_root_path, label="Clean target")

    if dry_run:
        return ReconcileReport(changes=tuple(changes), applied=False)

    # Apply removals — state file last to preserve cleanup atomicity
    for change in changes:
        if change.path == state_path:
            continue  # deferred to end
        if change.resource_kind in ("skill", "project_skill"):
            if change.path.exists():
                shutil.rmtree(change.path)
        else:
            change.path.unlink(missing_ok=True)
            _cleanup_empty_parents(change.path.parent, project_root_path / ".codex")

    # Update the registry
    if registry_changed and updated_registry is not None:
        _atomic_write_file(registry_path, updated_registry.to_json().encode())

    # Remove the state file last
    state_path.unlink(missing_ok=True)

    return ReconcileReport(changes=tuple(changes), applied=True)


@dataclass(frozen=True)
class ReconcileAllProjectResult:
    """Result of reconciling one project in a reconcile-all run."""

    project_root: Path
    report: ReconcileReport


@dataclass(frozen=True)
class ReconcileAllError:
    """One project that failed during reconcile-all."""

    project_root: Path
    error: str


@dataclass(frozen=True)
class ReconcileAllReport:
    """Full reconcile-all result."""

    results: tuple[ReconcileAllProjectResult, ...]
    errors: tuple[ReconcileAllError, ...]


def reconcile_all(
    *,
    codex_home: str | Path | None = None,
    dry_run: bool = False,
) -> ReconcileAllReport:
    """Reconcile all registered projects."""
    from cc_codex_bridge.translate_agents import format_agent_translation_diagnostics

    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    registry_path = codex_home_path / GLOBAL_REGISTRY_FILENAME

    registry = None
    if registry_path.exists() and not registry_path.is_symlink():
        registry = GlobalSkillRegistry.from_path(registry_path)

    project_roots = list(registry.projects) if registry else []

    results: list[ReconcileAllProjectResult] = []
    errors: list[ReconcileAllError] = []

    for project_root in sorted(project_roots, key=str):
        if not project_root.is_dir():
            errors.append(ReconcileAllError(project_root=project_root, error="directory not found"))
            continue
        if not (project_root / "AGENTS.md").is_file():
            errors.append(ReconcileAllError(project_root=project_root, error="AGENTS.md not found"))
            continue

        try:
            build = build_project_desired_state(
                project_root,
                codex_home=codex_home_path,
            )
            if build.diagnostics:
                errors.append(ReconcileAllError(
                    project_root=project_root,
                    error=format_agent_translation_diagnostics(build.diagnostics),
                ))
                continue

            if dry_run:
                report = diff_desired_state(build.desired_state)
            else:
                report = reconcile_desired_state(build.desired_state)

            results.append(ReconcileAllProjectResult(project_root=project_root, report=report))
        except Exception as exc:
            errors.append(ReconcileAllError(project_root=project_root, error=str(exc)))

    return ReconcileAllReport(results=tuple(results), errors=tuple(errors))


def uninstall_all(
    *,
    codex_home: str | Path | None = None,
    launchagents_dir: str | Path | None = None,
    dry_run: bool = False,
) -> UninstallReport:
    """Remove all bridge-generated artifacts from the machine.

    Discovers projects from the global skill registry, cleans each accessible
    one, then removes global artifacts and LaunchAgent plists.
    """
    from cc_codex_bridge.install_launchagent import find_bridge_launchagents

    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    registry_path = codex_home_path / GLOBAL_REGISTRY_FILENAME

    # Step 1: Discover project roots from the registry
    project_roots: set[Path] = set()
    if registry_path.exists() and not registry_path.is_symlink():
        registry = GlobalSkillRegistry.from_path(registry_path)
        if registry is not None:
            project_roots.update(registry.projects)
            # Also include skill owners for backwards compatibility with
            # registries that predate the projects list
            for entry in registry.skills.values():
                project_roots.update(entry.owners)

    # Step 2: Clean each discovered project
    project_results: list[UninstallProjectResult] = []
    for root in sorted(project_roots, key=str):
        if not root.is_dir():
            project_results.append(UninstallProjectResult(
                root=root,
                status="skipped",
                changes=(),
                skip_reason="directory not found",
            ))
            continue

        state_path = root / STATE_RELATIVE_PATH
        if not state_path.exists():
            project_results.append(UninstallProjectResult(
                root=root,
                status="no_state",
                changes=(),
            ))
            continue

        try:
            report = clean_project(root, codex_home=codex_home_path, dry_run=dry_run)
            project_results.append(UninstallProjectResult(
                root=root,
                status="cleaned",
                changes=report.changes,
            ))
        except (ReconcileError, OSError, UnicodeError) as exc:
            project_results.append(UninstallProjectResult(
                root=root,
                status="skipped",
                changes=(),
                skip_reason=str(exc),
            ))

    # Step 3: Remove remaining global artifacts
    global_removals: list[Change] = []

    # Force-remove any remaining skill directories still in the registry
    # (handles skills owned by skipped projects)
    if registry_path.exists() and not registry_path.is_symlink():
        registry = GlobalSkillRegistry.from_path(registry_path)
        if registry is not None:
            for install_dir_name in sorted(registry.skills):
                skill_path = codex_home_path / "skills" / install_dir_name
                if skill_path.exists():
                    global_removals.append(
                        Change("remove", skill_path, resource_kind="skill")
                    )

    # Remove global AGENTS.md only if bridge-generated (sentinel present)
    global_agents_md = codex_home_path / "AGENTS.md"
    if (
        global_agents_md.exists()
        and not global_agents_md.is_symlink()
        and _has_bridge_sentinel(global_agents_md.read_bytes())
    ):
        global_removals.append(
            Change("remove", global_agents_md, resource_kind="global_instructions")
        )

    # Remove the registry file itself
    if registry_path.exists():
        global_removals.append(Change("remove", registry_path))

    # Step 4: Discover LaunchAgent plists
    bridge_plists = find_bridge_launchagents(launchagents_dir=launchagents_dir)
    launchagent_removals = tuple(
        LaunchAgentRemoval(
            path=plist_path,
            bootout_command=f"launchctl bootout gui/$(id -u) {plist_path}",
        )
        for plist_path in bridge_plists
    )

    if not dry_run:
        # Apply global removals
        for change in global_removals:
            if change.resource_kind == "skill":
                if change.path.exists():
                    shutil.rmtree(change.path)
            else:
                change.path.unlink(missing_ok=True)

        # Remove LaunchAgent plists
        for removal in launchagent_removals:
            removal.path.unlink(missing_ok=True)

    return UninstallReport(
        projects=tuple(project_results),
        global_removals=tuple(global_removals),
        launchagent_removals=launchagent_removals,
        applied=not dry_run,
    )


def _apply_changes(desired: DesiredState, plan: _MutationPlan) -> None:
    """Write all planned changes to disk."""
    desired_map = dict(desired.project_files)
    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}
    project_skills_by_name = {skill.install_dir_name: skill for skill in desired.project_skills}

    for change in plan.changes:
        if change.resource_kind == "global_instructions":
            if change.kind in ("create", "update"):
                _atomic_write_file(change.path, desired.global_instructions, container=desired.codex_home)
            elif change.kind == "remove":
                change.path.unlink(missing_ok=True)
            continue
        if change.resource_kind == "skill":
            if change.kind in ("create", "update"):
                if change.path.exists():
                    shutil.rmtree(change.path)
                change.path.mkdir(parents=True, exist_ok=True)
                _write_skill_tree(change.path, skills_by_name[change.path.name], container=desired.codex_home)
            elif change.kind == "remove":
                if change.path.exists():
                    shutil.rmtree(change.path)
            continue
        if change.resource_kind == "project_skill":
            if change.kind in ("create", "update"):
                if change.path.exists():
                    shutil.rmtree(change.path)
                change.path.mkdir(parents=True, exist_ok=True)
                _write_skill_tree(change.path, project_skills_by_name[change.path.name], container=desired.project_root)
            elif change.kind == "remove":
                if change.path.exists():
                    shutil.rmtree(change.path)
            continue
        else:
            if change.kind in ("create", "update"):
                _atomic_write_file(change.path, desired_map[change.path], container=desired.project_root)
            elif change.kind == "remove":
                change.path.unlink(missing_ok=True)
                _cleanup_empty_parents(change.path.parent, desired.project_root / ".codex")

    for registry_write in plan.registry_writes:
        _atomic_write_file(registry_write.destination, registry_write.content)

    state_bytes = _build_state_record(desired).to_json().encode()
    _atomic_write_file(desired.state_path, state_bytes, container=desired.project_root)


def _assert_path_contained(path: Path, root: Path, *, label: str) -> None:
    """Assert that the resolved path stays within the resolved root.

    Catches symlinked intermediate directories (e.g. .codex -> /tmp/outside)
    that would cause reads or writes to escape the expected directory tree.
    """
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise ReconcileError(
            f"{label} resolves outside expected root: {resolved} is not under {root_resolved}"
        )


def _atomic_write_file(path: Path, content: bytes, *, container: Path | None = None) -> None:
    """Write a file atomically via temp-file-then-rename."""
    if container is not None:
        _assert_path_contained(path, container, label="Write target")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".bridge-{uuid4().hex}"
    try:
        tmp.write_bytes(content)
        tmp.rename(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def format_change_report(report: ReconcileReport) -> str:
    """Format a file-level summary of changes."""
    if not report.changes:
        return "No changes."
    return "\n".join(
        f"{change.kind.upper()}: {change.path}{f' ({change.resource_kind})' if change.resource_kind else ''}"
        for change in report.changes
    )


def format_diff_report(desired: DesiredState, report: ReconcileReport) -> str:
    """Format diff output, including unified diffs for text files where useful."""
    if not report.changes:
        return "No changes."

    lines = [format_change_report(report)]
    desired_map = dict(desired.project_files)
    for change in report.changes:
        if change.kind not in {"create", "update"}:
            continue
        if change.resource_kind == "skill":
            continue
        if change.path.suffix not in {".md", ".toml", ".json"}:
            continue
        existing_text = (
            read_utf8_text(change.path, label="managed text file", error_type=ReconcileError)
            if change.path.exists()
            else ""
        )
        if change.resource_kind == "global_instructions":
            desired_content = desired.global_instructions
        else:
            desired_content = desired_map.get(change.path)
        if desired_content is None:
            continue
        desired_text = desired_content.decode()
        diff = difflib.unified_diff(
            existing_text.splitlines(),
            desired_text.splitlines(),
            fromfile=str(change.path),
            tofile=str(change.path),
            lineterm="",
        )
        diff_lines = list(diff)
        if diff_lines:
            lines.append("")
            lines.extend(diff_lines)
    return "\n".join(lines)


def _plan_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
) -> _MutationPlan:
    """Plan file, skill, and registry mutations for one reconcile run."""
    project_changes = _compute_project_file_changes(desired, previous_state)
    project_skill_changes = _plan_project_skill_mutations(desired, previous_state)
    skill_changes, registry_writes = _plan_skill_mutations(desired, previous_state)
    global_changes = _plan_global_instructions_changes(desired)
    return _MutationPlan(
        changes=tuple((*project_changes, *project_skill_changes, *skill_changes, *global_changes)),
        registry_writes=registry_writes,
    )


def _plan_project_skill_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
) -> tuple[Change, ...]:
    """Plan project-local skill directory mutations using directory-snapshot comparison."""
    desired_skills = {skill.install_dir_name: skill for skill in desired.project_skills}
    previously_managed = set(previous_state.managed_project_skill_dirs) if previous_state else set()
    changes: list[Change] = []

    for install_dir_name in sorted(desired_skills):
        skill = desired_skills[install_dir_name]
        destination = desired.project_root / SKILLS_RELATIVE_ROOT / install_dir_name

        if destination.exists() and not destination.is_dir():
            raise ReconcileError(
                f"Expected a project skill directory but found a file: {destination}"
            )

        if not destination.exists():
            changes.append(Change("create", destination, resource_kind="project_skill"))
            continue

        if destination.is_symlink():
            raise ReconcileError(
                f"Refusing to overwrite symlinked project skill directory: {destination}"
            )

        if _directory_matches_skill(destination, skill):
            continue

        # Directory exists but doesn't match — only update if we own it
        if install_dir_name not in previously_managed:
            raise ReconcileError(
                f"Refusing to overwrite non-generated project skill directory: {destination}"
            )
        changes.append(Change("update", destination, resource_kind="project_skill"))

    # Detect stale project skill directories
    for install_dir_name in sorted(previously_managed - set(desired_skills)):
        stale_path = desired.project_root / SKILLS_RELATIVE_ROOT / install_dir_name
        if stale_path.exists() and not stale_path.is_symlink():
            changes.append(Change("remove", stale_path, resource_kind="project_skill"))

    return tuple(changes)


def _compute_project_file_changes(
    desired: DesiredState,
    previous_state: BridgeState | None,
) -> tuple[Change, ...]:
    """Compute project-local file changes, enforcing ownership safety."""
    if previous_state is not None and previous_state.project_root != desired.project_root:
        raise ReconcileError(
            "Interop state belongs to a different project root: "
            f"{previous_state.project_root}"
        )

    project_file_map = dict(desired.project_files)
    managed_project_files = set(previous_state.managed_project_files) if previous_state else set()
    invalid_managed_paths = sorted(
        relative for relative in managed_project_files if not _is_allowed_managed_project_relative(relative)
    )
    if invalid_managed_paths:
        raise ReconcileError(
            "Interop state contains unexpected managed project files: "
            + ", ".join(invalid_managed_paths)
        )
    changes: list[Change] = []

    for path, content in sorted(project_file_map.items(), key=lambda item: str(item[0])):
        relative = _project_relative(desired, path)
        owned = relative in managed_project_files
        if not path.exists():
            changes.append(Change("create", path))
            continue
        if path.is_symlink():
            raise ReconcileError(f"Refusing to overwrite symlinked project file: {path}")
        existing = path.read_bytes()
        if existing == content:
            continue
        if not owned:
            raise ReconcileError(f"Refusing to overwrite non-generated project file: {path}")
        changes.append(Change("update", path))

    desired_project_paths = {
        *(_project_relative(desired, path) for path, _ in desired.project_files),
        *(_project_relative(desired, path) for path in desired.preserved_project_files),
    }

    for relative in sorted(managed_project_files - desired_project_paths):
        if relative == STATE_RELATIVE_PATH.as_posix():
            continue
        path = desired.project_root / relative
        if path.exists():
            changes.append(Change("remove", path))

    return tuple(changes)


def _plan_skill_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
) -> tuple[tuple[Change, ...], tuple[_RegistryWrite, ...]]:
    """Plan global-skill ownership and directory mutations."""
    current_snapshot = _load_registry_snapshot(desired.codex_home / GLOBAL_REGISTRY_FILENAME)
    previous_snapshot = current_snapshot
    if previous_state is not None and previous_state.codex_home != desired.codex_home:
        previous_snapshot = _load_registry_snapshot(
            previous_state.codex_home / GLOBAL_REGISTRY_FILENAME
        )

    desired_skills = {skill.install_dir_name: skill for skill in desired.skills}
    desired_hashes = {
        install_dir_name: hash_generated_skill(skill)
        for install_dir_name, skill in desired_skills.items()
    }
    changes: list[Change] = []

    updated_current = GlobalSkillRegistry(
        skills=dict(current_snapshot.registry.skills),
        projects=_ensure_project_in_list(
            current_snapshot.registry.projects, desired.project_root
        ),
    )
    for install_dir_name in sorted(desired_skills):
        skill = desired_skills[install_dir_name]
        destination = desired.codex_home / "skills" / install_dir_name
        existing_entry = updated_current.skills.get(install_dir_name)
        desired_hash = desired_hashes[install_dir_name]
        registry_owned = existing_entry is not None

        if existing_entry is not None and existing_entry.content_hash != desired_hash:
            if set(existing_entry.owners) != {desired.project_root}:
                raise ReconcileError(
                    "Generated skill registry conflict for "
                    f"{destination}: existing content hash {existing_entry.content_hash} "
                    f"does not match desired {desired_hash}"
                )
            registry_owned = True

        if destination.exists() and not destination.is_dir():
            raise ReconcileError(f"Expected a skill directory but found a file: {destination}")

        if not registry_owned:
            if destination.exists() and not _directory_matches_skill(destination, skill):
                raise ReconcileError(
                    "Refusing to adopt conflicting existing skill directory: "
                    f"{destination}"
                )
            existing_owners: tuple[Path, ...] = ()
        else:
            existing_owners = existing_entry.owners if existing_entry is not None else ()

        updated_current.skills[install_dir_name] = GlobalSkillEntry(
            content_hash=desired_hash,
            owners=_sorted_owner_set((*existing_owners, desired.project_root)),
        )

        if not destination.exists():
            changes.append(Change("create", destination, resource_kind="skill"))
            continue
        if _directory_matches_skill(destination, skill):
            continue
        changes.append(Change("update", destination, resource_kind="skill"))

    previously_owned_current = _owned_skill_names(
        current_snapshot.registry,
        desired.project_root,
    )
    for install_dir_name in sorted(previously_owned_current - set(desired_skills)):
        entry = updated_current.skills[install_dir_name]
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != desired.project_root
        )
        if remaining_owners:
            updated_current.skills[install_dir_name] = GlobalSkillEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
            continue

        del updated_current.skills[install_dir_name]
        stale_path = desired.codex_home / "skills" / install_dir_name
        if stale_path.exists():
            changes.append(Change("remove", stale_path, resource_kind="skill"))

    registry_writes: list[_RegistryWrite] = []
    current_write = _build_registry_write(current_snapshot, updated_current)
    if current_write is not None:
        registry_writes.append(current_write)

    if previous_snapshot.path != current_snapshot.path:
        updated_previous = GlobalSkillRegistry(
            skills=dict(previous_snapshot.registry.skills),
            projects=tuple(
                p for p in previous_snapshot.registry.projects
                if p != desired.project_root
            ),
        )
        for install_dir_name in sorted(_owned_skill_names(previous_snapshot.registry, desired.project_root)):
            entry = updated_previous.skills[install_dir_name]
            remaining_owners = tuple(
                owner for owner in entry.owners if owner != desired.project_root
            )
            if remaining_owners:
                updated_previous.skills[install_dir_name] = GlobalSkillEntry(
                    content_hash=entry.content_hash,
                    owners=remaining_owners,
                )
                continue

            del updated_previous.skills[install_dir_name]
            stale_path = previous_snapshot.path.parent / "skills" / install_dir_name
            if stale_path.exists():
                changes.append(Change("remove", stale_path, resource_kind="skill"))

        previous_write = _build_registry_write(previous_snapshot, updated_previous)
        if previous_write is not None:
            registry_writes.append(previous_write)

    return tuple(changes), tuple(registry_writes)


def _plan_global_instructions_changes(desired: DesiredState) -> tuple[Change, ...]:
    """Plan changes for the global instructions file (~/.codex/AGENTS.md)."""
    path = desired.codex_home / "AGENTS.md"

    if desired.global_instructions is None:
        # Source is absent — only remove if we created it (sentinel present)
        if path.exists() and not path.is_symlink() and _has_bridge_sentinel(path.read_bytes()):
            return (Change("remove", path, resource_kind="global_instructions"),)
        return ()

    if not path.exists():
        return (Change("create", path, resource_kind="global_instructions"),)
    if path.is_symlink():
        raise ReconcileError(f"Refusing to overwrite symlinked global instructions: {path}")
    existing = path.read_bytes()
    if existing == desired.global_instructions:
        return ()
    if not _has_bridge_sentinel(existing):
        raise ReconcileError(
            f"Refusing to overwrite hand-authored global instructions: {path}"
        )
    return (Change("update", path, resource_kind="global_instructions"),)


def _build_registry_write(
    snapshot: _RegistrySnapshot,
    updated_registry: GlobalSkillRegistry,
) -> _RegistryWrite | None:
    """Return one registry write when the desired registry differs from disk."""
    if updated_registry == snapshot.registry:
        return None
    return _RegistryWrite(
        destination=snapshot.path,
        content=updated_registry.to_json().encode(),
    )


def _owned_skill_names(registry: GlobalSkillRegistry, project_root: Path) -> set[str]:
    """Return the generated skill names currently claimed by one project."""
    return {
        install_dir_name
        for install_dir_name, entry in registry.skills.items()
        if project_root in entry.owners
    }


def _sorted_owner_set(owners: Iterable[Path]) -> tuple[Path, ...]:
    """Return unique owners in deterministic order."""
    return tuple(sorted(set(owners), key=str))


def _ensure_project_in_list(
    projects: tuple[Path, ...], project_root: Path
) -> tuple[Path, ...]:
    """Return projects tuple with project_root included, sorted."""
    if project_root in projects:
        return projects
    return tuple(sorted((*projects, project_root), key=str))


def _write_skill_tree(destination: Path, skill: GeneratedSkill, *, container: Path | None = None) -> None:
    """Write one staged skill directory tree."""
    if container is not None:
        _assert_path_contained(destination, container, label="Skill directory")
    for generated_file in skill.files:
        file_path = destination / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(generated_file.content)
        file_path.chmod(generated_file.mode)


def _directory_matches_skill(path: Path, skill: GeneratedSkill) -> bool:
    """Check whether an installed skill directory matches the desired tree exactly."""
    expected_files = {generated_file.relative_path: generated_file for generated_file in skill.files}
    actual_files = {
        item.relative_to(path): item
        for item in path.rglob("*")
        if item.is_file()
    }
    if set(actual_files) != set(expected_files):
        return False

    for relative_path, actual_path in actual_files.items():
        expected = expected_files[relative_path]
        if actual_path.read_bytes() != expected.content:
            return False
        if (actual_path.stat().st_mode & 0o777) != expected.mode:
            return False

    return True


def _load_previous_state(desired: DesiredState) -> BridgeState | None:
    """Load state only from a regular file at the expected project path."""
    if desired.state_path.is_symlink():
        raise ReconcileError(f"Refusing to use symlinked bridge state file: {desired.state_path}")
    return BridgeState.from_path(desired.state_path)


def _load_registry_snapshot(path: Path) -> _RegistrySnapshot:
    """Load one global registry only from a regular file at the expected path."""
    if path.is_symlink():
        raise ReconcileError(f"Refusing to use symlinked global skill registry file: {path}")
    registry = GlobalSkillRegistry.from_path(path)
    return _RegistrySnapshot(
        path=path,
        registry=registry or GlobalSkillRegistry(skills={}),
        existed=registry is not None,
    )


def _project_relative(desired: DesiredState, path: Path) -> str:
    """Return a project-relative path string."""
    return _normalize_relative_path(
        path.relative_to(desired.project_root),
        label="managed project path",
    ).as_posix()


def _is_under(path: Path, parent: Path) -> bool:
    """Return True if path is strictly under parent."""
    try:
        path.relative_to(parent)
        return path != parent
    except ValueError:
        return False


def _cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    """Remove empty directories up to stop_at."""
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _build_state_record(desired: DesiredState) -> BridgeState:
    """Build the desired stable state payload."""
    managed_project_paths = {
        *(_project_relative(desired, path) for path, _ in desired.project_files),
        *(_project_relative(desired, path) for path in desired.preserved_project_files),
        STATE_RELATIVE_PATH.as_posix(),
    }
    managed_project_files = tuple(sorted(managed_project_paths))
    managed_project_skill_dirs = tuple(
        sorted(skill.install_dir_name for skill in desired.project_skills)
    )
    return BridgeState(
        project_root=desired.project_root,
        codex_home=desired.codex_home,
        managed_project_files=managed_project_files,
        managed_project_skill_dirs=managed_project_skill_dirs,
    )


def _state_write_needed(desired: DesiredState) -> bool:
    """Return True when the project-local state file must be updated."""
    state_bytes = _build_state_record(desired).to_json().encode()
    return not desired.state_path.exists() or desired.state_path.read_bytes() != state_bytes


SKILLS_RELATIVE_ROOT = Path(".codex") / "skills"


def _is_allowed_managed_project_relative(relative: str) -> bool:
    """Return True for the only project-relative paths the generator may ever manage."""
    try:
        normalized = _normalize_relative_path(Path(relative), label="managed project path")
    except ReconcileError:
        return False

    allowed_exact = {
        "CLAUDE.md",
        CONFIG_RELATIVE_PATH.as_posix(),
        STATE_RELATIVE_PATH.as_posix(),
    }
    normalized_text = normalized.as_posix()
    if normalized_text in allowed_exact:
        return True

    if normalized.parent == PROMPTS_RELATIVE_ROOT and normalized.suffix == ".md":
        return True

    return False


def _resolve_managed_project_path(project_root: Path, relative_path: Path) -> Path:
    """Resolve and validate one generated project-relative output path."""
    normalized = _normalize_relative_path(relative_path, label="managed project output")
    return project_root / normalized


def _normalize_relative_path(path: Path, *, label: str) -> Path:
    """Normalize one relative path and reject traversal or absolute forms."""
    candidate = Path(path)
    if candidate.is_absolute():
        raise ReconcileError(f"{label.capitalize()} must be relative: {candidate}")

    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise ReconcileError(f"{label.capitalize()} may not contain parent traversal: {candidate}")
        normalized_parts.append(part)

    if not normalized_parts:
        raise ReconcileError(f"{label.capitalize()} may not be empty: {candidate}")
    return Path(*normalized_parts)


