"""State tracking for generated Codex interop artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from cc_codex_bridge.model import ReconcileError


STATE_VERSION = 1


@dataclass(frozen=True)
class InteropState:
    """Recorded ownership and source metadata for generated artifacts."""

    project_root: Path
    codex_home: Path
    selected_plugins: tuple[str, ...]
    managed_project_files: tuple[str, ...]
    managed_codex_skill_dirs: tuple[str, ...]
    version: int = STATE_VERSION

    @classmethod
    def from_path(cls, path: Path) -> "InteropState | None":
        """Read a state file if it exists."""
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise ReconcileError(f"Invalid interop state file: {path}") from exc

        if not isinstance(data, dict):
            raise ReconcileError(f"Invalid interop state file: {path}")
        if data.get("version") != STATE_VERSION:
            raise ReconcileError(f"Unsupported interop state version in: {path}")

        return cls(
            project_root=Path(_require_string(data, "project_root", path)),
            codex_home=Path(_require_string(data, "codex_home", path)),
            selected_plugins=tuple(_read_string_list(data, "selected_plugins", path)),
            managed_project_files=tuple(_read_string_list(data, "managed_project_files", path)),
            managed_codex_skill_dirs=tuple(
                _read_string_list(data, "managed_codex_skill_dirs", path)
            ),
            version=STATE_VERSION,
        )

    def to_json(self) -> str:
        """Serialize state deterministically."""
        payload = {
            "version": self.version,
            "project_root": str(self.project_root),
            "codex_home": str(self.codex_home),
            "selected_plugins": list(self.selected_plugins),
            "managed_project_files": list(self.managed_project_files),
            "managed_codex_skill_dirs": list(self.managed_codex_skill_dirs),
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _require_string(data: dict[str, object], key: str, path: Path) -> str:
    """Read one required string field from the state payload."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReconcileError(f"Invalid interop state file: {path}")
    return value


def _read_string_list(data: dict[str, object], key: str, path: Path) -> list[str]:
    """Read one optional string-list field from the state payload."""
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReconcileError(f"Invalid interop state file: {path}")
    return value
