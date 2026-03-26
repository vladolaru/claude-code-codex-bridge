"""Machine-level environment checks for installed CLI usage."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import sys
from typing import Iterable

import cc_codex_bridge.discover as discover_module
import cc_codex_bridge.install_launchagent as launchagent_module
from cc_codex_bridge.model import SemVer


DEFAULT_CODEX_HOME = Path.home() / ".codex"


@dataclass(frozen=True)
class DoctorCheck:
    """One machine-level environment check."""

    name: str
    status: str
    message: str


def run_doctor(
    *,
    cache_dir: str | Path | None = None,
    claude_home: str | Path | None = None,
    codex_home: str | Path | None = None,
    launchagents_dir: str | Path | None = None,
    command_name: str = "cc-codex-bridge",
    python_executable: str | Path | None = None,
    python_version: tuple[int, ...] | None = None,
    path_env: str | None = None,
    bridge_home: str | Path | None = None,
) -> tuple[DoctorCheck, ...]:
    """Run the machine-level doctor checks."""
    resolved_python = Path(python_executable or sys.executable).expanduser().resolve()
    version_info = tuple(python_version or tuple(sys.version_info[:3]))
    resolved_cache_dir = discover_module._resolve_cache_dir(
        cache_dir, claude_home
    )
    resolved_codex_home = Path(codex_home or DEFAULT_CODEX_HOME).expanduser().resolve()
    resolved_launchagents_dir = Path(
        launchagents_dir or launchagent_module.DEFAULT_LAUNCHAGENTS_DIR
    ).expanduser().resolve()
    effective_path = path_env if path_env is not None else os.environ.get("PATH", "")

    from cc_codex_bridge.bridge_home import resolve_bridge_home
    resolved_bridge_home = Path(bridge_home).expanduser().resolve() if bridge_home else resolve_bridge_home()

    return (
        _check_python(resolved_python, version_info),
        _check_claude_cli(),
        _check_claude_cache(resolved_cache_dir),
        _check_codex_home(resolved_codex_home),
        _check_launchagents_dir(resolved_launchagents_dir),
        _check_command_on_path(command_name, effective_path),
        _check_config(resolved_bridge_home),
    )


def overall_status(checks: Iterable[DoctorCheck]) -> str:
    """Summarize the overall doctor status."""
    materialized = tuple(checks)
    if any(check.status == "error" for check in materialized):
        return "error"
    if any(check.status == "warning" for check in materialized):
        return "warning"
    return "ok"


def doctor_exit_code(checks: Iterable[DoctorCheck]) -> int:
    """Return a process exit code for the doctor result."""
    return 1 if overall_status(checks) == "error" else 0


def format_doctor_report(checks: Iterable[DoctorCheck]) -> str:
    """Render doctor checks in a stable human-readable form."""
    from cc_codex_bridge import __version__
    from cc_codex_bridge._colors import color_fns

    materialized = tuple(checks)
    c = color_fns()
    status = overall_status(materialized)
    if status == "ok":
        colored_status = c["good"](status)
    elif status == "warning":
        colored_status = c["warn"](status)
    else:
        colored_status = c["bad"](status)

    n_ok = sum(1 for check in materialized if check.status == "ok")
    n_warn = sum(1 for check in materialized if check.status == "warning")
    n_err = sum(1 for check in materialized if check.status == "error")

    lines = [
        "",
        f"{c['key']('VERSION:')} v{__version__}",
        f"{c['key']('STATUS:')} {colored_status}",
        f"{c['key']('CHECKS_OK:')} {c['good'](str(n_ok)) if n_ok else str(n_ok)}",
        f"{c['key']('CHECKS_WARNING:')} {c['warn'](str(n_warn)) if n_warn else str(n_warn)}",
        f"{c['key']('CHECKS_ERROR:')} {c['bad'](str(n_err)) if n_err else str(n_err)}",
    ]
    for check in materialized:
        if check.status == "ok":
            status_s = c["good"](check.status)
        elif check.status == "warning":
            status_s = c["warn"](check.status)
        else:
            status_s = c["bad"](check.status)
        lines.append(
            f"{c['key']('CHECK:')} {check.name} status={status_s} message={check.message}"
        )
    return "\n".join(lines)


def format_doctor_json(checks: Iterable[DoctorCheck]) -> str:
    """Render doctor checks as deterministic JSON."""
    from cc_codex_bridge import __version__

    materialized = tuple(checks)
    return json.dumps(
        {
            "version": __version__,
            "status": overall_status(materialized),
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "message": check.message,
                }
                for check in materialized
            ],
        },
        indent=2,
        sort_keys=True,
    )


def _check_python(
    python_executable: Path,
    version_info: tuple[int, ...],
) -> DoctorCheck:
    """Verify the interpreter meets the package floor."""
    rendered_version = ".".join(str(part) for part in version_info[:3])
    if version_info[:2] < (3, 11):
        return DoctorCheck(
            name="python",
            status="error",
            message=(
                f"Python 3.11+ is required, found {rendered_version} at {python_executable}"
            ),
        )
    return DoctorCheck(
        name="python",
        status="ok",
        message=f"Using Python {rendered_version} at {python_executable}",
    )


def _check_claude_cli() -> DoctorCheck:
    """Check that the Claude CLI is available on PATH."""
    claude_path = shutil.which("claude")
    if claude_path is None:
        return DoctorCheck(
            name="claude_cli",
            status="error",
            message=(
                "Claude CLI not found on PATH. "
                "The bridge requires Claude Code to be installed. "
                "See https://docs.anthropic.com/en/docs/claude-code"
            ),
        )
    return DoctorCheck(
        name="claude_cli",
        status="ok",
        message=f"Claude CLI found at {claude_path}",
    )


def _check_claude_cache(cache_dir: Path) -> DoctorCheck:
    """Check that the Claude plugin cache path looks usable."""
    if not cache_dir.exists():
        return DoctorCheck(
            name="claude_cache",
            status="warning",
            message=(
                f"Claude plugin cache not found at {cache_dir}; install Claude plugins before reconcile"
            ),
        )
    if not cache_dir.is_dir():
        return DoctorCheck(
            name="claude_cache",
            status="warning",
            message=f"Claude plugin cache path is not a directory: {cache_dir}",
        )

    try:
        plugin_count = 0
        malformed_plugins: list[str] = []
        for marketplace_dir in sorted(cache_dir.iterdir()):
            if not marketplace_dir.is_dir():
                continue
            for plugin_dir in sorted(marketplace_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                has_valid_version = any(
                    _is_valid_semver(version_dir.name)
                    for version_dir in plugin_dir.iterdir()
                    if version_dir.is_dir()
                )
                if has_valid_version:
                    plugin_count += 1
                else:
                    malformed_plugins.append(
                        f"{marketplace_dir.name}/{plugin_dir.name}"
                    )
    except OSError as exc:
        return DoctorCheck(
            name="claude_cache",
            status="warning",
            message=f"Unable to inspect Claude plugin cache at {cache_dir}: {exc}",
        )

    if malformed_plugins:
        return DoctorCheck(
            name="claude_cache",
            status="warning",
            message=(
                f"Claude plugin cache at {cache_dir} has plugins with no valid "
                f"semantic versions: {', '.join(malformed_plugins)}; "
                f"discovery will fail for these plugins"
            ),
        )

    if plugin_count == 0:
        return DoctorCheck(
            name="claude_cache",
            status="warning",
            message=f"Claude plugin cache is present but empty at {cache_dir}",
        )

    return DoctorCheck(
        name="claude_cache",
        status="ok",
        message=f"Claude plugin cache is readable at {cache_dir} ({plugin_count} plugin directories)",
    )


def _check_codex_home(codex_home: Path) -> DoctorCheck:
    """Check that the Codex home path is writable or creatable."""
    return _check_writable_location(
        name="codex_home",
        path=codex_home,
        missing_message="Codex home can be created",
        present_message="Codex home is writable",
        failure_status="error",
    )


def _check_launchagents_dir(launchagents_dir: Path) -> DoctorCheck:
    """Check that the LaunchAgents directory is writable or creatable."""
    return _check_writable_location(
        name="launchagents_dir",
        path=launchagents_dir,
        missing_message="LaunchAgents directory can be created",
        present_message="LaunchAgents directory is writable",
        failure_status="warning",
    )


def _check_writable_location(
    *,
    name: str,
    path: Path,
    missing_message: str,
    present_message: str,
    failure_status: str,
) -> DoctorCheck:
    """Validate a path that should be writable or safely creatable."""
    if path.exists():
        if not path.is_dir():
            return DoctorCheck(
                name=name,
                status=failure_status,
                message=f"Path exists but is not a directory: {path}",
            )
        if _directory_is_writable(path):
            return DoctorCheck(
                name=name,
                status="ok",
                message=f"{present_message} at {path}",
            )
        return DoctorCheck(
            name=name,
            status=failure_status,
            message=f"Directory is not writable: {path}",
        )

    anchor = _existing_parent(path)
    if anchor is None:
        return DoctorCheck(
            name=name,
            status=failure_status,
            message=f"No existing parent directory is available for {path}",
        )
    if not anchor.is_dir():
        return DoctorCheck(
            name=name,
            status=failure_status,
            message=f"Existing parent path is not a directory: {anchor}",
        )
    if not _directory_is_writable(anchor):
        return DoctorCheck(
            name=name,
            status=failure_status,
            message=f"Parent directory is not writable for {path}: {anchor}",
        )
    return DoctorCheck(
        name=name,
        status="ok",
        message=f"{missing_message} at {path}",
    )


def _existing_parent(path: Path) -> Path | None:
    """Return the nearest existing parent for a path."""
    candidate = path
    while not candidate.exists():
        if candidate == candidate.parent:
            return None
        candidate = candidate.parent
    return candidate


def _directory_is_writable(path: Path) -> bool:
    """Return whether a directory accepts a small probe write."""
    probe = path / ".cc-codex-bridge-doctor-write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return False
    return True


def _is_valid_semver(name: str) -> bool:
    """Return True if a directory name is a valid semantic version.

    Uses the same SemVer parser as discovery to avoid accepting names
    that discovery would reject.
    """
    try:
        SemVer.parse(name)
        return True
    except ValueError:
        return False


def _check_command_on_path(command_name: str, path_env: str) -> DoctorCheck:
    """Check whether the installed command is reachable through PATH."""
    resolved = shutil.which(command_name, path=path_env)
    if resolved is None:
        return DoctorCheck(
            name="command_path",
            status="warning",
            message=f"`{command_name}` is not currently discoverable on PATH",
        )
    return DoctorCheck(
        name="command_path",
        status="ok",
        message=f"`{command_name}` resolves to {Path(resolved).resolve()}",
    )


def _check_config(bridge_home: Path) -> DoctorCheck:
    """Check that the global bridge config is valid."""
    from cc_codex_bridge.config_check import check_global_config

    config_path = bridge_home / "config.toml"
    results = check_global_config(config_path, bridge_home=bridge_home)
    failures = [r for r in results if not r.passed]

    if failures:
        messages = "; ".join(f"{r.label}: {r.message}" for r in failures)
        return DoctorCheck(
            name="config",
            status="warning",
            message=f"Config issues: {messages}",
        )

    return DoctorCheck(
        name="config",
        status="ok",
        message=f"Global config is valid at {config_path}",
    )
