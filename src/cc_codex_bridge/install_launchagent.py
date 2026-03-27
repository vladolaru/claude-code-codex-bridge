"""macOS LaunchAgent rendering and installation for scheduled reconcile runs."""

from __future__ import annotations

import plistlib
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from cc_codex_bridge.model import ReconcileError


DEFAULT_START_INTERVAL = 1800
DEFAULT_LAUNCHAGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
DEFAULT_LOGS_DIR = Path.home() / "Library" / "Logs" / "codex-bridge"
BRIDGE_LABEL_PREFIX = "cc-codex-bridge."
GLOBAL_LAUNCHAGENT_LABEL = "cc-codex-bridge.autosync"


def build_launchagent_label(project_root: str | Path) -> str:
    """Build a deterministic LaunchAgent label from a project path."""
    project_path = Path(project_root).expanduser().resolve()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", project_path.name).strip("-").lower() or "project"
    stable_hash = uuid5(NAMESPACE_URL, str(project_path)).hex[:10]
    return f"{BRIDGE_LABEL_PREFIX}{slug}.{stable_hash}"


def build_launchagent_plist(
    *,
    project_root: str | Path,
    interval_seconds: int = DEFAULT_START_INTERVAL,
    cache_dir: str | Path | None = None,
    claude_home: str | Path | None = None,
    codex_home: str | Path | None = None,
    python_executable: str | Path | None = None,
    cli_path: str | Path | None = None,
    label: str | None = None,
    logs_dir: str | Path | None = None,
) -> bytes:
    """Render a LaunchAgent plist that periodically runs reconcile."""
    project_path = Path(project_root).expanduser().resolve()
    if interval_seconds <= 0:
        raise ReconcileError("LaunchAgent interval must be a positive integer")
    if not (project_path / "AGENTS.md").is_file():
        raise ReconcileError(f"LaunchAgent project root must contain AGENTS.md: {project_path}")

    label_value = label or build_launchagent_label(project_path)
    python_path = str(Path(python_executable or sys.executable).expanduser().resolve())
    cli_script_path = str(Path(cli_path or (Path(__file__).resolve().parent / "cli.py")).resolve())
    log_root = Path(logs_dir or DEFAULT_LOGS_DIR).expanduser().resolve()
    stdout_path = log_root / f"{label_value}.out.log"
    stderr_path = log_root / f"{label_value}.err.log"

    program_arguments = [
        python_path,
        cli_script_path,
        "reconcile",
        "--project",
        str(project_path),
    ]
    if cache_dir is not None:
        program_arguments.extend(["--cache-dir", str(Path(cache_dir).expanduser().resolve())])
    if claude_home is not None:
        program_arguments.extend(["--claude-home", str(Path(claude_home).expanduser().resolve())])
    if codex_home is not None:
        program_arguments.extend(["--codex-home", str(Path(codex_home).expanduser().resolve())])

    payload: dict[str, Any] = {
        "Label": label_value,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(project_path),
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
        "ProcessType": "Background",
    }
    return plistlib.dumps(payload, sort_keys=True)


def build_global_launchagent_plist(
    *,
    interval_seconds: int = DEFAULT_START_INTERVAL,
    python_executable: str | Path | None = None,
    cli_path: str | Path | None = None,
    label: str | None = None,
    logs_dir: str | Path | None = None,
    path_env: str | None = None,
) -> bytes:
    """Render a global LaunchAgent plist that periodically runs reconcile --all.

    ``path_env`` overrides the PATH baked into ``EnvironmentVariables``.
    When omitted the current process PATH is used so that the agent can
    locate the ``claude`` CLI even though macOS LaunchAgents run with a
    stripped PATH.
    """
    import os

    if interval_seconds <= 0:
        raise ReconcileError("LaunchAgent interval must be a positive integer")

    label_value = label or GLOBAL_LAUNCHAGENT_LABEL
    log_root = Path(logs_dir or DEFAULT_LOGS_DIR).expanduser().resolve()
    stdout_path = log_root / f"{label_value}.out.log"
    stderr_path = log_root / f"{label_value}.err.log"

    # Prefer the cc-codex-bridge console script so macOS shows the tool name
    # in background-activity notifications instead of the Python interpreter.
    console_script = cli_path or shutil.which("cc-codex-bridge")
    if console_script:
        program = str(Path(console_script).expanduser().resolve())
        program_arguments = ["cc-codex-bridge", "reconcile", "--all"]
    else:
        # Fallback: invoke cli.py via the Python interpreter directly.
        python_path = str(Path(python_executable or sys.executable).expanduser().resolve())
        cli_script_path = str(Path(__file__).resolve().parent / "cli.py")
        program = python_path
        program_arguments = [python_path, cli_script_path, "reconcile", "--all"]

    # Bake the current PATH into the plist so the agent can find the claude
    # CLI at runtime. macOS LaunchAgents run with a stripped PATH that omits
    # user-level directories like ~/.local/bin, /opt/homebrew/bin, etc.
    effective_path = path_env if path_env is not None else os.environ.get("PATH", "")

    payload: dict[str, Any] = {
        "EnvironmentVariables": {"PATH": effective_path},
        "Label": label_value,
        "ProcessType": "Background",
        "Program": program,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "StandardErrorPath": str(stderr_path),
        "StandardOutPath": str(stdout_path),
        "StartInterval": interval_seconds,
        "WorkingDirectory": str(Path.home()),
    }
    return plistlib.dumps(payload, sort_keys=True)


def install_launchagent(
    plist_bytes: bytes,
    *,
    label: str,
    launchagents_dir: str | Path | None = None,
) -> Path:
    """Write the LaunchAgent plist and load it via launchd.

    If the agent is already loaded, it is booted out first so the new
    plist takes effect immediately. Returns the installed plist path.
    """
    import os
    import subprocess

    root = Path(launchagents_dir or DEFAULT_LAUNCHAGENTS_DIR).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{label}.plist"

    # Boot out any running instance before replacing the plist.
    if destination.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(destination)],
            check=False, capture_output=True,
        )

    with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
        handle.write(plist_bytes)
        temp_path = Path(handle.name)
    temp_path.replace(destination)

    subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(destination)],
        check=False, capture_output=True,
    )

    return destination


def uninstall_launchagent(
    label: str,
    *,
    launchagents_dir: str | Path | None = None,
    dry_run: bool = False,
) -> Path | None:
    """Bootout and remove a bridge LaunchAgent plist.

    Returns the plist path if it existed, None if it was not found.
    When dry_run is True, reports what would be done without modifying anything.
    """
    import os
    import subprocess

    root = Path(launchagents_dir or DEFAULT_LAUNCHAGENTS_DIR).expanduser().resolve()
    plist_path = root / f"{label}.plist"
    if not plist_path.exists():
        return None
    if dry_run:
        return plist_path
    uid = os.getuid()
    subprocess.run(
        ["launchctl", "bootout", f"gui/{uid}", str(plist_path)],
        check=False,
        capture_output=True,
    )
    plist_path.unlink()
    return plist_path


def find_bridge_launchagents(
    *,
    launchagents_dir: str | Path | None = None,
) -> tuple[Path, ...]:
    """Return paths of bridge LaunchAgent plists in the given directory."""
    root = Path(launchagents_dir or DEFAULT_LAUNCHAGENTS_DIR).expanduser().resolve()
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            p for p in root.iterdir()
            if p.is_file()
            and p.name.startswith(BRIDGE_LABEL_PREFIX)
            and p.name.endswith(".plist")
        )
    )
