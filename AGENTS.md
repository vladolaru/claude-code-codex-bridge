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

## AI Artifacts

All AI-generated artifacts go under `.claude/docs/`:

- `analysis/` for investigations and findings
- `decisions/` for architecture decisions
- `learnings/` for debugging insights and gotchas
- `patterns/` for reusable workflows and conventions
- `plans/` for implementation plans
- `research/` for deeper research notes

Do not create AI working artifacts in the repo root.

## Guidance

- Prefer small, verifiable changes.
- Preserve deterministic behavior in generated outputs.
- Keep Codex-facing artifacts generated from Claude Code sources instead of hand-maintained copies.
- Update documentation when command surfaces, package layout, or installation behavior changes.
