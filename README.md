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
- project-local `.codex/claude-code-interop-state.json`
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
cc-codex-bridge reconcile --dry-run --project .
cc-codex-bridge reconcile --dry-run --diff --project .
cc-codex-bridge status --project .
cc-codex-bridge reconcile --project .
```

Optional exclusion flags (repeatable) let you skip Claude-specific entities:

```bash
cc-codex-bridge reconcile --project . \
  --exclude-plugin marketplace/plugin \
  --exclude-skill marketplace/plugin/skill \
  --exclude-agent marketplace/plugin/agent.md
```

To persist exclusions per project, create `.codex/bridge.toml`:

```toml
[exclude]
plugins = ["market/pirategoat-tools"]
skills = ["market/prompt-engineer/internal-cc-only"]
agents = ["market/prompt-engineer/reviewer.md"]
```

CLI exclusion flags override config exclusions for the same entity kind in that run.

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

The release workflow is triggered by version tags in the form `vX.Y.Z`. Update [`CHANGELOG.md`](CHANGELOG.md) for each release and follow the release instructions in [`AGENTS.md`](AGENTS.md).

## Developer Workflow

1. Update the canonical Claude Code sources that define the local setup.
2. Make sure those assets are available in the local Claude Code environment.
3. Run `cc-codex-bridge validate --project .`
4. Run `cc-codex-bridge reconcile --dry-run --project .`
5. Optionally run `cc-codex-bridge reconcile --dry-run --diff --project .`
6. Inspect current state with `cc-codex-bridge status --project .`
7. Run `cc-codex-bridge reconcile --project .`
8. Use the generated Codex artifacts. Do not hand-edit them.

## More Detail

The package-level documentation remains in [`src/cc_codex_bridge/README.md`](src/cc_codex_bridge/README.md).

## License

MIT. See [`LICENSE`](LICENSE).
