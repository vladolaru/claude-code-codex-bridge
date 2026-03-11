# claude-code-codex-bridge

Bridge your local Claude Code setup into Codex so both stay equally effective.

This repository contains the `cc-codex-bridge` CLI. It reads the Claude Code setup available on a local machine and projects it into Codex-compatible artifacts without creating a second hand-maintained ecosystem.

What it bridges:

- installed Claude Code plugins
- shared project instructions rooted in `AGENTS.md`

What it generates:

- project-local `CLAUDE.md`
- project-local `.codex/config.toml`
- project-local `.codex/prompts/agents/*.md`
- project-local `.codex/claude-code-bridge-state.json`
- user-global `~/.codex/skills/*`

## Install From GitHub Releases

For normal use on macOS, install from the latest GitHub Release:

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

The release installer downloads a self-contained wheelhouse bundle from GitHub and installs with `pip --no-index`, so it does not need PyPI during installation.

Supported interpreter versions for the release installer are currently Python `3.11`, `3.12`, `3.13`, and `3.14`.

To install a specific release instead of the latest:

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | \
  bash -s -- --version v0.3.0
```

After installation, verify the local machine setup:

```bash
cc-codex-bridge doctor
cc-codex-bridge doctor --json
```

Then run the normal project commands:

```bash
cc-codex-bridge validate --project .
cc-codex-bridge reconcile --dry-run --project .
cc-codex-bridge reconcile --dry-run --diff --project .
cc-codex-bridge status --project .
cc-codex-bridge reconcile --project .
```

Remove bridge artifacts from a single project:

```bash
cc-codex-bridge clean --project .
cc-codex-bridge clean --dry-run --project .
```

Remove all bridge artifacts from the machine:

```bash
cc-codex-bridge uninstall --dry-run
cc-codex-bridge uninstall --dry-run --json
cc-codex-bridge uninstall
```

## Install From A Local Checkout

For direct installation from a local clone, install the package non-editably:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install .
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

The release workflow is triggered by version tags in the form `vX.Y.Z`.

Maintainer flow:

```bash
make release VERSION=X.Y.Z
```

Run it from a clean `main` checkout using the repository `.venv`.

That command checks version alignment, verifies the selected interpreter has `pytest` and `setuptools`, verifies the git worktree is clean, requires the current branch to be `main`, runs `pytest tests -q`, creates the annotated tag, and atomically pushes the branch plus tag. GitHub Actions then builds the release artifacts and publishes the GitHub Release.

Fresh disposable Python virtualenvs on modern macOS may not include `setuptools`. If you rehearse the release flow outside the repository `.venv`, bootstrap `setuptools` in that venv before installing the repo.

Update [`CHANGELOG.md`](CHANGELOG.md) and both version declarations before running it. The detailed agent-facing release guidance lives in [`AGENTS.md`](AGENTS.md).

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
