"""Tests for the maintainer release preflight helper."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess

import pytest

from cc_codex_bridge import __version__


def _load_release_check_module():
    """Load the standalone packaging/release_check.py module for direct tests."""
    path = Path(__file__).resolve().parents[1] / "packaging" / "release_check.py"
    spec = importlib.util.spec_from_file_location("release_check_module", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_check_requires_main_branch(monkeypatch: pytest.MonkeyPatch):
    """The maintainer release preflight should only allow releases from main."""
    module = _load_release_check_module()

    monkeypatch.setattr(module, "_read_runtime_version", lambda _path: __version__)

    def fake_run_git(_project_root: Path, *args: str):
        if args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return subprocess.CompletedProcess(args, 0, stdout="feature/release\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(module, "_run_git", fake_run_git)

    with pytest.raises(SystemExit, match="must run from `main`"):
        module.main([__version__])


def test_release_check_accepts_clean_main_branch(monkeypatch: pytest.MonkeyPatch):
    """A clean main branch with aligned versions should pass preflight."""
    module = _load_release_check_module()

    monkeypatch.setattr(module, "_read_runtime_version", lambda _path: __version__)

    def fake_run_git(_project_root: Path, *args: str):
        if args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return subprocess.CompletedProcess(args, 0, stdout="main\n", stderr="")
        raise AssertionError(args)

    monkeypatch.setattr(module, "_run_git", fake_run_git)

    assert module.main([__version__]) == 0
