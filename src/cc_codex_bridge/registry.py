"""Global generated-skill registry models and deterministic hash helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable

from cc_codex_bridge.model import GeneratedSkill, GeneratedSkillFile, ReconcileError
from cc_codex_bridge.text import read_utf8_text


GLOBAL_REGISTRY_VERSION = 1
GLOBAL_REGISTRY_FILENAME = "claude-code-bridge-global-state.json"


@dataclass(frozen=True)
class GlobalSkillEntry:
    """One generated-skill ownership record."""

    content_hash: str
    owners: tuple[Path, ...]


@dataclass(frozen=True)
class GlobalAgentEntry:
    """One generated-agent ownership record."""

    content_hash: str
    owners: tuple[Path, ...]


@dataclass(frozen=True)
class GlobalSkillRegistry:
    """Validated global generated-skill and agent ownership registry."""

    skills: dict[str, GlobalSkillEntry]
    projects: tuple[Path, ...] = ()
    agents: dict[str, GlobalAgentEntry] = None  # type: ignore[assignment]
    version: int = GLOBAL_REGISTRY_VERSION

    def __post_init__(self) -> None:
        if self.agents is None:
            object.__setattr__(self, "agents", {})

    @classmethod
    def from_path(cls, path: Path) -> "GlobalSkillRegistry | None":
        """Read one registry file when present."""
        if not path.exists():
            return None

        try:
            data = json.loads(
                read_utf8_text(path, label="global skill registry file", error_type=ReconcileError)
            )
        except json.JSONDecodeError as exc:
            raise ReconcileError(f"Invalid global skill registry file: {path}") from exc

        if not isinstance(data, dict):
            raise ReconcileError(f"Invalid global skill registry file: {path}")
        if data.get("version") != GLOBAL_REGISTRY_VERSION:
            raise ReconcileError(f"Unsupported global skill registry version in: {path}")

        raw_skills = data.get("skills", {})
        if not isinstance(raw_skills, dict):
            raise ReconcileError(f"Invalid global skill registry file: {path}")

        skills: dict[str, GlobalSkillEntry] = {}
        for skill_name, raw_entry in raw_skills.items():
            normalized_skill_name = _require_skill_dir_name(skill_name, path)
            if not isinstance(raw_entry, dict):
                raise ReconcileError(f"Invalid global skill registry file: {path}")
            content_hash = _require_content_hash(raw_entry, path)
            owners = tuple(
                sorted(
                    _read_owner_path_list(raw_entry, "owners", path),
                    key=str,
                )
            )
            skills[normalized_skill_name] = GlobalSkillEntry(
                content_hash=content_hash,
                owners=owners,
            )

        raw_projects = data.get("projects", [])
        if not isinstance(raw_projects, list) or any(
            not isinstance(item, str) for item in raw_projects
        ):
            raise ReconcileError(f"Invalid global skill registry file: {path}")

        projects: list[Path] = []
        for raw_project in raw_projects:
            project_path = Path(raw_project).expanduser()
            if not project_path.is_absolute():
                raise ReconcileError(f"Invalid global skill registry file: {path}")
            projects.append(project_path.resolve())

        raw_agents = data.get("agents", {})
        if not isinstance(raw_agents, dict):
            raise ReconcileError(f"Invalid global skill registry file: {path}")

        agents: dict[str, GlobalAgentEntry] = {}
        for agent_filename, raw_agent_entry in raw_agents.items():
            normalized_agent_filename = _require_agent_filename(agent_filename, path)
            if not isinstance(raw_agent_entry, dict):
                raise ReconcileError(f"Invalid global skill registry file: {path}")
            agent_content_hash = _require_content_hash(raw_agent_entry, path)
            agent_owners = tuple(
                sorted(
                    _read_owner_path_list(raw_agent_entry, "owners", path),
                    key=str,
                )
            )
            agents[normalized_agent_filename] = GlobalAgentEntry(
                content_hash=agent_content_hash,
                owners=agent_owners,
            )

        return cls(
            skills=skills,
            projects=tuple(sorted(projects, key=str)),
            agents=agents,
            version=GLOBAL_REGISTRY_VERSION,
        )

    def to_json(self) -> str:
        """Serialize the registry deterministically."""
        payload: dict[str, object] = {
            "version": self.version,
            "agents": {
                agent_filename: {
                    "content_hash": entry.content_hash,
                    "owners": [str(owner) for owner in sorted(entry.owners, key=str)],
                }
                for agent_filename, entry in sorted(self.agents.items())
            },
            "projects": sorted(str(p) for p in self.projects),
            "skills": {
                skill_name: {
                    "content_hash": entry.content_hash,
                    "owners": [str(owner) for owner in sorted(entry.owners, key=str)],
                }
                for skill_name, entry in sorted(self.skills.items())
            },
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def hash_agent_file(content: str) -> str:
    """Return the deterministic content hash for one generated agent .toml file."""
    digest = hashlib.sha256()
    encoded = content.encode("utf-8")
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    return f"sha256:{digest.hexdigest()}"


def hash_generated_skill(skill: GeneratedSkill) -> str:
    """Return the deterministic content hash for one generated skill tree."""
    return hash_generated_skill_files(skill.files)


def hash_generated_skill_files(files: Iterable[GeneratedSkillFile]) -> str:
    """Return a stable content hash for a generated skill file set."""
    digest = hashlib.sha256()
    for generated_file in sorted(files, key=lambda item: item.relative_path.as_posix()):
        relative_path = _normalize_skill_relative_path(generated_file.relative_path).as_posix().encode(
            "utf-8"
        )
        digest.update(len(relative_path).to_bytes(8, "big"))
        digest.update(relative_path)
        digest.update(generated_file.mode.to_bytes(4, "big"))
        digest.update(len(generated_file.content).to_bytes(8, "big"))
        digest.update(generated_file.content)
    return f"sha256:{digest.hexdigest()}"


def _require_content_hash(data: dict[str, object], path: Path) -> str:
    """Read and validate one content hash field."""
    value = data.get("content_hash")
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) <= len("sha256:"):
        raise ReconcileError(f"Invalid global skill registry file: {path}")
    return value


def _read_owner_path_list(data: dict[str, object], key: str, path: Path) -> list[Path]:
    """Read one required owner-path list from the registry payload."""
    value = data.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    owners = [_read_absolute_path(item, path) for item in value]
    if len(set(owners)) != len(owners):
        raise ReconcileError(f"Invalid global skill registry file: {path}")
    return owners


def _read_absolute_path(value: str, path: Path) -> Path:
    """Read one absolute owner path from the registry payload."""
    owner = Path(value).expanduser()
    if not owner.is_absolute():
        raise ReconcileError(f"Invalid global skill registry file: {path}")
    return owner.resolve()


def _require_skill_dir_name(value: str, path: Path) -> str:
    """Validate one generated skill directory name."""
    candidate = value.strip()
    if not candidate:
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    normalized = Path(candidate)
    if normalized.is_absolute() or candidate != normalized.name or candidate in {".", ".."}:
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    return candidate


def _require_agent_filename(value: str, path: Path) -> str:
    """Validate one generated agent .toml filename."""
    candidate = value.strip()
    if not candidate:
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    normalized = Path(candidate)
    if normalized.is_absolute() or candidate != normalized.name or candidate in {".", ".."}:
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    if not candidate.endswith(".toml"):
        raise ReconcileError(f"Invalid global skill registry file: {path}")

    return candidate


def _normalize_skill_relative_path(path: Path) -> Path:
    """Normalize one generated skill-relative file path."""
    candidate = Path(path)
    if candidate.is_absolute():
        raise ReconcileError(f"Generated skill file path must be relative: {candidate}")

    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise ReconcileError(
                f"Generated skill file path may not contain parent traversal: {candidate}"
            )
        normalized_parts.append(part)

    if not normalized_parts:
        raise ReconcileError(f"Generated skill file path may not be empty: {candidate}")
    return Path(*normalized_parts)
