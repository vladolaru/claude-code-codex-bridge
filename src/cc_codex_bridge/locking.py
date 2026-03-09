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
    descriptor = _open_lock_descriptor(path, label=label)

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


def _open_lock_descriptor(path: Path, *, label: str) -> int:
    """Open a new lock file, reclaiming same-host stale locks when safe."""
    for attempt in range(2):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            if attempt == 0 and _clear_stale_lock(path):
                continue
            raise ReconcileError(f"{label} lock is already held: {path}") from exc
        except OSError as exc:
            raise ReconcileError(f"Unable to acquire {label.lower()} lock: {path}") from exc

    raise AssertionError(f"Lock acquisition retry exhausted unexpectedly: {path}")


def _clear_stale_lock(path: Path) -> bool:
    """Remove a stale same-host lock file left behind by a dead process."""
    lock_pid, lock_host = _read_lock_identity(path)
    if lock_pid is None or lock_host != socket.gethostname():
        return False
    if _pid_is_running(lock_pid):
        return False

    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _read_lock_identity(path: Path) -> tuple[int | None, str | None]:
    """Read the pid/host pair from one lock file."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, None

    payload: dict[str, str] = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        payload[key.strip()] = value.strip()

    raw_pid = payload.get("pid")
    raw_host = payload.get("host")
    if raw_pid is None or raw_host is None:
        return None, None

    try:
        pid = int(raw_pid)
    except ValueError:
        return None, None

    if pid <= 0:
        return None, None

    return pid, raw_host


def _pid_is_running(pid: int) -> bool:
    """Return True when a process id currently exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True
