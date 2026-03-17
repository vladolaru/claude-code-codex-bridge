# AGENTS.md

This file provides shared project guidance for agent runtimes working with this repository.

## Repository Overview

This repository is **claude-code-codex-bridge**.

It provides the `cc-codex-bridge` CLI, a standalone tool that bridges a local Claude Code setup into Codex-compatible artifacts without creating a second hand-maintained system.

## Architecture

### Repository Structure

```text
claude-code-codex-bridge/
├── .claude/
│   └── docs/
│       ├── analysis/
│       ├── decisions/
│       ├── learnings/
│       ├── patterns/
│       ├── plans/
│       └── research/
├── .github/
│   └── workflows/
├── docs/
│   ├── agent-skills-standard.md
│   └── codex-cli-reference.md
├── src/
│   └── cc_codex_bridge/
│       ├── __main__.py
│       ├── cli.py
│       ├── discover.py
│       ├── reconcile.py
│       ├── translate_agents.py
│       ├── translate_skills.py
│       └── ...
├── tests/
├── AGENTS.md
├── CLAUDE.md
├── LICENSE
├── README.md
└── pyproject.toml
```

### Package Layout

- Runtime code lives under `src/cc_codex_bridge/`.
- Tests live under `tests/`.
- The installable CLI command is `cc-codex-bridge`.
- The Python module entrypoint is `python3 -m cc_codex_bridge`.

### Runtime Dependencies

The `claude` CLI must be available on PATH. The bridge shells out to `claude plugins list --json` to determine which plugins are enabled. Without it, `discover()` raises a `DiscoveryError` and the bridge cannot function.

### Runtime Contract

The bridge reads local Claude Code state and produces Codex-compatible outputs such as:

- `CLAUDE.md` as the `@AGENTS.md` shim
- `~/.codex/agents/*.toml` (global agent files, tracked in global registry)
- `.codex/agents/*.toml` (project-local agent files)
- `.codex/claude-code-bridge-state.json`
- `~/.codex/skills/*`

Do not treat generated `.codex/*` or generated Codex skill directories as hand-authored source.

## Development

### Setup

Install in editable mode:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

### Testing

**Always run tests through the project venv.** The package depends on PyYAML and other libraries that are not available in the system Python. Running bare `pytest` without the venv will fail with `ModuleNotFoundError`.

Run the full test suite after code changes:

```bash
source .venv/bin/activate && pytest tests -q
```

Run coverage:

```bash
source .venv/bin/activate && pytest --cov=cc_codex_bridge --cov-report=term-missing tests -q
```

### Packaging

- Keep the `src/` layout intact.
- Keep imports using the `cc_codex_bridge` package path.
- Keep the console script entrypoint in `pyproject.toml` aligned with the package layout.

## Development Model

This project is AI-written and AI-maintained. The human (Vlad) sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance. "Single maintainer" does not mean capacity-constrained — it means single human decision-maker with AI execution capacity. Do not assume limited implementation bandwidth when reasoning about priorities or feasibility.

No agent carries context between sessions — every agent reads the code cold. This has practical implications:

- **Prefer single canonical implementations** over duplicated patterns. An agent will copy whichever pattern it encounters first; if two conventions exist for the same thing, drift is inevitable.
- **Constants, types, and named helpers are discovery mechanisms.** They are more valuable here than in a human-authored codebase — they are the primary way agents find the "right" way to do something.
- **Consolidating duplicated logic is drift prevention**, not polish. Treat it accordingly when prioritizing work.

## Domain References

The bridge translates between two ecosystems. Authoritative reference documents live in `docs/`:

- `docs/agent-skills-standard.md` — the open Agent Skills standard (from [agentskills.io](https://agentskills.io/)): skill directory structure, `SKILL.md` format, frontmatter fields, progressive disclosure, client implementation contract, and script conventions.
- `docs/codex-cli-reference.md` — Codex CLI specifics (from [developers.openai.com/codex](https://developers.openai.com/codex/) and the [Codex source](https://github.com/openai/codex)): instructions discovery (`AGENTS.md`), skill discovery hierarchy, agent role configuration, agent file auto-discovery, and Claude Code vs Codex comparison.

Consult these before making design decisions that depend on how Codex discovers skills, loads instructions, or defines agent roles.

## Canonical Architecture

`DESIGN.md` is the canonical architectural source for the current implemented state of this project.

Agents must consult `DESIGN.md` before making substantial architectural or cross-module changes, and must keep it updated at all times when implementation changes affect architecture, data flow, ownership rules, constraints, command behavior, or module responsibilities.

## AI Artifacts

All AI-generated artifacts go under `.claude/docs/`:

- `analysis/` for investigations and findings
- `decisions/` for architecture decisions
- `learnings/` for debugging insights and gotchas
- `patterns/` for reusable workflows and conventions
- `plans/` for implementation plans
- `research/` for deeper research notes

Do not create AI working artifacts in the repo root.

### Working Docs

Before starting substantial implementation or design work, check for relevant docs under `.claude/docs/`.

Also check `DESIGN.md` for the canonical description of the current implemented architecture.

- Review `.claude/docs/analysis/` for prior investigations and technical findings.
- Review spec documents in `.claude/docs/plans/`, such as `*-spec.md`, for the intended contract and constraints.
- Review implementation plans in `.claude/docs/plans/`, such as `*-implementation-plan.md`, for sequencing, scope, and expected milestones.
- Consult `docs/agent-skills-standard.md` and `docs/codex-cli-reference.md` for the authoritative domain references that the bridge targets. These supersede earlier research notes in `.claude/docs/research/`.
- Update `DESIGN.md` whenever the implemented architecture changes materially.
- Update the relevant analysis, spec, or plan docs when the command surface, architecture, or implementation direction changes materially.

## Guidance

- Prefer small, verifiable changes.
- Preserve deterministic behavior in generated outputs.
- Keep Codex-facing artifacts generated from Claude Code sources instead of hand-maintained copies.
- Update documentation when command surfaces, package layout, or installation behavior changes.

## Versioning & Releases

### Rule 0: Runtime Changes Must Update Release Metadata

Any change that modifies shipped runtime behavior should update the release metadata in the same change:

1. add or update an entry under `CHANGELOG.md` `Unreleased`
2. bump the package version when preparing a release

For this repository, release version sources must stay aligned:

1. `pyproject.toml` `project.version`
2. `src/cc_codex_bridge/__init__.py` `__version__`

The package tests enforce that these values match.

### Semver Guidance

Use semver for release planning:

- `feat` level changes: minor bump
- `fix`, `refactor`, or `perf` changes that preserve compatibility: patch bump
- breaking changes: major bump

Documentation-only, test-only, CI-only, style-only, and non-runtime chore changes do not require an immediate version bump, but notable user-facing changes should still be recorded in `CHANGELOG.md`.

### Changelog Discipline

`CHANGELOG.md` is the canonical human-maintained release summary.

Before a release:

1. keep new entries under `## [Unreleased]`
2. group changes under Keep a Changelog headings such as `Added`, `Changed`, `Fixed`, and `Removed`
3. move the `Unreleased` notes into a dated `## [X.Y.Z] - YYYY-MM-DD` section when cutting the release

If a release has not been tagged and pushed yet, fold additional related work into the pending unreleased notes rather than creating fake intermediate versions.

### Tag and Release Model

This is a single-package repository. Use package-level tags:

- tag format: `vX.Y.Z`

Pushing a matching version tag triggers `.github/workflows/release.yml`, which validates the package, builds `sdist` and `wheel` artifacts, collects exact runtime dependency wheels for supported macOS/Python targets, generates `install.sh` plus `SHA256SUMS`, and creates a GitHub Release.

The release channel is GitHub Releases only for now.

Release assets must remain self-contained:

- package wheel
- package sdist
- offline wheelhouse archive with the app wheel plus exact runtime dependency wheels
- generated `install.sh`
- `SHA256SUMS`

End-user installs should work from GitHub assets alone with `pip install --no-index` against the unpacked wheelhouse bundle. Do not introduce a PyPI requirement into the end-user install path.

### Agent Release Workflow

When asked to prepare or execute a release, agents should:

1. update `CHANGELOG.md`
2. update both version declarations
3. run `make release VERSION=X.Y.Z` from a clean `main` worktree using the repository `.venv`

`make release` is the maintainer-facing release command. It checks version alignment, verifies that the selected interpreter has `pytest` and `setuptools`, verifies the worktree is clean, requires the current branch to be `main`, runs `pytest tests -q`, creates the annotated `vX.Y.Z` tag, and atomically pushes `main` plus the tag.

Local releases are supported from the repository `.venv`. Fresh disposable Python virtualenvs may not include `setuptools`, so agents rehearsing the release flow outside `.venv` must bootstrap `setuptools` before installing the repo into that environment.

GitHub Actions is authoritative for release artifact validation and publication. The release workflow must continue to:

1. build `sdist` and `wheel`
2. validate the offline wheelhouse install path
3. generate the wheelhouse bundle, `install.sh`, and `SHA256SUMS`
4. publish the GitHub Release

Use conventional commit prefixes where they add clarity, but do not invent extra release mechanics beyond the workflow and docs already in this repository.
