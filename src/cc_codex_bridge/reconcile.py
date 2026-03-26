"""Desired-state reconcile engine for Codex bridge artifacts."""

from __future__ import annotations

from dataclasses import dataclass, replace
import difflib
from pathlib import Path
import shutil
from typing import Iterable
from uuid import uuid4

from cc_codex_bridge.bridge_home import resolve_bridge_home, project_state_dir
from cc_codex_bridge.scan import ScanResult, scan_for_projects, seed_config_stub
from cc_codex_bridge.model import (
    ClaudeShimDecision,
    DiscoveryResult,
    GeneratedAgentFile,
    GeneratedPrompt,
    GeneratedSkill,
    ReconcileError,
    VendoredPluginResource,
)
from cc_codex_bridge.registry import (
    GLOBAL_REGISTRY_FILENAME,
    GlobalAgentEntry,
    GlobalPluginResourceEntry,
    GlobalPromptEntry,
    GlobalSkillEntry,
    GlobalSkillRegistry,
    hash_agent_file,
    hash_file_content,
    hash_generated_skill,
    hash_generated_skill_files,
    hash_prompt_content,
)
from cc_codex_bridge.state import BridgeState
from cc_codex_bridge.text import read_utf8_text


DEFAULT_CODEX_HOME = Path.home() / ".codex"
AGENTS_RELATIVE_ROOT = Path(".codex") / "agents"

GLOBAL_INSTRUCTIONS_SENTINEL = "\n<!-- managed by cc-codex-bridge -->\n"


def _has_bridge_sentinel(content: bytes) -> bool:
    """Return True if content contains the bridge ownership sentinel."""
    return GLOBAL_INSTRUCTIONS_SENTINEL.encode() in content


@dataclass(frozen=True)
class DesiredState:
    """Full desired-state model for one project reconcile."""

    project_root: Path
    codex_home: Path
    bridge_home: Path
    project_files: tuple[tuple[Path, bytes], ...]
    preserved_project_files: tuple[Path, ...]
    skills: tuple[GeneratedSkill, ...]
    state_path: Path
    global_instructions: bytes | None = None
    project_skills: tuple[GeneratedSkill, ...] = ()
    global_agents: tuple[GeneratedAgentFile, ...] = ()
    global_prompts: tuple[GeneratedPrompt, ...] = ()
    plugin_resources: tuple[VendoredPluginResource, ...] = ()


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
    ownership_released: bool = False


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

    @property
    def has_errors(self) -> bool:
        """True when any accessible project could not be fully cleaned.

        Projects skipped because the directory no longer exists are not treated
        as errors (vanished projects are expected during uninstall).  Projects
        that were accessible but could not be cleaned — including those with
        missing state files — are actionable errors.
        """
        return any(
            result.status == "no_state"
            or (result.status == "skipped"
                and result.skip_reason != "directory not found")
            for result in self.projects
        )


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
    container: Path


@dataclass(frozen=True)
class _MutationPlan:
    """Planned file, skill, and registry mutations for one reconcile."""

    changes: tuple[Change, ...]
    registry_writes: tuple[_RegistryWrite, ...]


def build_desired_state(
    discovery: DiscoveryResult,
    shim_decision: ClaudeShimDecision,
    skills: Iterable[GeneratedSkill],
    *,
    codex_home: str | Path | None = None,
    bridge_home: str | Path | None = None,
    extra_project_files: Iterable[tuple[Path, bytes]] | None = None,
    project_skills: Iterable[GeneratedSkill] | None = None,
    global_agents: Iterable[GeneratedAgentFile] | None = None,
    global_prompts: Iterable[GeneratedPrompt] | None = None,
    project_agent_files: Iterable[tuple[Path, bytes]] | None = None,
    plugin_resources: Iterable[VendoredPluginResource] | None = None,
) -> DesiredState:
    """Build the desired generated outputs for a project."""
    if shim_decision.action == "fail":
        raise ReconcileError(shim_decision.reason)

    project_root = discovery.project.root.resolve()
    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    bridge_home_path = Path(bridge_home or resolve_bridge_home()).expanduser().resolve()
    project_files: list[tuple[Path, bytes]] = []
    preserved_project_files: list[Path] = []
    skills_tuple = tuple(skills)

    if shim_decision.action in ("create", "bootstrap") and shim_decision.content is not None:
        project_files.append(
            (
                _resolve_managed_project_path(project_root, Path("CLAUDE.md")),
                shim_decision.content.encode(),
            )
        )
    if shim_decision.action == "bootstrap" and shim_decision.agents_md_content is not None:
        project_files.append(
            (
                _resolve_managed_project_path(project_root, Path("AGENTS.md")),
                shim_decision.agents_md_content.encode(),
            )
        )
    elif shim_decision.action == "preserve":
        preserved_project_files.append(
            _resolve_managed_project_path(project_root, Path("CLAUDE.md"))
        )
        # AGENTS.md may have been created by bootstrap — preserve it so the
        # staleness detector does not remove a file the bridge previously wrote.
        agents_md_path = _resolve_managed_project_path(project_root, Path("AGENTS.md"))
        if agents_md_path.exists():
            preserved_project_files.append(agents_md_path)

    # Add project-local agent .toml files
    if project_agent_files:
        for relpath, content in sorted(project_agent_files, key=lambda item: item[0].as_posix()):
            project_files.append(
                (
                    _resolve_managed_project_path(project_root, relpath),
                    content,
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

    state_dir = project_state_dir(project_root, bridge_home=bridge_home_path)
    return DesiredState(
        project_root=project_root,
        codex_home=codex_home_path,
        bridge_home=bridge_home_path,
        project_files=tuple(project_files),
        preserved_project_files=tuple(sorted(set(preserved_project_files), key=str)),
        skills=skills_tuple,
        state_path=state_dir / "state.json",
        global_instructions=global_instructions,
        project_skills=tuple(project_skills or ()),
        global_agents=tuple(global_agents or ()),
        global_prompts=tuple(global_prompts or ()),
        plugin_resources=tuple(plugin_resources or ()),
    )


@dataclass(frozen=True)
class ProjectBuildResult:
    """Result of running the full project build pipeline."""

    desired_state: DesiredState | None
    discovery: DiscoveryResult
    shim_decision: ClaudeShimDecision
    agent_count: int
    skill_count: int
    prompt_count: int
    exclusion_report: object  # ExclusionReport from exclusions module
    diagnostics: tuple  # AgentTranslationDiagnostic and SkillValidationDiagnostic items


def _rewrite_skill_references(
    skills: tuple[GeneratedSkill, ...],
    reference_map: dict[str, str],
) -> tuple[GeneratedSkill, ...]:
    """Rewrite plugin-qualified references in skill file content."""
    from cc_codex_bridge.rewrite_references import rewrite_content

    result: list[GeneratedSkill] = []
    for skill in skills:
        new_files: list[object] = []
        changed = False
        for f in skill.files:
            rewritten = rewrite_content(f.content, reference_map)
            if rewritten != f.content:
                changed = True
                new_files.append(replace(f, content=rewritten))
            else:
                new_files.append(f)
        if changed:
            result.append(replace(skill, files=tuple(new_files)))
        else:
            result.append(skill)
    return tuple(result)


def _rewrite_agent_references(
    agents: tuple[GeneratedAgentFile, ...],
    reference_map: dict[str, str],
) -> tuple[GeneratedAgentFile, ...]:
    """Rewrite plugin-qualified references in agent developer_instructions."""
    from cc_codex_bridge.rewrite_references import rewrite_content

    result: list[GeneratedAgentFile] = []
    for agent in agents:
        rewritten = rewrite_content(
            agent.developer_instructions.encode(), reference_map,
        ).decode()
        if rewritten != agent.developer_instructions:
            result.append(replace(agent, developer_instructions=rewritten))
        else:
            result.append(agent)
    return tuple(result)


def _rewrite_prompt_references(
    prompts: tuple[GeneratedPrompt, ...],
    reference_map: dict[str, str],
) -> tuple[GeneratedPrompt, ...]:
    """Rewrite plugin-qualified references in prompt content."""
    from cc_codex_bridge.rewrite_references import rewrite_content

    result: list[GeneratedPrompt] = []
    for prompt in prompts:
        rewritten = rewrite_content(prompt.content, reference_map)
        if rewritten != prompt.content:
            result.append(replace(prompt, content=rewritten))
        else:
            result.append(prompt)
    return tuple(result)


def build_project_desired_state(
    project_root: str | Path | None = None,
    *,
    codex_home: str | Path | None = None,
    bridge_home: str | Path | None = None,
    claude_home: str | Path | None = None,
    cache_dir: str | Path | None = None,
    exclude_plugins: Iterable[str] = (),
    exclude_skills: Iterable[str] = (),
    exclude_agents: Iterable[str] = (),
    exclude_commands: Iterable[str] = (),
) -> ProjectBuildResult:
    """Run the full discover-translate-build pipeline for one project.

    Returns a ProjectBuildResult containing the desired state and all
    intermediate values both callers (CLI and reconcile_all) need.
    """
    from cc_codex_bridge.bridge_home import config_path as bridge_config_path
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.config import load_config
    from cc_codex_bridge.discover import discover
    from cc_codex_bridge.exclusions import (
        apply_sync_exclusions,
        load_project_exclusions,
        resolve_effective_exclusions,
    )
    from cc_codex_bridge.render_agent_toml import render_agent_toml
    from cc_codex_bridge.translate_agents import (
        assign_agent_names,
        translate_installed_agents_with_diagnostics,
        translate_standalone_agents,
    )
    from cc_codex_bridge.translate_prompts import (
        assign_prompt_names,
        translate_installed_commands as translate_installed_prompts,
        translate_standalone_commands as translate_standalone_prompts,
    )
    from cc_codex_bridge.translate_skills import (
        assign_skill_names,
        translate_installed_skills,
        translate_standalone_skills,
    )

    bridge_home_path = Path(bridge_home or resolve_bridge_home()).expanduser().resolve()

    result = discover(
        project_path=project_root,
        cache_dir=cache_dir,
        claude_home=claude_home,
    )
    cfg = load_config(bridge_config_path(bridge_home=bridge_home_path))

    config_exclusions = load_project_exclusions(result.project.root)
    exclusions = resolve_effective_exclusions(
        config_exclusions,
        global_config=cfg.exclude,
        cli_exclude_plugins=tuple(exclude_plugins) or None,
        cli_exclude_skills=tuple(exclude_skills) or None,
        cli_exclude_agents=tuple(exclude_agents) or None,
        cli_exclude_commands=tuple(exclude_commands) or None,
    )
    result, exclusion_report = apply_sync_exclusions(result, exclusions)
    shim_decision = plan_claude_shim(result.project)

    # Bootstrap: when CLAUDE.md exists without AGENTS.md, the full translation
    # pipeline still runs.  build_desired_state handles the "bootstrap" shim
    # decision by including both CLAUDE.md (shim) and AGENTS.md (original
    # content) in desired project files.  Normal reconcile creates both.

    agent_result = translate_installed_agents_with_diagnostics(result.plugins, bridge_home=bridge_home_path)
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
            agent_count=0,
            skill_count=0,
            prompt_count=0,
            exclusion_report=exclusion_report,
            diagnostics=tuple(all_diagnostics),
        )

    all_global_agents = assign_agent_names(
        (*agent_result.agents, *user_agent_result.agents)
    )
    project_agents = project_agent_result.agents
    global_agents = all_global_agents

    plugin_skill_result = translate_installed_skills(result.plugins, bridge_home=bridge_home_path)
    user_skill_result = translate_standalone_skills(result.user_skills, scope="user")
    project_skill_result = translate_standalone_skills(result.project_skills, scope="project")

    # Translate commands into prompts
    plugin_prompt_result = translate_installed_prompts(result.plugins, bridge_home=bridge_home_path)
    user_prompt_result = translate_standalone_prompts(result.user_commands, scope="user")
    project_prompt_result = translate_standalone_prompts(
        result.project_commands,
        scope="project",
        project_dir_name=result.project.root.name,
    )

    all_prompts = assign_prompt_names((
        *plugin_prompt_result.prompts,
        *user_prompt_result.prompts,
        *project_prompt_result.prompts,
    ))

    all_global_skills = assign_skill_names((
        *plugin_skill_result.skills,
        *user_skill_result.skills,
    ))
    all_project_skills = project_skill_result.skills

    # Rewrite plugin-qualified references in generated content
    from cc_codex_bridge.rewrite_references import build_reference_map, rewrite_content

    reference_map = build_reference_map(
        skills=(*all_global_skills, *all_project_skills),
        prompts=all_prompts,
    )

    if reference_map:
        all_global_skills = _rewrite_skill_references(all_global_skills, reference_map)
        all_project_skills = _rewrite_skill_references(all_project_skills, reference_map)
        global_agents = _rewrite_agent_references(global_agents, reference_map)
        project_agents = _rewrite_agent_references(project_agents, reference_map)
        all_prompts = _rewrite_prompt_references(all_prompts, reference_map)

        if result.user_claude_md is not None:
            rewritten_md = rewrite_content(
                result.user_claude_md.encode(), reference_map,
            ).decode()
            if rewritten_md != result.user_claude_md:
                result = replace(result, user_claude_md=rewritten_md)

    # Render project-local agent .toml files (after reference rewriting)
    project_agent_files: list[tuple[Path, bytes]] = []
    for agent in project_agents:
        relpath = AGENTS_RELATIVE_ROOT / agent.install_filename
        content = render_agent_toml(
            agent.agent_name,
            agent.description,
            agent.developer_instructions,
            sandbox_mode=agent.sandbox_mode,
        )
        project_agent_files.append((relpath, content.encode()))

    skill_diagnostics = (
        *plugin_skill_result.diagnostics,
        *user_skill_result.diagnostics,
        *project_skill_result.diagnostics,
    )
    prompt_diagnostics = (
        *plugin_prompt_result.diagnostics,
        *user_prompt_result.diagnostics,
        *project_prompt_result.diagnostics,
    )
    all_diagnostics = (*all_diagnostics, *skill_diagnostics, *prompt_diagnostics)

    # Exclude skills whose target root is a symlink — the project (or user)
    # already provides Codex-visible skills through the symlink.
    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser()
    project_root_path = result.project.root
    if (project_root_path / SKILLS_RELATIVE_ROOT).is_symlink():
        all_project_skills = ()
    if (codex_home_path / "skills").is_symlink():
        all_global_skills = ()

    total_skill_count = len(all_global_skills) + len(all_project_skills)
    prompt_count = len(all_prompts)

    # Collect and deduplicate plugin resources from skills, agents, and prompts
    all_plugin_resources: dict[tuple[str, str, str], VendoredPluginResource] = {}
    for resource in plugin_skill_result.plugin_resources:
        key = (resource.marketplace, resource.plugin_name, resource.target_dir_name)
        all_plugin_resources[key] = resource
    for resource in agent_result.plugin_resources:
        key = (resource.marketplace, resource.plugin_name, resource.target_dir_name)
        all_plugin_resources[key] = resource
    for resource in plugin_prompt_result.plugin_resources:
        key = (resource.marketplace, resource.plugin_name, resource.target_dir_name)
        all_plugin_resources[key] = resource
    plugin_resources = tuple(sorted(
        all_plugin_resources.values(),
        key=lambda r: (r.marketplace, r.plugin_name, r.target_dir_name),
    ))
    desired_state = build_desired_state(
        result, shim_decision,
        all_global_skills, codex_home=codex_home,
        bridge_home=bridge_home,
        project_skills=all_project_skills,
        global_agents=global_agents,
        global_prompts=all_prompts,
        project_agent_files=project_agent_files,
        plugin_resources=plugin_resources,
    )

    return ProjectBuildResult(
        desired_state=desired_state,
        discovery=result,
        shim_decision=shim_decision,
        agent_count=len(all_global_agents) + len(project_agents),
        skill_count=total_skill_count,
        prompt_count=prompt_count,
        exclusion_report=exclusion_report,
        diagnostics=tuple(all_diagnostics),
    )


def diff_desired_state(desired: DesiredState) -> ReconcileReport:
    """Compare current outputs to desired state without writing."""
    previous_state = _load_previous_state(desired)
    prev_managed = _previously_managed_set(previous_state)
    plan = _plan_mutations(desired, previous_state)
    state_write_needed = _state_write_needed(desired, prev_managed)
    _validate_mutation_targets(
        desired,
        previous_state,
        plan,
        state_write_needed=state_write_needed,
    )
    changes = list(plan.changes)

    # Report state file mutations that reconcile would perform
    if state_write_needed:
        kind = "create" if not desired.state_path.exists() else "update"
        changes.append(Change(kind, desired.state_path, resource_kind="state"))

    return ReconcileReport(changes=tuple(changes), applied=False)


def reconcile_desired_state(desired: DesiredState) -> ReconcileReport:
    """Apply the desired state to disk."""
    previous_state = _load_previous_state(desired)
    prev_managed = _previously_managed_set(previous_state)
    plan = _plan_mutations(desired, previous_state)
    state_write_needed = _state_write_needed(desired, prev_managed)
    _validate_mutation_targets(
        desired,
        previous_state,
        plan,
        state_write_needed=state_write_needed,
    )
    if not plan.changes and not plan.registry_writes and not state_write_needed:
        return ReconcileReport(changes=(), applied=True)

    state_existed = desired.state_path.exists()
    _apply_changes(desired, plan, prev_managed)
    changes = list(plan.changes)
    if state_write_needed:
        kind = "create" if not state_existed else "update"
        changes.append(Change(kind, desired.state_path, resource_kind="state"))
    return ReconcileReport(changes=tuple(changes), applied=True)


def clean_project(
    project_root: str | Path,
    *,
    bridge_home: str | Path | None = None,
    dry_run: bool = False,
) -> ReconcileReport:
    """Remove all bridge-generated artifacts from one project.

    Loads the existing bridge state to determine what is managed, releases
    global skill registry claims, and deletes managed project files plus the
    state file.  Returns a report of what was (or would be) removed.
    """
    project_root_path = Path(project_root).expanduser().resolve()
    bridge_home_path = Path(bridge_home or resolve_bridge_home()).expanduser().resolve()
    state_dir = project_state_dir(project_root_path, bridge_home=bridge_home_path)
    state_path = state_dir / "state.json"

    if state_path.is_symlink():
        raise ReconcileError(f"Refusing to use symlinked bridge state file: {state_path}")
    # Verify state file resolves within bridge home (catches symlinked ancestors)
    _assert_path_contained(state_path, bridge_home_path, label="Bridge state file")
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

    # Validate managed project files against the allowlist (same check as
    # _compute_project_file_changes) to prevent corrupted state from deleting
    # hand-authored files like AGENTS.md.
    invalid_managed_paths = sorted(
        relative for relative in previous_state.managed_project_files
        if not _is_allowed_managed_project_relative(relative)
    )
    if invalid_managed_paths:
        raise ReconcileError(
            "Interop state contains unexpected managed project files: "
            + ", ".join(invalid_managed_paths)
        )

    # Remove managed project skill directories
    managed_project_skill_dirs = _validated_managed_project_skill_dirs(previous_state)
    skill_dirs_to_remove: list[Path] = []
    for skill_dir_name in sorted(managed_project_skill_dirs):
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
            stored_hash = previous_state.managed_project_files[relative]
            if stored_hash:
                current_hash = hash_file_content(path.read_bytes())
                if current_hash != stored_hash:
                    # File was externally modified — don't remove it
                    continue
            changes.append(Change("remove", path))

    # Bootstrap reversal: when both AGENTS.md and CLAUDE.md are scheduled for
    # removal (both unedited), restore CLAUDE.md to the original content that
    # the bootstrap copied into AGENTS.md.  This way the project looks like it
    # did before the bridge was ever used.
    agents_path = project_root_path / "AGENTS.md"
    claude_path = project_root_path / "CLAUDE.md"
    paths_to_remove = {c.path for c in changes if c.kind == "remove"}
    bootstrap_reversal_content: bytes | None = None
    if agents_path in paths_to_remove and claude_path in paths_to_remove:
        bootstrap_reversal_content = agents_path.read_bytes()
        changes = [
            c if c.path != claude_path else Change("restore", claude_path)
            for c in changes
        ]

    # Release skill ownership claims from the global registry
    registry_path = bridge_home_path / GLOBAL_REGISTRY_FILENAME
    if registry_path.is_symlink():
        raise ReconcileError(
            f"Refusing to use symlinked global skill registry file: {registry_path}"
        )
    registry = GlobalSkillRegistry.from_path(registry_path)
    if registry is None:
        raise ReconcileError(
            f"Cannot clean: global registry missing or corrupt at {registry_path}. "
            "Global ownership claims cannot be released safely."
        )

    registry_changed = False
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

    # Release agent ownership claims
    updated_agents = dict(registry.agents)
    for agent_filename in sorted(registry.agents):
        entry = registry.agents[agent_filename]
        if project_root_path not in entry.owners:
            continue
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != project_root_path
        )
        if remaining_owners:
            updated_agents[agent_filename] = GlobalAgentEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
        else:
            del updated_agents[agent_filename]
            agent_path = codex_home_path / "agents" / agent_filename
            if agent_path.exists():
                changes.append(Change("remove", agent_path, resource_kind="agent"))
        registry_changed = True

    # Release plugin resource ownership claims
    updated_plugin_resources = dict(registry.plugin_resources)
    for plugin_dir_name in sorted(registry.plugin_resources):
        entry = registry.plugin_resources[plugin_dir_name]
        if project_root_path not in entry.owners:
            continue
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != project_root_path
        )
        if remaining_owners:
            updated_plugin_resources[plugin_dir_name] = GlobalPluginResourceEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
        else:
            del updated_plugin_resources[plugin_dir_name]
            plugin_dir = bridge_home_path / "plugins" / plugin_dir_name
            if plugin_dir.exists():
                changes.append(Change("remove", plugin_dir, resource_kind="plugin_resource"))
        registry_changed = True

    # Release prompt ownership claims
    updated_prompts = dict(registry.prompts)
    for prompt_filename in sorted(registry.prompts):
        entry = registry.prompts[prompt_filename]
        if project_root_path not in entry.owners:
            continue
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != project_root_path
        )
        if remaining_owners:
            updated_prompts[prompt_filename] = GlobalPromptEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
        else:
            del updated_prompts[prompt_filename]
            prompt_path = codex_home_path / "prompts" / prompt_filename
            if prompt_path.exists():
                changes.append(Change("remove", prompt_path, resource_kind="prompt"))
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
            agents=updated_agents,
            prompts=updated_prompts,
            plugin_resources=updated_plugin_resources,
        )
    else:
        updated_registry = None

    # Verify project-local clean targets resolve within project_root
    for change in changes:
        if change.path == state_path:
            continue
        if change.resource_kind in ("skill", "agent", "prompt"):
            # Global skills, agents, and prompts live under codex_home, not project_root
            _assert_path_contained(change.path, codex_home_path, label="Clean target")
        elif change.resource_kind == "plugin_resource":
            # Plugin resources live under bridge_home, not project_root
            _assert_path_contained(change.path, bridge_home_path, label="Clean target")
        else:
            _assert_path_contained(change.path, project_root_path, label="Clean target")

    if dry_run:
        return ReconcileReport(
            changes=tuple(changes),
            applied=False,
            ownership_released=registry_changed,
        )

    # Apply removals — state file last to preserve cleanup atomicity
    for change in changes:
        if change.path == state_path:
            continue  # deferred to end
        if change.kind == "restore" and bootstrap_reversal_content is not None:
            # Bootstrap reversal: write original content back to CLAUDE.md
            _atomic_write_file(
                change.path,
                bootstrap_reversal_content,
                container=project_root_path,
            )
        elif change.resource_kind in ("skill", "project_skill"):
            if change.path.is_dir():
                shutil.rmtree(change.path)
            elif change.path.exists() or change.path.is_symlink():
                change.path.unlink()
        elif change.resource_kind == "agent":
            change.path.unlink(missing_ok=True)
        elif change.resource_kind == "prompt":
            change.path.unlink(missing_ok=True)
        elif change.resource_kind == "plugin_resource":
            shutil.rmtree(change.path)
        else:
            change.path.unlink(missing_ok=True)
            _cleanup_empty_parents(change.path.parent, project_root_path / ".codex")

    # Update the registry
    if registry_changed and updated_registry is not None:
        _atomic_write_file(
            registry_path,
            updated_registry.to_json().encode(),
            container=bridge_home_path,
        )

    # Remove the state file last, then clean up its parent if empty
    state_path.unlink(missing_ok=True)
    _cleanup_empty_parents(state_path.parent, bridge_home_path)

    return ReconcileReport(
        changes=tuple(changes),
        applied=True,
        ownership_released=registry_changed,
    )


@dataclass(frozen=True)
class ReconcileAllProjectResult:
    """Result of reconciling one project in a reconcile --all run."""

    project_root: Path
    report: ReconcileReport


@dataclass(frozen=True)
class ReconcileAllError:
    """One project that failed during reconcile --all."""

    project_root: Path
    error: str


@dataclass(frozen=True)
class ReconcileAllReport:
    """Full reconcile --all result."""

    results: tuple[ReconcileAllProjectResult, ...]
    errors: tuple[ReconcileAllError, ...]
    scan_result: ScanResult | None = None


def reconcile_all(
    *,
    codex_home: str | Path | None = None,
    bridge_home: str | Path | None = None,
    claude_home: str | Path | None = None,
    cache_dir: str | Path | None = None,
    exclude_plugins: Iterable[str] = (),
    exclude_skills: Iterable[str] = (),
    exclude_agents: Iterable[str] = (),
    exclude_commands: Iterable[str] = (),
    dry_run: bool = False,
) -> ReconcileAllReport:
    """Reconcile all registered projects."""
    from cc_codex_bridge.model import AgentTranslationDiagnostic
    from cc_codex_bridge.translate_agents import format_agent_translation_diagnostics

    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    bridge_home_path = Path(bridge_home or resolve_bridge_home()).expanduser().resolve()
    registry_path = bridge_home_path / GLOBAL_REGISTRY_FILENAME

    snapshot = _load_registry_snapshot(registry_path)
    scan_result = scan_for_projects(bridge_home_path)
    registry_roots = list(snapshot.registry.projects) if snapshot.existed else []
    project_roots = sorted(
        set(registry_roots) | set(scan_result.bridgeable), key=str
    )

    results: list[ReconcileAllProjectResult] = []
    errors: list[ReconcileAllError] = []

    for project_root in project_roots:
        if not project_root.is_dir():
            errors.append(ReconcileAllError(project_root=project_root, error="directory not found"))
            continue
        if (
            not (project_root / "AGENTS.md").is_file()
            and not (project_root / "CLAUDE.md").is_file()
        ):
            errors.append(ReconcileAllError(project_root=project_root, error="AGENTS.md not found"))
            continue

        try:
            build = build_project_desired_state(
                project_root,
                codex_home=codex_home_path,
                bridge_home=bridge_home,
                claude_home=claude_home,
                cache_dir=cache_dir,
                exclude_plugins=exclude_plugins,
                exclude_skills=exclude_skills,
                exclude_agents=exclude_agents,
                exclude_commands=exclude_commands,
            )
            # Only agent diagnostics block reconciliation.
            # Skill validation warnings are informational and do not prevent sync.
            agent_diags = tuple(
                d for d in build.diagnostics
                if isinstance(d, AgentTranslationDiagnostic)
            )
            if agent_diags:
                errors.append(ReconcileAllError(
                    project_root=project_root,
                    error=format_agent_translation_diagnostics(agent_diags),
                ))
                continue

            if dry_run:
                report = diff_desired_state(build.desired_state)
            else:
                report = reconcile_desired_state(build.desired_state)

            results.append(ReconcileAllProjectResult(
                project_root=project_root, report=report,
            ))
        except Exception as exc:
            errors.append(ReconcileAllError(project_root=project_root, error=str(exc)))

    return ReconcileAllReport(results=tuple(results), errors=tuple(errors), scan_result=scan_result)


def uninstall_all(
    *,
    codex_home: str | Path | None = None,
    bridge_home: str | Path | None = None,
    launchagents_dir: str | Path | None = None,
    dry_run: bool = False,
) -> UninstallReport:
    """Remove all bridge-generated artifacts from the machine.

    Discovers projects from the global skill registry, cleans each accessible
    one, then removes global artifacts and LaunchAgent plists.
    """
    from cc_codex_bridge.install_launchagent import find_bridge_launchagents

    codex_home_path = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    bridge_home_path = Path(bridge_home or resolve_bridge_home()).expanduser().resolve()
    registry_path = bridge_home_path / GLOBAL_REGISTRY_FILENAME

    # Step 1: Discover project roots from the registry
    project_roots: set[Path] = set()
    snapshot = _load_registry_snapshot(registry_path)
    if snapshot.existed:
        project_roots.update(snapshot.registry.projects)

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

        state_dir = project_state_dir(root, bridge_home=bridge_home_path)
        state_path = state_dir / "state.json"
        if not state_path.exists():
            project_results.append(UninstallProjectResult(
                root=root,
                status="no_state",
                changes=(),
            ))
            continue

        try:
            report = clean_project(root, bridge_home=bridge_home_path, dry_run=dry_run)
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

    # Force-remove any remaining skill/agent entries still in the registry
    # (handles skills/agents owned by skipped projects)
    if registry_path.exists():
        post_clean_snapshot = _load_registry_snapshot(registry_path)
        if post_clean_snapshot.existed:
            for install_dir_name in sorted(post_clean_snapshot.registry.skills):
                skill_path = codex_home_path / "skills" / install_dir_name
                if skill_path.exists():
                    global_removals.append(
                        Change("remove", skill_path, resource_kind="skill")
                    )
            for agent_filename in sorted(post_clean_snapshot.registry.agents):
                agent_path = codex_home_path / "agents" / agent_filename
                if agent_path.exists():
                    global_removals.append(
                        Change("remove", agent_path, resource_kind="agent")
                    )
            for prompt_filename in sorted(post_clean_snapshot.registry.prompts):
                prompt_path = codex_home_path / "prompts" / prompt_filename
                if prompt_path.exists():
                    global_removals.append(
                        Change("remove", prompt_path, resource_kind="prompt")
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
                if change.path.is_dir():
                    shutil.rmtree(change.path)
                elif change.path.exists() or change.path.is_symlink():
                    change.path.unlink()
            elif change.resource_kind == "prompt":
                change.path.unlink(missing_ok=True)
            else:
                change.path.unlink(missing_ok=True)

        # Remove LaunchAgent plists
        for removal in launchagent_removals:
            removal.path.unlink(missing_ok=True)

        # Remove vendored plugin resources
        plugins_dir = bridge_home_path / "plugins"
        if plugins_dir.exists():
            shutil.rmtree(plugins_dir)

        # Remove activity logs
        logs_dir = bridge_home_path / "logs"
        if logs_dir.exists():
            shutil.rmtree(logs_dir)

        # Remove bridge home directory if empty
        if bridge_home_path.exists():
            _cleanup_empty_parents(bridge_home_path / "projects", bridge_home_path)
            try:
                bridge_home_path.rmdir()
            except OSError:
                pass  # Not empty — other state may remain

    return UninstallReport(
        projects=tuple(project_results),
        global_removals=tuple(global_removals),
        launchagent_removals=launchagent_removals,
        applied=not dry_run,
    )


def _apply_changes(
    desired: DesiredState,
    plan: _MutationPlan,
    previously_managed: frozenset[str] = frozenset(),
) -> None:
    """Write all planned changes to disk."""
    from cc_codex_bridge.render_agent_toml import render_agent_toml

    desired_map = dict(desired.project_files)
    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}
    project_skills_by_name = {skill.install_dir_name: skill for skill in desired.project_skills}
    agents_by_filename = {agent.install_filename: agent for agent in desired.global_agents}
    desired_prompt_map = {p.filename: p for p in desired.global_prompts}
    plugin_resources_by_path: dict[Path, VendoredPluginResource] = {
        desired.bridge_home / "plugins" / f"{r.marketplace}-{r.plugin_name}" / r.target_dir_name: r
        for r in desired.plugin_resources
    }

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
        if change.resource_kind == "agent":
            if change.kind in ("create", "update"):
                agent = agents_by_filename[change.path.name]
                content = render_agent_toml(
                    agent.agent_name,
                    agent.description,
                    agent.developer_instructions,
                    sandbox_mode=agent.sandbox_mode,
                )
                _atomic_write_file(change.path, content.encode(), container=desired.codex_home)
            elif change.kind == "remove":
                change.path.unlink(missing_ok=True)
            continue
        if change.resource_kind == "prompt":
            if change.kind in ("create", "update"):
                prompt = desired_prompt_map[change.path.name]
                change.path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write_file(change.path, prompt.content, container=desired.codex_home)
            elif change.kind == "remove":
                change.path.unlink(missing_ok=True)
            continue
        if change.resource_kind == "plugin_resource":
            if change.kind in ("create", "update"):
                if change.path.exists():
                    shutil.rmtree(change.path)
                change.path.mkdir(parents=True, exist_ok=True)
                resource = plugin_resources_by_path[change.path]
                for f in resource.files:
                    file_path = change.path / f.relative_path
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_bytes(f.content)
                    file_path.chmod(f.mode)
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
        _atomic_write_file(
            registry_write.destination,
            registry_write.content,
            container=registry_write.container,
        )

    seed_config_stub(desired.bridge_home)

    state_bytes = _build_state_record(desired, previously_managed).to_json().encode()
    desired.state_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_file(desired.state_path, state_bytes, container=desired.bridge_home)


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
        if change.resource_kind in ("skill", "project_skill"):
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
        elif change.resource_kind == "agent":
            from cc_codex_bridge.render_agent_toml import render_agent_toml
            agents_by_filename = {a.install_filename: a for a in desired.global_agents}
            agent = agents_by_filename.get(change.path.name)
            if agent is not None:
                desired_content = render_agent_toml(
                    agent.agent_name,
                    agent.description,
                    agent.developer_instructions,
                    sandbox_mode=agent.sandbox_mode,
                ).encode()
            else:
                desired_content = None
        elif change.resource_kind == "prompt":
            prompts_by_filename = {p.filename: p for p in desired.global_prompts}
            prompt = prompts_by_filename.get(change.path.name)
            desired_content = prompt.content if prompt is not None else None
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
    """Plan file, skill, agent, prompt, and registry mutations for one reconcile run.

    Skills, agents, and prompts share the same global registry file.  To
    prevent independent planners from clobbering each other's updates, we
    load the registry snapshot once, let each planner mutate it in sequence,
    then generate a single registry write at the end.
    """
    project_changes = _compute_project_file_changes(desired, previous_state)
    project_skill_changes = _plan_project_skill_mutations(desired, previous_state)

    snapshot = _load_registry_snapshot(desired.bridge_home / GLOBAL_REGISTRY_FILENAME)

    skill_changes, updated_registry = _plan_skill_mutations(
        desired, previous_state, snapshot,
    )
    agent_changes, updated_registry = _plan_global_agent_mutations(
        desired, previous_state, snapshot, updated_registry,
    )
    prompt_changes, updated_registry = _plan_prompt_mutations(
        desired, snapshot, updated_registry,
    )
    plugin_resource_changes, updated_registry = _plan_plugin_resource_mutations(
        desired, snapshot, updated_registry,
    )

    # Build registry write from the final accumulated state.
    registry_writes: list[_RegistryWrite] = []
    registry_write = _build_registry_write(snapshot, updated_registry)
    if registry_write is not None:
        registry_writes.append(registry_write)

    global_changes = _plan_global_instructions_changes(desired)
    return _MutationPlan(
        changes=tuple((
            *project_changes, *project_skill_changes,
            *skill_changes, *agent_changes, *prompt_changes,
            *global_changes, *plugin_resource_changes,
        )),
        registry_writes=tuple(registry_writes),
    )


def _plan_project_skill_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
) -> tuple[Change, ...]:
    """Plan project-local skill directory mutations using directory-snapshot comparison."""
    skills_root = desired.project_root / SKILLS_RELATIVE_ROOT
    # When the skills root is a symlink (e.g. .codex/skills -> .ai/skills),
    # the project already provides Codex-visible skills through the symlink.
    # Skip project skill management to avoid writing through the symlink
    # and modifying the target directory.
    if skills_root.is_symlink():
        return ()
    desired_skills = {skill.install_dir_name: skill for skill in desired.project_skills}
    previously_managed = (
        _validated_managed_project_skill_dirs(previous_state)
        if previous_state
        else set()
    )
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
        # or if the existing content is a subset of what we'd generate
        # (hand-bridged directory from the same source skill).
        if install_dir_name not in previously_managed:
            if not _directory_is_subset_of_skill(destination, skill):
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
    managed_project_files = set(previous_state.managed_project_files.keys()) if previous_state else set()
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
        if owned:
            # Check for drift: if the stored hash doesn't match the on-disk
            # content, the file was externally modified — skip the update to
            # avoid overwriting user edits.
            stored_hash = previous_state.managed_project_files.get(relative, "") if previous_state else ""
            if stored_hash:
                current_hash = hash_file_content(existing)
                if current_hash != stored_hash:
                    # File was externally modified — skip update to preserve user edits
                    continue
        else:
            # First reconcile for this file — the bridge is adopting it.
            # This is the bootstrap case: the file exists with different content
            # (e.g. CLAUDE.md being replaced with the shim) and isn't tracked yet.
            pass
        changes.append(Change("update", path))

    desired_project_paths = {
        *(_project_relative(desired, path) for path, _ in desired.project_files),
        *(_project_relative(desired, path) for path in desired.preserved_project_files),
    }

    for relative in sorted(managed_project_files - desired_project_paths):
        path = desired.project_root / relative
        if path.exists():
            # Check for drift: if the stored hash doesn't match the on-disk
            # content, the file was externally modified — skip the removal to
            # avoid deleting user-edited content.
            stored_hash = previous_state.managed_project_files.get(relative, "") if previous_state else ""
            if stored_hash:
                current_hash = hash_file_content(path.read_bytes())
                if current_hash != stored_hash:
                    continue
            changes.append(Change("remove", path))

    return tuple(changes)


def _plan_skill_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
    snapshot: _RegistrySnapshot,
) -> tuple[tuple[Change, ...], GlobalSkillRegistry]:
    """Plan global-skill ownership and directory mutations.

    Returns the change list plus the updated registry for the caller
    to pass into the agent planner.
    """
    global_skills_root = desired.codex_home / "skills"
    if global_skills_root.is_symlink():
        return (), GlobalSkillRegistry(
            skills=dict(snapshot.registry.skills),
            projects=_ensure_project_in_list(
                snapshot.registry.projects, desired.project_root
            ),
            agents=dict(snapshot.registry.agents),
            prompts=dict(snapshot.registry.prompts),
            plugin_resources=dict(snapshot.registry.plugin_resources),
        )
    desired_skills = {skill.install_dir_name: skill for skill in desired.skills}
    desired_hashes = {
        install_dir_name: hash_generated_skill(skill)
        for install_dir_name, skill in desired_skills.items()
    }
    changes: list[Change] = []

    updated_registry = GlobalSkillRegistry(
        skills=dict(snapshot.registry.skills),
        projects=_ensure_project_in_list(
            snapshot.registry.projects, desired.project_root
        ),
        agents=dict(snapshot.registry.agents),
        prompts=dict(snapshot.registry.prompts),
        plugin_resources=dict(snapshot.registry.plugin_resources),
    )
    for install_dir_name in sorted(desired_skills):
        skill = desired_skills[install_dir_name]
        destination = desired.codex_home / "skills" / install_dir_name
        existing_entry = updated_registry.skills.get(install_dir_name)
        desired_hash = desired_hashes[install_dir_name]
        registry_owned = existing_entry is not None

        if existing_entry is not None and existing_entry.content_hash != desired_hash:
            if desired.project_root in existing_entry.owners:
                # This project already owns this entry — the shared source
                # (plugin or user skill) upgraded.  Allow the update.
                registry_owned = True
            else:
                # A different project owns this entry with different content.
                # This is a genuine conflict (e.g., exclusion-driven name
                # shifts producing different content at the same name).
                raise ReconcileError(
                    "Generated skill registry conflict for "
                    f"{destination}: existing content hash {existing_entry.content_hash} "
                    f"does not match desired {desired_hash}"
                )

        if destination.exists() and not destination.is_dir():
            raise ReconcileError(f"Expected a skill directory but found a file: {destination}")

        if destination.is_symlink():
            raise ReconcileError(
                f"Refusing to overwrite symlinked global skill directory: {destination}"
            )

        if not registry_owned:
            if destination.exists() and not _directory_matches_skill(destination, skill):
                raise ReconcileError(
                    "Refusing to adopt conflicting existing skill directory: "
                    f"{destination}"
                )
            existing_owners: tuple[Path, ...] = ()
        else:
            existing_owners = existing_entry.owners if existing_entry is not None else ()

        updated_registry.skills[install_dir_name] = GlobalSkillEntry(
            content_hash=desired_hash,
            owners=_sorted_owner_set((*existing_owners, desired.project_root)),
        )

        if not destination.exists():
            changes.append(Change("create", destination, resource_kind="skill"))
            continue
        if _directory_matches_skill(destination, skill):
            continue
        changes.append(Change("update", destination, resource_kind="skill"))

    previously_owned = _owned_skill_names(
        snapshot.registry,
        desired.project_root,
    )
    for install_dir_name in sorted(previously_owned - set(desired_skills)):
        entry = updated_registry.skills[install_dir_name]
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != desired.project_root
        )
        if remaining_owners:
            updated_registry.skills[install_dir_name] = GlobalSkillEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
            continue

        del updated_registry.skills[install_dir_name]
        stale_path = desired.codex_home / "skills" / install_dir_name
        if stale_path.exists():
            changes.append(Change("remove", stale_path, resource_kind="skill"))

    return tuple(changes), updated_registry


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


def _plan_plugin_resource_mutations(
    desired: DesiredState,
    snapshot: _RegistrySnapshot,
    updated_registry: GlobalSkillRegistry,
) -> tuple[tuple[Change, ...], GlobalSkillRegistry]:
    """Plan vendored plugin resource directory mutations and registry ownership.

    Compares desired vendored resources against on-disk state using
    directory-snapshot comparison.  Also updates the global registry to
    track which projects own each vendored plugin resource directory,
    matching the ownership model used for global skills and agents.
    """
    changes: list[Change] = []

    # Group resources by plugin dir name for combined hashing
    desired_by_dir: dict[str, list[VendoredPluginResource]] = {}
    for resource in desired.plugin_resources:
        dir_name = f"{resource.marketplace}-{resource.plugin_name}"
        desired_by_dir.setdefault(dir_name, []).append(resource)

    # Compute combined hash per dir
    desired_hashes: dict[str, str] = {}
    for dir_name, resources in desired_by_dir.items():
        all_files = tuple(
            f
            for r in sorted(resources, key=lambda r: r.target_dir_name)
            for f in r.files
        )
        desired_hashes[dir_name] = hash_generated_skill_files(all_files)

    # Update registry entries for each plugin dir
    for dir_name in sorted(desired_by_dir):
        existing_entry = updated_registry.plugin_resources.get(dir_name)
        desired_hash = desired_hashes[dir_name]

        if existing_entry is not None:
            existing_owners = existing_entry.owners
        else:
            existing_owners = ()

        updated_registry.plugin_resources[dir_name] = GlobalPluginResourceEntry(
            content_hash=desired_hash,
            owners=_sorted_owner_set((*existing_owners, desired.project_root)),
        )

    # Plan on-disk mutations per plugin dir, using hash-based fast path
    for dir_name in sorted(desired_by_dir):
        resources = desired_by_dir[dir_name]
        desired_hash = desired_hashes[dir_name]
        existing_entry = snapshot.registry.plugin_resources.get(dir_name)

        # Hash-based fast path: if registry hash matches desired hash,
        # all subdirs can skip the expensive on-disk comparison (the
        # combined hash covers all subdirs under this parent).
        hash_matches = (
            existing_entry is not None
            and existing_entry.content_hash == desired_hash
        )

        for resource in resources:
            resource_dir = (
                desired.bridge_home
                / "plugins"
                / dir_name
                / resource.target_dir_name
            )

            if not resource_dir.exists():
                changes.append(Change("create", resource_dir, resource_kind="plugin_resource"))
                continue

            # Fast path: registry hash matches — content unchanged, skip on-disk read
            if hash_matches:
                continue

            # Slow path: hash differs or no registry entry — check on-disk
            if not _directory_matches_resource(resource_dir, resource):
                changes.append(Change("update", resource_dir, resource_kind="plugin_resource"))

    # Detect stale plugin resource dirs owned by this project
    previously_owned = _owned_plugin_resource_dirs(
        snapshot.registry, desired.project_root,
    )
    for dir_name in sorted(previously_owned - set(desired_by_dir)):
        entry = updated_registry.plugin_resources[dir_name]
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != desired.project_root
        )
        if remaining_owners:
            updated_registry.plugin_resources[dir_name] = GlobalPluginResourceEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
            continue

        del updated_registry.plugin_resources[dir_name]
        stale_path = desired.bridge_home / "plugins" / dir_name
        if stale_path.exists():
            changes.append(Change("remove", stale_path, resource_kind="plugin_resource"))

    return tuple(changes), updated_registry


def _directory_matches_resource(path: Path, resource: VendoredPluginResource) -> bool:
    """Check whether an installed vendored resource directory matches the desired tree."""
    expected_files = {f.relative_path: f for f in resource.files}
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


def _plan_global_agent_mutations(
    desired: DesiredState,
    previous_state: BridgeState | None,
    snapshot: _RegistrySnapshot,
    updated_registry: GlobalSkillRegistry,
) -> tuple[tuple[Change, ...], GlobalSkillRegistry]:
    """Plan global agent file and registry mutations.

    Receives the updated registry from the skill planner and continues
    accumulating changes.  Returns the final updated registry.
    """
    from cc_codex_bridge.render_agent_toml import render_agent_toml

    desired_agents = {agent.install_filename: agent for agent in desired.global_agents}
    desired_contents: dict[str, str] = {}
    desired_hashes: dict[str, str] = {}
    for filename, agent in desired_agents.items():
        content = render_agent_toml(
            agent.agent_name,
            agent.description,
            agent.developer_instructions,
            sandbox_mode=agent.sandbox_mode,
        )
        desired_contents[filename] = content
        desired_hashes[filename] = hash_agent_file(content)

    changes: list[Change] = []

    for filename in sorted(desired_agents):
        destination = desired.codex_home / "agents" / filename
        existing_entry = updated_registry.agents.get(filename)
        desired_hash = desired_hashes[filename]
        registry_owned = existing_entry is not None

        if existing_entry is not None and existing_entry.content_hash != desired_hash:
            # Agent files are single files derived from the same plugin source.
            # When the plugin upgrades, any project reconciling first should
            # update the shared file and registry entry for all owners.
            registry_owned = True

        if not registry_owned:
            if destination.exists() and not destination.is_symlink():
                existing_content = read_utf8_text(
                    destination, label="existing agent file", error_type=ReconcileError
                )
                if hash_agent_file(existing_content) != desired_hash:
                    raise ReconcileError(
                        f"Refusing to adopt conflicting existing agent file: {destination}"
                    )
            existing_owners: tuple[Path, ...] = ()
        else:
            existing_owners = existing_entry.owners if existing_entry is not None else ()

        updated_registry.agents[filename] = GlobalAgentEntry(
            content_hash=desired_hash,
            owners=_sorted_owner_set((*existing_owners, desired.project_root)),
        )

        if not destination.exists():
            changes.append(Change("create", destination, resource_kind="agent"))
            continue
        if destination.is_symlink():
            raise ReconcileError(f"Refusing to overwrite symlinked agent file: {destination}")
        existing_content = read_utf8_text(
            destination, label="existing agent file", error_type=ReconcileError
        )
        if hash_agent_file(existing_content) == desired_hash:
            continue
        changes.append(Change("update", destination, resource_kind="agent"))

    # Detect stale agent files owned by this project
    previously_owned_agents = _owned_agent_filenames(
        snapshot.registry, desired.project_root
    )
    for filename in sorted(previously_owned_agents - set(desired_agents)):
        entry = updated_registry.agents[filename]
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != desired.project_root
        )
        if remaining_owners:
            updated_registry.agents[filename] = GlobalAgentEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
            continue

        del updated_registry.agents[filename]
        stale_path = desired.codex_home / "agents" / filename
        if stale_path.exists():
            changes.append(Change("remove", stale_path, resource_kind="agent"))

    return tuple(changes), updated_registry


def _owned_agent_filenames(registry: GlobalSkillRegistry, project_root: Path) -> set[str]:
    """Return the agent filenames currently claimed by one project."""
    return {
        filename
        for filename, entry in registry.agents.items()
        if project_root in entry.owners
    }


def _owned_plugin_resource_dirs(registry: GlobalSkillRegistry, project_root: Path) -> set[str]:
    """Return the plugin resource dir names currently claimed by one project."""
    return {
        dir_name
        for dir_name, entry in registry.plugin_resources.items()
        if project_root in entry.owners
    }


def _owned_prompt_names(
    registry: GlobalSkillRegistry,
    project_root: Path,
) -> set[str]:
    """Return prompt filenames owned by a project."""
    return {
        filename
        for filename, entry in registry.prompts.items()
        if project_root in entry.owners
    }


def _plan_prompt_mutations(
    desired: DesiredState,
    snapshot: _RegistrySnapshot,
    updated_registry: GlobalSkillRegistry,
) -> tuple[tuple[Change, ...], GlobalSkillRegistry]:
    """Plan global-prompt ownership and file mutations.

    Prompts are flat files at ``codex_home / "prompts" / filename``.
    Ownership follows the same model as agents: the registry tracks
    which projects contributed each prompt, and the first project to
    reconcile after a content change updates both the file and registry.
    """
    desired_prompts = {p.filename: p for p in desired.global_prompts}
    desired_hashes: dict[str, str] = {
        filename: hash_prompt_content(prompt.content)
        for filename, prompt in desired_prompts.items()
    }
    changes: list[Change] = []

    for filename in sorted(desired_prompts):
        prompt = desired_prompts[filename]
        destination = desired.codex_home / "prompts" / filename
        existing_entry = updated_registry.prompts.get(filename)
        desired_hash = desired_hashes[filename]
        registry_owned = existing_entry is not None

        if existing_entry is not None and existing_entry.content_hash != desired_hash:
            if desired.project_root in existing_entry.owners:
                # This project already owns this entry — the shared source
                # (plugin or user command) changed.  Allow the update.
                registry_owned = True
            else:
                # A different project owns this entry with different content.
                # This is a genuine conflict (e.g., exclusion-driven name
                # shifts producing different content at the same name).
                raise ReconcileError(
                    "Generated prompt registry conflict for "
                    f"{destination}: existing content hash {existing_entry.content_hash} "
                    f"does not match desired {desired_hash}"
                )

        if not registry_owned:
            if destination.exists() and not destination.is_symlink():
                existing_hash = hash_prompt_content(destination.read_bytes())
                if existing_hash != desired_hash:
                    raise ReconcileError(
                        f"Refusing to adopt conflicting existing prompt file: {destination}"
                    )
            existing_owners: tuple[Path, ...] = ()
        else:
            existing_owners = existing_entry.owners if existing_entry is not None else ()

        updated_registry.prompts[filename] = GlobalPromptEntry(
            content_hash=desired_hash,
            owners=_sorted_owner_set((*existing_owners, desired.project_root)),
        )

        if not destination.exists():
            changes.append(Change("create", destination, resource_kind="prompt"))
            continue
        if destination.is_symlink():
            raise ReconcileError(f"Refusing to overwrite symlinked prompt file: {destination}")
        existing_hash = hash_prompt_content(destination.read_bytes())
        if existing_hash == desired_hash:
            continue
        changes.append(Change("update", destination, resource_kind="prompt"))

    # Detect stale prompt files owned by this project
    previously_owned_prompts = _owned_prompt_names(
        snapshot.registry, desired.project_root
    )
    for filename in sorted(previously_owned_prompts - set(desired_prompts)):
        entry = updated_registry.prompts[filename]
        remaining_owners = tuple(
            owner for owner in entry.owners if owner != desired.project_root
        )
        if remaining_owners:
            updated_registry.prompts[filename] = GlobalPromptEntry(
                content_hash=entry.content_hash,
                owners=remaining_owners,
            )
            continue

        del updated_registry.prompts[filename]
        stale_path = desired.codex_home / "prompts" / filename
        if stale_path.exists():
            changes.append(Change("remove", stale_path, resource_kind="prompt"))

    return tuple(changes), updated_registry


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
        container=snapshot.path.parent,
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


def _directory_is_subset_of_skill(path: Path, skill: GeneratedSkill) -> bool:
    """Check whether every file in an existing directory has a counterpart in the desired skill.

    Returns True when the directory file paths are a subset of what the bridge
    would generate.  Content may differ (the bridge rewrites cross-references),
    so only the file tree is compared.  This identifies hand-bridged directories
    that were copied from the same source skill before the bridge existed.
    """
    expected_paths = {generated_file.relative_path for generated_file in skill.files}
    actual_paths = {
        item.relative_to(path)
        for item in path.rglob("*")
        if item.is_file()
    }
    if not actual_paths:
        return False
    return actual_paths <= expected_paths


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


def _validate_mutation_targets(
    desired: DesiredState,
    previous_state: BridgeState | None,
    plan: _MutationPlan,
    *,
    state_write_needed: bool,
) -> None:
    """Reject plans whose write or delete targets escape managed roots."""
    for change in plan.changes:
        if change.resource_kind in {"skill", "agent", "prompt", "global_instructions"}:
            _assert_path_contained(
                change.path,
                desired.codex_home,
                label="Managed global target",
            )
            continue
        if change.resource_kind == "plugin_resource":
            _assert_path_contained(
                change.path,
                desired.bridge_home,
                label="Managed plugin resource target",
            )
            continue
        _assert_path_contained(change.path, desired.project_root, label="Managed project target")

    for registry_write in plan.registry_writes:
        _assert_path_contained(
            registry_write.destination,
            registry_write.container,
            label="Registry write target",
        )

    if state_write_needed:
        _assert_path_contained(desired.state_path, desired.bridge_home, label="Write target")


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


def _build_state_record(
    desired: DesiredState,
    previously_managed: frozenset[str] = frozenset(),
) -> BridgeState:
    """Build the desired stable state payload.

    Preserved project files are only included when they were previously
    managed — this distinguishes files the bridge created from files that
    existed before the bridge ran.
    """
    preserved_relatives = {
        _project_relative(desired, path)
        for path in desired.preserved_project_files
    }
    managed_project_files: dict[str, str] = {}
    # Files the bridge is actively writing — hash from desired content
    for path, content in desired.project_files:
        relative = _project_relative(desired, path)
        managed_project_files[relative] = hash_file_content(content)
    # Preserved files — keep tracking only if previously managed
    for rel in sorted(preserved_relatives):
        if rel in previously_managed:
            full_path = desired.project_root / rel
            if full_path.is_symlink():
                # Track symlinked preserved files without a content hash —
                # the bridge does not own the symlink target's content.
                managed_project_files[rel] = ""
            elif full_path.exists():
                managed_project_files[rel] = hash_file_content(full_path.read_bytes())
    managed_project_skill_dirs = tuple(
        sorted(
            _normalize_dir_name(
                skill.install_dir_name,
                label="managed project skill directory",
            )
            for skill in desired.project_skills
        )
    )
    return BridgeState(
        project_root=desired.project_root,
        codex_home=desired.codex_home,
        bridge_home=desired.bridge_home,
        managed_project_files=managed_project_files,
        managed_project_skill_dirs=managed_project_skill_dirs,
    )


def _previously_managed_set(previous_state: BridgeState | None) -> frozenset[str]:
    """Extract the managed project file set from previous state."""
    if previous_state is None:
        return frozenset()
    return frozenset(previous_state.managed_project_files.keys())


def _state_write_needed(
    desired: DesiredState,
    previously_managed: frozenset[str] = frozenset(),
) -> bool:
    """Return True when the project-local state file must be updated."""
    state_bytes = _build_state_record(desired, previously_managed).to_json().encode()
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
        "AGENTS.md",
    }
    normalized_text = normalized.as_posix()
    if normalized_text in allowed_exact:
        return True

    # Agent .toml files under .codex/agents/
    if normalized.parent == AGENTS_RELATIVE_ROOT and normalized.suffix == ".toml":
        return True

    return False


def _validated_managed_project_skill_dirs(previous_state: BridgeState) -> set[str]:
    """Return validated managed project skill directory names from bridge state."""
    managed_project_skill_dirs = set(previous_state.managed_project_skill_dirs)
    invalid_managed_skill_dirs = sorted(
        skill_dir_name
        for skill_dir_name in managed_project_skill_dirs
        if not _is_allowed_managed_project_skill_dir_name(skill_dir_name)
    )
    if invalid_managed_skill_dirs:
        raise ReconcileError(
            "Interop state contains unexpected managed project skill directories: "
            + ", ".join(invalid_managed_skill_dirs)
        )
    return managed_project_skill_dirs


def _is_allowed_managed_project_skill_dir_name(skill_dir_name: str) -> bool:
    """Return True for valid state-tracked project skill directory names."""
    try:
        _normalize_dir_name(
            skill_dir_name,
            label="managed project skill directory",
        )
    except ReconcileError:
        return False

    return True


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


def _normalize_dir_name(value: str, *, label: str) -> str:
    """Normalize one generated directory name and reject traversal or separators."""
    candidate = value
    if not candidate.strip():
        raise ReconcileError(f"{label.capitalize()} may not be empty: {value}")

    normalized = Path(candidate)
    if (
        normalized.is_absolute()
        or candidate != normalized.name
        or candidate != candidate.strip()
        or candidate in {".", ".."}
    ):
        raise ReconcileError(f"{label.capitalize()} must be a plain directory name: {value}")

    return candidate
