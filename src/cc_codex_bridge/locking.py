"""Exclusive file locks for reconcile mutations."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import socket

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import GLOBAL_REGISTRY_LOCK_FILENAME


PROJECT_LOCK_RELATIVE_PATH = Path(".codex") / "claude-code-interop.lock"


def project_lock_path(project_root: Path) -> Path:
    """Return the reconcile lock path for one project root."""
    return project_root / PROJECT_LOCK_RELATIVE_PATH


def global_registry_lock_path(codex_home: Path) -> Path:
    """Return the reconcile lock path for one global registry."""
    return codex_home / GLOBAL_REGISTRY_LOCK_FILENAME


@contextmanager
def acquire_project_lock(project_root: Path):
    """Acquire the per-project reconcile lock."""
    with _acquire_lock(
        project_lock_path(project_root),
        label="Project reconcile",
    ) as lock_path:
        yield lock_path


@contextmanager
def acquire_global_registry_lock(codex_home: Path):
    """Acquire the global generated-skill registry lock."""
    with _acquire_lock(
        global_registry_lock_path(codex_home),
        label="Global skill registry",
    ) as lock_path:
        yield lock_path


@contextmanager
def _acquire_lock(path: Path, *, label: str):
    """Acquire one exclusive lock file or raise a reconcile error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise ReconcileError(f"{label} lock is already held: {path}") from exc
    except OSError as exc:
        raise ReconcileError(f"Unable to acquire {label.lower()} lock: {path}") from exc

    try:
        payload = f"pid={os.getpid()}\nhost={socket.gethostname()}\n".encode()
        os.write(descriptor, payload)
        os.close(descriptor)
        descriptor = None
        yield path
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
