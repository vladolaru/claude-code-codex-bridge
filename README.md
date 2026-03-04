# claude-code-codex-bridge

Bridge your local Claude Code setup into Codex so both runtimes stay equally effective.

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

## Install

```bash
python3 -m pip install -e .
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

If you need to run from a raw checkout without installing, use:

```bash
PYTHONPATH=src python3 -m cc_codex_bridge reconcile --project .
```

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
