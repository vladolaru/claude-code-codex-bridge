"""Desired-state reconcile engine for Codex interop artifacts."""

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
from cc_codex_bridge.state import InteropState
from cc_codex_bridge.text import read_utf8_text


DEFAULT_CODEX_HOME = Path.home() / ".codex"
STATE_RELATIVE_PATH = Path(".codex") / "claude-code-interop-state.json"
CONFIG_RELATIVE_PATH = Path(".codex") / "config.toml"
PROMPTS_RELATIVE_ROOT = Path(".codex") / "prompts" / "agents"


@dataclass(frozen=True)
class DesiredState:
    """Full desired-state model for one project reconcile."""

    project_root: Path
    codex_home: Path
    project_files: tuple[tuple[Path, bytes], ...]
    preserved_project_files: tuple[Path, ...]
    skills: tuple[GeneratedSkill, ...]
    state_path: Path


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

    return DesiredState(
        project_root=project_root,
        codex_home=codex_home_path,
        project_files=tuple(project_files),
        preserved_project_files=tuple(sorted(set(preserved_project_files), key=str)),
        skills=skills_tuple,
        state_path=project_root / STATE_RELATIVE_PATH,
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


def _apply_changes(desired: DesiredState, plan: _MutationPlan) -> None:
    """Write all planned changes to disk."""
    desired_map = dict(desired.project_files)
    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}

    for change in plan.changes:
        if change.resource_kind == "skill":
            if change.kind in ("create", "update"):
                if change.path.exists():
                    shutil.rmtree(change.path)
                change.path.mkdir(parents=True, exist_ok=True)
                _write_skill_tree(change.path, skills_by_name[change.path.name])
            elif change.kind == "remove":
                if change.path.exists():
                    shutil.rmtree(change.path)
        else:
            if change.kind in ("create", "update"):
                _atomic_write_file(change.path, desired_map[change.path])
            elif change.kind == "remove":
                change.path.unlink(missing_ok=True)
                _cleanup_empty_parents(change.path.parent, desired.project_root / ".codex")

    for registry_write in plan.registry_writes:
        _atomic_write_file(registry_write.destination, registry_write.content)

    state_bytes = _build_state_record(desired).to_json().encode()
    _atomic_write_file(desired.state_path, state_bytes)


def _atomic_write_file(path: Path, content: bytes) -> None:
    """Write a file atomically via temp-file-then-rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".interop-{uuid4().hex}"
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
        if change.path.suffix not in {".md", ".toml", ".json"}:
            continue
        existing_text = (
            read_utf8_text(change.path, label="managed text file", error_type=ReconcileError)
            if change.path.exists()
            else ""
        )
        desired_text = desired_map[change.path].decode()
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
    previous_state: InteropState | None,
) -> _MutationPlan:
    """Plan file, skill, and registry mutations for one reconcile run."""
    project_changes = _compute_project_file_changes(desired, previous_state)
    skill_changes, registry_writes = _plan_skill_mutations(desired, previous_state)
    return _MutationPlan(
        changes=tuple((*project_changes, *skill_changes)),
        registry_writes=registry_writes,
    )


def _compute_project_file_changes(
    desired: DesiredState,
    previous_state: InteropState | None,
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
    previous_state: InteropState | None,
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

    updated_current = GlobalSkillRegistry(skills=dict(current_snapshot.registry.skills))
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
        updated_previous = GlobalSkillRegistry(skills=dict(previous_snapshot.registry.skills))
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


def _write_skill_tree(destination: Path, skill: GeneratedSkill) -> None:
    """Write one staged skill directory tree."""
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


def _load_previous_state(desired: DesiredState) -> InteropState | None:
    """Load state only from a regular file at the expected project path."""
    if desired.state_path.is_symlink():
        raise ReconcileError(f"Refusing to use symlinked interop state file: {desired.state_path}")
    return InteropState.from_path(desired.state_path)


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


def _cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    """Remove empty directories up to stop_at."""
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _build_state_record(desired: DesiredState) -> InteropState:
    """Build the desired stable state payload."""
    managed_project_paths = {
        *(_project_relative(desired, path) for path, _ in desired.project_files),
        *(_project_relative(desired, path) for path in desired.preserved_project_files),
        STATE_RELATIVE_PATH.as_posix(),
    }
    managed_project_files = tuple(sorted(managed_project_paths))
    return InteropState(
        project_root=desired.project_root,
        codex_home=desired.codex_home,
        managed_project_files=managed_project_files,
    )


def _state_write_needed(desired: DesiredState) -> bool:
    """Return True when the project-local state file must be updated."""
    state_bytes = _build_state_record(desired).to_json().encode()
    return not desired.state_path.exists() or desired.state_path.read_bytes() != state_bytes


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

    return normalized.parent == PROMPTS_RELATIVE_ROOT and normalized.suffix == ".md"


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


