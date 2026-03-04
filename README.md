# claude-code-codex-bridge

Bridge your local Claude Code setup into Codex so both stay equally effective.

This repository contains the `cc-codex-bridge` CLI. It reads the Claude Code setup available on a local machine and projects it into Codex-compatible artifacts without creating a second hand-maintained ecosystem.

What it bridges:

- installed Claude Code plugins
- user-level Claude Code skills and agents that are part of the local Claude setup
- shared project instructions rooted in `AGENTS.md`

What it generates:

- project-local `CLAUDE.md`
- project-local `.codex/config.toml`
- project-local `.codex/prompts/agents/*.md`
- project-local `.codex/interop-state.json`
- user-global `~/.codex/skills/*`

## Install From A Local Checkout

For normal use from a local clone, install the package non-editably:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
```

Then run:

```bash
cc-codex-bridge validate --project .
cc-codex-bridge dry-run --project .
cc-codex-bridge reconcile --project .
```

The module entrypoint also works after installation:

```bash
python3 -m cc_codex_bridge reconcile --project .
```

## Contributor Setup

For development, use the editable install with the dev extras:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e ".[dev]"
```

Common verification commands:

```bash
pytest tests -q
python3 -m build --sdist --wheel
```

## Run From A Raw Checkout

If you need to run directly from the checkout without installing the package, use:

```bash
PYTHONPATH=src python3 -m cc_codex_bridge reconcile --project .
```

## Release

The release workflow is triggered by version tags in the form `vX.Y.Z`. The release checklist lives in [`.claude/docs/patterns/2026-03-04-release-procedure.md`](.claude/docs/patterns/2026-03-04-release-procedure.md).

## Developer Workflow

1. Update the canonical Claude Code sources that define the local setup.
2. Make sure those assets are available in the local Claude Code environment.
3. Run `cc-codex-bridge validate --project .`
4. Run `cc-codex-bridge dry-run --project .`
5. Run `cc-codex-bridge reconcile --project .`
6. Use the generated Codex artifacts. Do not hand-edit them.

## More Detail

The package-level documentation remains in [`src/cc_codex_bridge/README.md`](src/cc_codex_bridge/README.md).

## License

MIT. See [`LICENSE`](LICENSE).
