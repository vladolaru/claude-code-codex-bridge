# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Added a machine-level `doctor` command with text and JSON output so installs can verify Python, Codex-home, Claude-cache, LaunchAgents, and PATH visibility before the project pipeline runs.
- Added self-contained GitHub Release installer assets: a generated `install.sh`, a bundled macOS wheelhouse archive, and release checksums for GitHub-only installs without PyPI access.
- Added a maintainer-facing `make release VERSION=X.Y.Z` command that verifies a clean worktree, runs tests, tags the release, and hands artifact publishing off to GitHub Actions.

### Changed

- Started reconcile hardening with a validated global skill-registry model, deterministic generated-skill hashing, and a trimmed project-local state payload that no longer records selected plugin identities.
- Moved generated Codex skill ownership into a global registry under the resolved Codex home, allowing identical skills to be shared safely across projects and keeping last-owner cleanup aligned with registry claims.
- Simplified the project-local interop state so it now tracks only project-local managed files plus the last reconciled Codex home.
- Added project-first/global-second reconcile locking for mutating runs so concurrent reconciles fail fast instead of racing shared project or Codex-home writes.
- Changed unsupported Claude agent tools from silent drops into hard diagnostics, with `status` now reporting an explicit `invalid` state instead of pretending the project only has pending changes.
- Switched shared agent and skill frontmatter parsing to PyYAML safe loading for frontmatter blocks, while keeping strict post-parse validation and explicit errors for unsupported runtime shapes.
- Removed the old test-only generated-skill materialization helper so reconcile remains the single production path that writes installed Codex skill trees.
- Changed CI and release packaging smoke tests to validate offline installs from a GitHub-hosted wheelhouse bundle using `pip --no-index`.

### Fixed

- Aligned the offline installer and release wheelhouse contract so the installer now fails fast outside the explicitly bundled Python minors and the release workflow now includes Python 3.14 wheelhouse slices.
- Tightened `make release` so it only runs from `main` and atomically pushes the branch plus tag, avoiding split branch/tag release states.
- Added an early maintainer release preflight for `pytest` and `setuptools` so unsupported local interpreters fail with a `.venv` setup hint instead of dying later inside tests or editable installs.

- Hardened generated agent role and prompt naming so unsafe Claude agent names cannot escape the managed prompt directory, and cross-marketplace collisions are disambiguated deterministically.
- Escaped generated `.codex/config.toml` string values correctly so multiline Claude frontmatter fields still produce valid TOML.
- Validated interop state schema types and migrated managed skill directories correctly when `--codex-home` changes, preventing crashes and orphaned generated skills.
- Tightened `CLAUDE.md` shim ownership checks so only the exact generated shim content is treated as generator-owned.
- Preserved generator-managed `CLAUDE.md -> AGENTS.md` symlinks across later reconciles instead of treating them as stale managed files and deleting them.
- Rejected foreign or malformed reconcile state before stale skill cleanup, including invalid managed skill directory entries and symlinked state files.
- Failed fast when a generated Codex skill references a missing sibling Claude skill instead of emitting a broken relocated path.
- Surfaced filesystem `OSError` failures as user-facing CLI errors with exit code `1` instead of uncaught exceptions.
- Surfaced UTF-8 decode failures in `CLAUDE.md`, frontmatter files, config files, state files, registries, and diffed managed text as user-facing errors instead of uncaught tracebacks.
- Accepted quoted scalar frontmatter values and simple inline lists such as `tools: [Read, Write]` in the shared parser.
- Reclaimed same-host stale reconcile lock files automatically when their recorded pid is no longer running.
- Resolved LaunchAgent commands from nested project paths using the same upward `AGENTS.md` discovery as the main pipeline.

## [0.3.0] - 2026-03-05

### Added

- Added exclusion controls for sync inputs via project `.codex/bridge.toml` and repeatable CLI flags: `--exclude-plugin`, `--exclude-skill`, and `--exclude-agent`.
- Added status/summary reporting for effective excluded plugins, skills, and agents.

## [0.2.0] - 2026-03-05

### Changed

- Renamed the project-local reconcile state artifact to `.codex/claude-code-interop-state.json`.
- Clarified internal naming in reconcile/discovery command flow (no behavior change).
- Consolidated preview mode under `reconcile --dry-run` and `reconcile --dry-run --diff`.

### Added

- Added `status` command with `in_sync` vs `pending_changes`, categorized change reporting, and optional `--json` output.

## [0.1.0] - 2026-03-04

### Added

- Initial `cc-codex-bridge` CLI for discovering installed Claude Code plugins and generating Codex-compatible artifacts.
- Project-local generation for `CLAUDE.md`, `.codex/config.toml`, `.codex/prompts/agents/*.md`, and `.codex/interop-state.json`.
- User-global generated Codex skill installation under `~/.codex/skills/`.
- Conservative reconcile/state management and LaunchAgent support for scheduled reconcile runs.
