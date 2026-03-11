"""State tracking for generated Codex bridge artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.text import read_utf8_text


STATE_VERSION = 3


@dataclass(frozen=True)
class BridgeState:
    """Recorded ownership metadata for generated artifacts."""

    project_root: Path
    codex_home: Path
    managed_project_files: tuple[str, ...]
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
        if data.get("version") != STATE_VERSION:
            raise ReconcileError(f"Unsupported bridge state version in: {path}")

        return cls(
            project_root=_read_absolute_path(data, "project_root", path),
            codex_home=_read_absolute_path(data, "codex_home", path),
            managed_project_files=tuple(_read_string_list(data, "managed_project_files", path)),
            version=STATE_VERSION,
        )

    def to_json(self) -> str:
        """Serialize state deterministically."""
        payload = {
            "version": self.version,
            "project_root": str(self.project_root),
            "codex_home": str(self.codex_home),
            "managed_project_files": list(self.managed_project_files),
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
