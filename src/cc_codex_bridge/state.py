"""State tracking for generated Codex bridge artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.text import read_utf8_text


STATE_VERSION = 9

# Accept state files from this version onward (migration supported).
_MIN_STATE_VERSION = 8


@dataclass(frozen=True)
class BridgeState:
    """Recorded ownership metadata for generated artifacts."""

    project_root: Path
    codex_home: Path
    bridge_home: Path
    managed_project_files: dict[str, str]
    managed_project_skill_dirs: tuple[str, ...] = ()
    version: int = STATE_VERSION

    @classmethod
    def from_path(cls, path: Path) -> "BridgeState | None":
        """Read a state file if it exists."""
        if not path.exists():
            return None

        try:
            data = json.loads(
                read_utf8_text(path, label="bridge state file", error_type=ReconcileError)
            )
        except json.JSONDecodeError as exc:
            raise ReconcileError(f"Invalid bridge state file: {path}") from exc

        if not isinstance(data, dict):
            raise ReconcileError(f"Invalid bridge state file: {path}")
        file_version = data.get("version")
        if not isinstance(file_version, int) or file_version < _MIN_STATE_VERSION or file_version > STATE_VERSION:
            raise ReconcileError(f"Unsupported bridge state version in: {path}")

        # Migrate managed_project_files based on state version.
        if file_version <= 8:
            # v8 stored managed_project_files as a list of strings.
            raw_files = _read_string_list(data, "managed_project_files", path)
            managed_files = {f: "" for f in raw_files}
        else:
            # v9+ stores managed_project_files as a dict of path -> hash.
            managed_files = _read_string_dict(data, "managed_project_files", path)

        return cls(
            project_root=_read_absolute_path(data, "project_root", path),
            codex_home=_read_absolute_path(data, "codex_home", path),
            bridge_home=_read_absolute_path(data, "bridge_home", path),
            managed_project_files=managed_files,
            managed_project_skill_dirs=tuple(_read_string_list(data, "managed_project_skill_dirs", path)),
            version=STATE_VERSION,
        )

    def to_json(self) -> str:
        """Serialize state deterministically."""
        payload = {
            "version": self.version,
            "project_root": str(self.project_root),
            "codex_home": str(self.codex_home),
            "bridge_home": str(self.bridge_home),
            "managed_project_files": dict(self.managed_project_files),
            "managed_project_skill_dirs": list(self.managed_project_skill_dirs),
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _require_string(data: dict[str, object], key: str, path: Path) -> str:
    """Read one required string field from the state payload."""
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ReconcileError(f"Invalid bridge state file: {path}")
    return value


def _read_absolute_path(data: dict[str, object], key: str, path: Path) -> Path:
    """Read one required absolute path field from the state payload."""
    value = Path(_require_string(data, key, path)).expanduser()
    if not value.is_absolute():
        raise ReconcileError(f"Invalid bridge state file: {path}")
    return value.resolve()


def _read_string_list(data: dict[str, object], key: str, path: Path) -> list[str]:
    """Read one optional string-list field from the state payload."""
    value = data.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ReconcileError(f"Invalid bridge state file: {path}")
    return value


def _read_string_dict(data: dict[str, object], key: str, path: Path) -> dict[str, str]:
    """Read one optional string-to-string dict field from the state payload."""
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ReconcileError(f"Invalid bridge state file: {path}")
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise ReconcileError(f"Invalid bridge state file: {path}")
    return value
