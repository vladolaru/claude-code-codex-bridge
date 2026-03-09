"""Desired-state reconcile engine for Codex interop artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import shutil
import tempfile
from typing import Iterable
from uuid import uuid4

from cc_codex_bridge.locking import acquire_global_registry_lock, acquire_project_lock
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


@dataclass(frozen=True)
class _PendingSwap:
    """One staged replacement waiting to be committed."""

    destination: Path
    staged_path: Path
    backup_root: Path
    is_dir: bool


@dataclass(frozen=True)
class _PendingRemoval:
    """One path that should be removed transactionally."""

    destination: Path
    backup_root: Path
    is_dir: bool


@dataclass
class _AppliedChange:
    """One committed path mutation that can be rolled back."""

    destination: Path
    backup_path: Path | None
    is_dir: bool


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
    skills_tuple = tuple(skills)

    if shim_decision.content is not None:
        project_files.append(
            (
                _resolve_managed_project_path(project_root, Path("CLAUDE.md")),
                shim_decision.content.encode(),
            )
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

    _ensure_unique_project_file_paths(project_files)
    _ensure_unique_skill_names(skills_tuple)

    return DesiredState(
        project_root=project_root,
        codex_home=codex_home_path,
        project_files=tuple(project_files),
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
    with acquire_project_lock(desired.project_root):
        with acquire_global_registry_lock(desired.codex_home):
            previous_state = _load_previous_state(desired)
            plan = _plan_mutations(desired, previous_state)
            if not plan.changes and not plan.registry_writes and not _state_write_needed(desired):
                return ReconcileReport(changes=(), applied=True)

            pending_swaps, pending_removals, stage_roots = _stage_transaction(
                desired,
                plan.changes,
                plan.registry_writes,
            )
            applied_changes: list[_AppliedChange] = []
            try:
                _apply_transaction(pending_swaps, pending_removals, applied_changes)
            except Exception:
                _rollback_transaction(applied_changes, desired)
                raise
            else:
                _finalize_transaction(applied_changes, desired)
            finally:
                for stage_root in stage_roots:
                    if stage_root.exists():
                        shutil.rmtree(stage_root)

            return ReconcileReport(changes=plan.changes, applied=True)


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
    for change in report.changes:
        if change.kind not in {"create", "update"}:
            continue
        if change.path.suffix not in {".md", ".toml", ".json"}:
            continue
        desired_bytes = _lookup_desired_file_bytes(desired, change.path)
        if desired_bytes is None:
            continue
        existing_text = change.path.read_text() if change.path.exists() else ""
        desired_text = desired_bytes.decode()
        if existing_text == desired_text:
            continue
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

    desired_project_paths = {_project_relative(desired, path) for path, _ in desired.project_files}
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
        if not registry_owned:
            raise AssertionError("Conflicting adopted skill directories should already fail")
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
    if not snapshot.existed and not updated_registry.skills:
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


def _lookup_desired_file_bytes(desired: DesiredState, path: Path) -> bytes | None:
    """Look up desired bytes for a project file path."""
    for candidate_path, content in desired.project_files:
        if candidate_path == path:
            return content
    return None


def _is_project_path(desired: DesiredState, path: Path) -> bool:
    """Return True when a path is inside the target project."""
    try:
        path.relative_to(desired.project_root)
        return True
    except ValueError:
        return False


def _cleanup_empty_project_dirs(path: Path, stop_at: Path) -> None:
    """Remove empty generated directories up to `.codex`."""
    current = path
    while current != stop_at and current.exists():
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _build_state_record(desired: DesiredState) -> InteropState:
    """Build the desired stable state payload."""
    managed_project_files = tuple(
        sorted(
            (*(_project_relative(desired, path) for path, _ in desired.project_files), STATE_RELATIVE_PATH.as_posix())
        )
    )
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


def _stage_transaction(
    desired: DesiredState,
    changes: tuple[Change, ...],
    registry_writes: tuple[_RegistryWrite, ...],
) -> tuple[tuple[_PendingSwap, ...], tuple[_PendingRemoval, ...], tuple[Path, ...]]:
    """Stage all create/update artifacts before mutating any live outputs."""
    stage_roots: list[Path] = []
    pending_swaps: list[_PendingSwap] = []
    pending_removals: list[_PendingRemoval] = []

    desired_map = dict(desired.project_files)
    project_stage_root = Path(tempfile.mkdtemp(prefix=".interop-project-stage-", dir=desired.project_root))
    stage_roots.append(project_stage_root)
    project_backup_root = Path(tempfile.mkdtemp(prefix=".interop-project-backup-", dir=desired.project_root))
    stage_roots.append(project_backup_root)

    for change in sorted(changes, key=lambda item: str(item.path)):
        if change.resource_kind == "skill":
            continue
        if not _is_project_path(desired, change.path):
            continue
        if change.kind == "remove":
            pending_removals.append(
                _PendingRemoval(
                    destination=change.path,
                    backup_root=project_backup_root,
                    is_dir=False,
                )
            )
            continue
        relative = _project_relative(desired, change.path)
        staged_path = project_stage_root / relative
        _write_staged_file(staged_path, desired_map[change.path])
        pending_swaps.append(
            _PendingSwap(
                destination=change.path,
                staged_path=staged_path,
                backup_root=project_backup_root,
                is_dir=False,
            )
        )

    state_bytes = _build_state_record(desired).to_json().encode()
    if not desired.state_path.exists() or desired.state_path.read_bytes() != state_bytes:
        staged_state_path = project_stage_root / STATE_RELATIVE_PATH
        _write_staged_file(staged_state_path, state_bytes)
        pending_swaps.append(
            _PendingSwap(
                destination=desired.state_path,
                staged_path=staged_state_path,
                backup_root=project_backup_root,
                is_dir=False,
            )
        )

    for registry_write in registry_writes:
        registry_destination = registry_write.destination
        registry_destination.parent.mkdir(parents=True, exist_ok=True)
        registry_stage_root = Path(
            tempfile.mkdtemp(
                prefix=".interop-registry-stage-",
                dir=registry_destination.parent,
            )
        )
        stage_roots.append(registry_stage_root)
        registry_backup_root = Path(
            tempfile.mkdtemp(
                prefix=".interop-registry-backup-",
                dir=registry_destination.parent,
            )
        )
        stage_roots.append(registry_backup_root)
        staged_registry_path = registry_stage_root / registry_destination.name
        _write_staged_file(staged_registry_path, registry_write.content)
        pending_swaps.append(
            _PendingSwap(
                destination=registry_destination,
                staged_path=staged_registry_path,
                backup_root=registry_backup_root,
                is_dir=False,
            )
        )

    skills_root = desired.codex_home / "skills"
    desired.codex_home.mkdir(parents=True, exist_ok=True)
    skill_stage_root = Path(tempfile.mkdtemp(prefix=".interop-skill-stage-", dir=skills_root.parent))
    stage_roots.append(skill_stage_root)
    skill_backup_roots: dict[Path, Path] = {}
    skills_by_name = {skill.install_dir_name: skill for skill in desired.skills}

    def _skill_backup_root_for(destination: Path) -> Path:
        backup_parent = destination.parent.parent
        backup_root = skill_backup_roots.get(backup_parent)
        if backup_root is not None:
            return backup_root

        backup_root = Path(
            tempfile.mkdtemp(prefix=".interop-skill-backup-", dir=backup_parent)
        )
        skill_backup_roots[backup_parent] = backup_root
        stage_roots.append(backup_root)
        return backup_root

    for change in sorted(changes, key=lambda item: str(item.path)):
        if change.resource_kind != "skill":
            continue
        if change.kind == "remove":
            pending_removals.append(
                _PendingRemoval(
                    destination=change.path,
                    backup_root=_skill_backup_root_for(change.path),
                    is_dir=True,
                )
            )
            continue

        skill = skills_by_name[change.path.name]
        staged_dir = skill_stage_root / change.path.name
        staged_dir.mkdir(parents=True, exist_ok=True)
        _write_skill_tree(staged_dir, skill)
        pending_swaps.append(
            _PendingSwap(
                destination=change.path,
                staged_path=staged_dir,
                backup_root=_skill_backup_root_for(change.path),
                is_dir=True,
            )
        )

    return tuple(pending_swaps), tuple(pending_removals), tuple(stage_roots)


def _apply_transaction(
    pending_swaps: tuple[_PendingSwap, ...],
    pending_removals: tuple[_PendingRemoval, ...],
    applied_changes: list[_AppliedChange],
) -> None:
    """Apply all staged replacements and removals with rollback metadata."""
    for swap in pending_swaps:
        backup_path = _swap_path(swap.destination, swap.staged_path, swap.backup_root, is_dir=swap.is_dir)
        applied_changes.append(
            _AppliedChange(
                destination=swap.destination,
                backup_path=backup_path,
                is_dir=swap.is_dir,
            )
        )

    for removal in pending_removals:
        backup_path = _remove_path(removal.destination, removal.backup_root, is_dir=removal.is_dir)
        applied_changes.append(
            _AppliedChange(
                destination=removal.destination,
                backup_path=backup_path,
                is_dir=removal.is_dir,
            )
        )


def _finalize_transaction(applied_changes: list[_AppliedChange], desired: DesiredState) -> None:
    """Discard backups after the transaction has completed successfully."""
    for applied_change in reversed(applied_changes):
        if applied_change.backup_path is None:
            continue
        _remove_existing_path(applied_change.backup_path, is_dir=applied_change.is_dir)
        if not applied_change.is_dir and _is_project_path(desired, applied_change.destination):
            _cleanup_empty_project_dirs(
                applied_change.backup_path.parent,
                desired.project_root,
            )

    for applied_change in applied_changes:
        if applied_change.backup_path is not None:
            continue
        if not applied_change.destination.exists():
            continue
        if applied_change.is_dir or not _is_project_path(desired, applied_change.destination):
            continue
        _cleanup_empty_project_dirs(
            applied_change.destination.parent,
            desired.project_root / ".codex",
        )


def _rollback_transaction(applied_changes: list[_AppliedChange], desired: DesiredState) -> None:
    """Restore the last known good outputs after a failed apply."""
    for applied_change in reversed(applied_changes):
        if applied_change.destination.exists():
            _remove_existing_path(applied_change.destination, is_dir=applied_change.is_dir)
        if applied_change.backup_path is not None and applied_change.backup_path.exists():
            applied_change.destination.parent.mkdir(parents=True, exist_ok=True)
            applied_change.backup_path.rename(applied_change.destination)

    for applied_change in applied_changes:
        if applied_change.is_dir or not _is_project_path(desired, applied_change.destination):
            continue
        if applied_change.backup_path is None:
            _cleanup_empty_project_dirs(
                applied_change.destination.parent,
                desired.project_root / ".codex",
            )
        else:
            _cleanup_empty_project_dirs(
                applied_change.backup_path.parent,
                desired.project_root,
            )


def _swap_path(destination: Path, staged_path: Path, backup_root: Path, *, is_dir: bool) -> Path | None:
    """Replace a live path with a staged one, returning the backup path when replaced."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if destination.exists():
        backup_path = backup_root / uuid4().hex / destination.name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        destination.rename(backup_path)
    staged_path.rename(destination)
    return backup_path


def _remove_path(destination: Path, backup_root: Path, *, is_dir: bool) -> Path | None:
    """Move a live path aside so it can be restored if the transaction fails."""
    if not destination.exists():
        return None
    backup_path = backup_root / uuid4().hex / destination.name
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    destination.rename(backup_path)
    return backup_path


def _write_staged_file(path: Path, content: bytes) -> None:
    """Write one staged file inside a transaction staging directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _remove_existing_path(path: Path, *, is_dir: bool) -> None:
    """Remove an existing file or directory."""
    if is_dir:
        shutil.rmtree(path)
    else:
        path.unlink()


def _resolve_managed_project_path(project_root: Path, relative_path: Path) -> Path:
    """Resolve and validate one generated project-relative output path."""
    normalized = _normalize_relative_path(relative_path, label="managed project output")
    if not _is_allowed_managed_project_relative(normalized.as_posix()):
        raise ReconcileError(f"Unexpected managed project output path: {normalized}")

    candidate = project_root / normalized
    resolved_candidate = candidate.resolve()
    if not _is_relative_to(resolved_candidate, project_root):
        raise ReconcileError(f"Managed project output escapes the project root: {normalized}")
    return candidate


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


def _ensure_unique_project_file_paths(project_files: list[tuple[Path, bytes]]) -> None:
    """Reject duplicate generated project output paths."""
    seen_paths: set[Path] = set()
    for path, _content in project_files:
        if path in seen_paths:
            raise ReconcileError(f"Duplicate generated project file path: {path}")
        seen_paths.add(path)


def _ensure_unique_skill_names(skills: tuple[GeneratedSkill, ...]) -> None:
    """Reject duplicate generated skill install directory names."""
    seen_names: set[str] = set()
    for skill in skills:
        if skill.install_dir_name in seen_names:
            raise ReconcileError(
                f"Duplicate generated Codex skill directory name: {skill.install_dir_name}"
            )
        seen_names.add(skill.install_dir_name)


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return True when `path` is within `root` after resolution."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
