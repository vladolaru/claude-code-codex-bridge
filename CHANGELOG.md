# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- Hardened generated agent role and prompt naming so unsafe Claude agent names cannot escape the managed prompt directory, and cross-marketplace collisions are disambiguated deterministically.
- Escaped generated `.codex/config.toml` string values correctly so multiline Claude frontmatter fields still produce valid TOML.
- Validated interop state schema types and migrated managed skill directories correctly when `--codex-home` changes, preventing crashes and orphaned generated skills.
- Tightened `CLAUDE.md` shim ownership checks so only the exact generated shim content is treated as generator-owned.

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
