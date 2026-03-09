#!/usr/bin/env python3
"""Preflight checks for the maintainer release command."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tomllib
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    """Validate version alignment and git state for a tagged release."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("Usage: release_check.py X.Y.Z")

    requested_version = args[0]
    project_root = Path(__file__).resolve().parents[1]

    _require_release_tooling(project_root)

    pyproject = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    package_version = pyproject["project"]["version"]
    runtime_version = _read_runtime_version(
        project_root / "src" / "cc_codex_bridge" / "__init__.py"
    )

    if package_version != requested_version:
        raise SystemExit(
            f"pyproject.toml version {package_version!r} does not match requested release {requested_version!r}"
        )
    if runtime_version != requested_version:
        raise SystemExit(
            "src/cc_codex_bridge/__init__.py version "
            f"{runtime_version!r} does not match requested release {requested_version!r}"
        )

    status = _run_git(project_root, "status", "--porcelain")
    if status.stdout.strip():
        raise SystemExit("Git worktree is not clean. Commit or stash changes before releasing.")

    branch = _run_git(project_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch == "HEAD":
        raise SystemExit("Detached HEAD is not supported for make release.")
    if branch != "main":
        raise SystemExit(
            f"make release must run from `main`, found branch {branch!r}"
        )

    return 0


def _require_release_tooling(project_root: Path) -> None:
    """Fail fast when the selected interpreter cannot run the maintainer release flow."""
    missing_modules = [
        module_name
        for module_name in ("pytest", "setuptools")
        if importlib.util.find_spec(module_name) is None
    ]
    if not missing_modules:
        return

    missing_list = ", ".join(sorted(missing_modules))
    raise SystemExit(
        "Selected release interpreter "
        f"{sys.executable!r} is missing required modules: {missing_list}. "
        "Local releases are supported from the repository .venv. "
        "Fix with:\n"
        "  python3 -m venv .venv\n"
        "  .venv/bin/python -m pip install -e '.[dev]'"
    )


def _read_runtime_version(path: Path) -> str:
    """Return the package runtime version from __init__.py."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__ = "):
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("Could not find __version__ in src/cc_codex_bridge/__init__.py")


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run one git command from the project root."""
    return subprocess.run(
        ["git", *args],
        cwd=project_root,
        capture_output=True,
        text=True,
        check=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
