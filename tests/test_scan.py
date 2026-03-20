"""Tests for scan config loading, glob expansion, structural filtering, and scanning."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cc_codex_bridge.scan import (
    SCAN_CONFIG_FILENAME,
    ScanCandidate,
    ScanConfig,
    ScanResult,
    expand_scan_globs,
    filter_scan_candidates,
    load_scan_config,
    scan_for_projects,
)
from cc_codex_bridge.model import ReconcileError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_git_repo(path: Path) -> Path:
    """Create a directory with a .git/ directory (real git root)."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return path


def _make_git_submodule(path: Path) -> Path:
    """Create a directory with a .git file pointing to a parent modules dir."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text("gitdir: ../.git/modules/submod\n")
    return path


def _make_git_worktree(path: Path) -> Path:
    """Create a directory with a .git file pointing to a parent worktrees dir."""
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").write_text("gitdir: /some/repo/.git/worktrees/wt\n")
    return path


def test_missing_file_returns_empty_config(tmp_path: Path):
    """When config.toml does not exist, return empty ScanConfig."""
    config = load_scan_config(tmp_path)

    assert config.scan_paths == ()
    assert config.exclude_paths == ()


def test_valid_config_with_both_fields(tmp_path: Path):
    """Both scan_paths and exclude_paths are read when present."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = ["~/Work/a8c", "/opt/projects"]\n'
        'exclude_paths = ["~/Work/a8c/archive"]\n'
    )

    config = load_scan_config(tmp_path)

    assert config.scan_paths == ("~/Work/a8c", "/opt/projects")
    assert config.exclude_paths == ("~/Work/a8c/archive",)


def test_empty_lists(tmp_path: Path):
    """Empty lists produce empty tuples."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = []\n'
        'exclude_paths = []\n'
    )

    config = load_scan_config(tmp_path)

    assert config.scan_paths == ()
    assert config.exclude_paths == ()


def test_missing_exclude_paths_defaults_to_empty(tmp_path: Path):
    """When only scan_paths is specified, exclude_paths defaults to empty."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = ["/projects"]\n'
    )

    config = load_scan_config(tmp_path)

    assert config.scan_paths == ("/projects",)
    assert config.exclude_paths == ()


def test_missing_both_fields_returns_empty(tmp_path: Path):
    """When file exists but has no scan fields, return empty config."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text('# just a comment\n')

    config = load_scan_config(tmp_path)

    assert config.scan_paths == ()
    assert config.exclude_paths == ()


def test_unknown_keys_ignored(tmp_path: Path):
    """Unknown keys are silently ignored."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = ["/a"]\n'
        'future_key = true\n'
        'unknown_field = 42\n'
    )

    config = load_scan_config(tmp_path)

    assert config.scan_paths == ("/a",)
    assert config.exclude_paths == ()


def test_malformed_toml_raises_reconcile_error(tmp_path: Path):
    """Malformed TOML raises ReconcileError."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text('scan_paths = [unterminated\n')

    with pytest.raises(ReconcileError, match="Invalid TOML"):
        load_scan_config(tmp_path)


def test_bad_scan_paths_type_not_a_list(tmp_path: Path):
    """scan_paths as a non-list type raises ReconcileError."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = "/should/be/a/list"\n'
    )

    with pytest.raises(ReconcileError, match="scan_paths.*list of strings"):
        load_scan_config(tmp_path)


def test_bad_exclude_paths_type_not_a_list(tmp_path: Path):
    """exclude_paths as a non-list type raises ReconcileError."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'exclude_paths = 123\n'
    )

    with pytest.raises(ReconcileError, match="exclude_paths.*list of strings"):
        load_scan_config(tmp_path)


def test_bad_item_type_list_of_ints(tmp_path: Path):
    """List of non-string items raises ReconcileError."""
    config_path = tmp_path / SCAN_CONFIG_FILENAME
    config_path.write_text(
        'scan_paths = [1, 2, 3]\n'
    )

    with pytest.raises(ReconcileError, match="scan_paths.*list of strings"):
        load_scan_config(tmp_path)


def test_scan_config_is_frozen():
    """ScanConfig instances are immutable."""
    config = ScanConfig()

    with pytest.raises(AttributeError):
        config.scan_paths = ("changed",)  # type: ignore[misc]


def test_scan_config_filename_constant():
    """The config filename constant has the expected value."""
    assert SCAN_CONFIG_FILENAME == "config.toml"


# ===================================================================
# Task 2: expand_scan_globs
# ===================================================================


class TestExpandScanGlobs:
    """Tests for expand_scan_globs()."""

    def test_basic_expansion_directories_match_files_dont(self, tmp_path: Path):
        """Only directories are collected, not files."""
        (tmp_path / "project_a").mkdir()
        (tmp_path / "project_b").mkdir()
        (tmp_path / "readme.txt").write_text("hi")

        result = expand_scan_globs(
            scan_paths=(str(tmp_path / "*"),),
            exclude_paths=(),
        )

        resolved_names = {p.name for p in result}
        assert "project_a" in resolved_names
        assert "project_b" in resolved_names
        assert "readme.txt" not in resolved_names

    def test_tilde_expansion(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """~ is expanded via Path.expanduser()."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        (fake_home / "myproject").mkdir()

        monkeypatch.setenv("HOME", str(fake_home))

        result = expand_scan_globs(
            scan_paths=("~/myproject",),
            exclude_paths=(),
        )

        assert len(result) == 1
        assert result[0] == (fake_home / "myproject").absolute()

    def test_exclude_exact_match(self, tmp_path: Path):
        """An exact exclude path removes the matching candidate."""
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()

        result = expand_scan_globs(
            scan_paths=(str(tmp_path / "*"),),
            exclude_paths=(str(tmp_path / "beta"),),
        )

        names = {p.name for p in result}
        assert "alpha" in names
        assert "beta" not in names

    def test_exclude_glob_pattern(self, tmp_path: Path):
        """Exclude glob patterns remove matching candidates."""
        (tmp_path / "proj-a").mkdir()
        (tmp_path / "proj-b").mkdir()
        (tmp_path / "archive-old").mkdir()

        result = expand_scan_globs(
            scan_paths=(str(tmp_path / "*"),),
            exclude_paths=(str(tmp_path / "archive-*"),),
        )

        names = {p.name for p in result}
        assert "proj-a" in names
        assert "proj-b" in names
        assert "archive-old" not in names

    def test_no_matches_returns_empty(self, tmp_path: Path):
        """When no directories match, return an empty list."""
        result = expand_scan_globs(
            scan_paths=(str(tmp_path / "nonexistent-*"),),
            exclude_paths=(),
        )

        assert result == []

    def test_multiple_scan_patterns_union_dedup(self, tmp_path: Path):
        """Multiple scan patterns produce a union with no duplicates."""
        (tmp_path / "shared").mkdir()
        (tmp_path / "only_a").mkdir()
        (tmp_path / "only_b").mkdir()

        result = expand_scan_globs(
            scan_paths=(
                str(tmp_path / "shared"),
                str(tmp_path / "only_a"),
                str(tmp_path / "shared"),  # duplicate
                str(tmp_path / "only_b"),
            ),
            exclude_paths=(),
        )

        names = [p.name for p in result]
        assert names.count("shared") == 1
        assert set(names) == {"shared", "only_a", "only_b"}

    def test_deterministic_output_sorted(self, tmp_path: Path):
        """Output is always sorted by resolved path."""
        for name in ("zeta", "alpha", "mu", "beta"):
            (tmp_path / name).mkdir()

        result = expand_scan_globs(
            scan_paths=(str(tmp_path / "*"),),
            exclude_paths=(),
        )

        assert result == sorted(result)


# ===================================================================
# Task 3: filter_scan_candidates
# ===================================================================


class TestFilterScanCandidates:
    """Tests for filter_scan_candidates()."""

    def test_regular_repo_with_agents_md_bridgeable(self, tmp_path: Path):
        """A git repo with AGENTS.md is bridgeable."""
        repo = _make_git_repo(tmp_path / "myrepo")
        (repo / "AGENTS.md").write_text("# agents")

        result = filter_scan_candidates([repo])

        assert len(result.bridgeable) == 1
        assert result.bridgeable[0] == repo
        assert result.not_bridgeable == ()
        assert result.filtered == ()

    def test_regular_repo_with_claude_md_bridgeable(self, tmp_path: Path):
        """A git repo with CLAUDE.md is bridgeable."""
        repo = _make_git_repo(tmp_path / "myrepo")
        (repo / "CLAUDE.md").write_text("# claude")

        result = filter_scan_candidates([repo])

        assert len(result.bridgeable) == 1
        assert result.bridgeable[0] == repo

    def test_regular_repo_with_dot_claude_only_not_bridgeable(self, tmp_path: Path):
        """A git repo with only .claude/ dir is not_bridgeable."""
        repo = _make_git_repo(tmp_path / "myrepo")
        (repo / ".claude").mkdir()

        result = filter_scan_candidates([repo])

        assert result.bridgeable == ()
        assert len(result.not_bridgeable) == 1
        assert result.not_bridgeable[0].path == repo
        assert result.not_bridgeable[0].status == "not_bridgeable"
        assert result.not_bridgeable[0].filter_reason == "no_agents_or_claude_md"

    def test_symlink_filtered(self, tmp_path: Path):
        """A symlinked directory is filtered as 'symlink'."""
        real = _make_git_repo(tmp_path / "real")
        (real / "AGENTS.md").write_text("# agents")
        link = tmp_path / "link"
        link.symlink_to(real)

        result = filter_scan_candidates([link])

        assert result.bridgeable == ()
        assert result.not_bridgeable == ()
        assert len(result.filtered) == 1
        assert result.filtered[0].path == link
        assert result.filtered[0].filter_reason == "symlink"

    def test_git_submodule_filtered(self, tmp_path: Path):
        """A git submodule (.git file) is filtered as 'not_git_root'."""
        sub = _make_git_submodule(tmp_path / "sub")
        (sub / "AGENTS.md").write_text("# agents")

        result = filter_scan_candidates([sub])

        assert result.bridgeable == ()
        assert len(result.filtered) == 1
        assert result.filtered[0].filter_reason == "not_git_root"

    def test_git_worktree_filtered(self, tmp_path: Path):
        """A git worktree (.git file) is filtered as 'not_git_root'."""
        wt = _make_git_worktree(tmp_path / "wt")
        (wt / "CLAUDE.md").write_text("# claude")

        result = filter_scan_candidates([wt])

        assert result.bridgeable == ()
        assert len(result.filtered) == 1
        assert result.filtered[0].filter_reason == "not_git_root"

    def test_no_git_filtered(self, tmp_path: Path):
        """A directory without .git is filtered as 'no_git'."""
        d = tmp_path / "norepo"
        d.mkdir()
        (d / "AGENTS.md").write_text("# agents")

        result = filter_scan_candidates([d])

        assert result.bridgeable == ()
        assert len(result.filtered) == 1
        assert result.filtered[0].filter_reason == "no_git"

    def test_no_claude_presence_filtered(self, tmp_path: Path):
        """A git repo with no Claude presence is filtered as 'no_claude'."""
        repo = _make_git_repo(tmp_path / "bare")

        result = filter_scan_candidates([repo])

        assert result.bridgeable == ()
        assert result.not_bridgeable == ()
        assert len(result.filtered) == 1
        assert result.filtered[0].filter_reason == "no_claude"

    def test_multiple_candidates_categorized(self, tmp_path: Path):
        """Multiple candidates are categorized into the correct buckets."""
        # bridgeable
        good = _make_git_repo(tmp_path / "good")
        (good / "AGENTS.md").write_text("# agents")

        # not_bridgeable (has .claude/ only)
        partial = _make_git_repo(tmp_path / "partial")
        (partial / ".claude").mkdir()

        # filtered (symlink)
        real = _make_git_repo(tmp_path / "real")
        (real / "AGENTS.md").write_text("# agents")
        sym = tmp_path / "sym"
        sym.symlink_to(real)

        # filtered (no git)
        nogit = tmp_path / "nogit"
        nogit.mkdir()
        (nogit / "AGENTS.md").write_text("# agents")

        result = filter_scan_candidates([good, partial, sym, nogit])

        assert len(result.bridgeable) == 1
        assert result.bridgeable[0] == good
        assert len(result.not_bridgeable) == 1
        assert result.not_bridgeable[0].path == partial
        assert len(result.filtered) == 2

    def test_output_tuples_sorted_by_path(self, tmp_path: Path):
        """All output tuples are sorted by path."""
        # Create several bridgeable repos with names that would sort differently
        for name in ("zz_repo", "aa_repo", "mm_repo"):
            repo = _make_git_repo(tmp_path / name)
            (repo / "CLAUDE.md").write_text("# claude")

        result = filter_scan_candidates([
            tmp_path / "zz_repo",
            tmp_path / "aa_repo",
            tmp_path / "mm_repo",
        ])

        assert list(result.bridgeable) == sorted(result.bridgeable)


# ===================================================================
# Task 4: scan_for_projects
# ===================================================================


class TestScanForProjects:
    """Tests for scan_for_projects()."""

    def test_no_config_file_empty_result(self, tmp_path: Path):
        """When config.toml is absent, return empty ScanResult."""
        result = scan_for_projects(tmp_path)

        assert result.bridgeable == ()
        assert result.not_bridgeable == ()
        assert result.filtered == ()

    def test_empty_scan_paths_empty_result(self, tmp_path: Path):
        """When scan_paths is empty, return empty ScanResult."""
        (tmp_path / SCAN_CONFIG_FILENAME).write_text(
            'scan_paths = []\n'
        )

        result = scan_for_projects(tmp_path)

        assert result.bridgeable == ()
        assert result.not_bridgeable == ()
        assert result.filtered == ()

    def test_end_to_end_categorization(self, tmp_path: Path):
        """End-to-end: config + various project types → correct categories."""
        projects = tmp_path / "projects"
        projects.mkdir()

        # bridgeable git repo with AGENTS.md
        good = _make_git_repo(projects / "good")
        (good / "AGENTS.md").write_text("# agents")

        # symlinked directory
        real = _make_git_repo(projects / "real_target")
        (real / "AGENTS.md").write_text("# agents")
        (projects / "symlink").symlink_to(real)

        # git submodule
        _make_git_submodule(projects / "submod")
        (projects / "submod" / "AGENTS.md").write_text("# agents")

        # partial: .claude/ only
        partial = _make_git_repo(projects / "partial")
        (partial / ".claude").mkdir()

        # Write config
        (tmp_path / SCAN_CONFIG_FILENAME).write_text(
            f'scan_paths = ["{projects}/*"]\n'
        )

        result = scan_for_projects(tmp_path)

        bridgeable_names = {p.name for p in result.bridgeable}
        not_bridgeable_names = {c.path.name for c in result.not_bridgeable}
        filtered_reasons = {c.path.name: c.filter_reason for c in result.filtered}

        assert "good" in bridgeable_names
        # real_target is also bridgeable
        assert "real_target" in bridgeable_names
        assert "partial" in not_bridgeable_names
        assert filtered_reasons.get("symlink") == "symlink"
        assert filtered_reasons.get("submod") == "not_git_root"

    def test_excludes_applied_before_filtering(self, tmp_path: Path):
        """Exclude paths remove candidates before structural filtering."""
        projects = tmp_path / "projects"
        projects.mkdir()

        # Two bridgeable repos
        a = _make_git_repo(projects / "keep")
        (a / "AGENTS.md").write_text("# agents")
        b = _make_git_repo(projects / "skip")
        (b / "AGENTS.md").write_text("# agents")

        (tmp_path / SCAN_CONFIG_FILENAME).write_text(
            f'scan_paths = ["{projects}/*"]\n'
            f'exclude_paths = ["{projects}/skip"]\n'
        )

        result = scan_for_projects(tmp_path)

        bridgeable_names = {p.name for p in result.bridgeable}
        assert "keep" in bridgeable_names
        assert "skip" not in bridgeable_names
        # "skip" should not appear anywhere in results at all
        all_paths = (
            list(result.bridgeable)
            + [c.path for c in result.not_bridgeable]
            + [c.path for c in result.filtered]
        )
        assert all(p.name != "skip" for p in all_paths)
