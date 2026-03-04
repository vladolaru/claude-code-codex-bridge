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

### Runtime Contract

The bridge reads local Claude Code state and produces Codex-compatible outputs such as:

- `CLAUDE.md` as the `@AGENTS.md` shim
- `.codex/config.toml`
- `.codex/prompts/agents/*.md`
- `.codex/interop-state.json`
- `~/.codex/skills/*`

Do not treat generated `.codex/*` or generated Codex skill directories as hand-authored source.

## Development

### Setup

Install in editable mode:

```bash
python3 -m pip install -e ".[dev]"
```

### Testing

Run the full test suite after code changes:

```bash
pytest tests -q
```

Run coverage:

```bash
pytest --cov=cc_codex_bridge --cov-report=term-missing tests -q
```

### Packaging

- Keep the `src/` layout intact.
- Keep imports using the `cc_codex_bridge` package path.
- Keep the console script entrypoint in `pyproject.toml` aligned with the package layout.

## Development Model

This project is AI-written and AI-maintained with human guidance and decisions.

The human maintainer, Vlad, sets direction, makes architectural decisions, and reviews work. Claude Code agents do the implementation, testing, analysis, and maintenance.

"Single maintainer" does not mean capacity-constrained. It means there is a single human decision-maker with AI execution capacity. Do not assume limited implementation bandwidth when reasoning about priorities or feasibility.

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
- Update `DESIGN.md` whenever the implemented architecture changes materially.
- Update the relevant analysis, spec, or plan docs when the command surface, architecture, or implementation direction changes materially.

## Guidance

- Prefer small, verifiable changes.
- Preserve deterministic behavior in generated outputs.
- Keep Codex-facing artifacts generated from Claude Code sources instead of hand-maintained copies.
- Update documentation when command surfaces, package layout, or installation behavior changes.
