"""Tests for package-level metadata."""

from __future__ import annotations

import tomllib
from importlib import metadata
from pathlib import Path

from cc_codex_bridge import __version__


def test_package_exposes_version():
    """The package exposes a string version for runtime introspection."""
    assert isinstance(__version__, str)
    assert __version__


def test_runtime_version_matches_pyproject_version():
    """The runtime version must stay aligned with package metadata."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert __version__ == project_data["project"]["version"]


def test_installed_distribution_version_matches_runtime_version():
    """Editable and non-editable installs should expose the same version."""
    assert metadata.version("cc-codex-bridge") == __version__


def test_console_script_entrypoint_matches_cli_main():
    """The packaged console script should keep pointing at the CLI entrypoint."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project_data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    assert project_data["project"]["scripts"]["cc-codex-bridge"] == "cc_codex_bridge.cli:main"
