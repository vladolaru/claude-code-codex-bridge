# Codex Interop

`cc_codex_bridge` is a generator and reconcile layer that projects a local Claude Code setup into Codex-compatible artifacts.

It exists to avoid maintaining two parallel systems:

- Claude Code remains the canonical runtime and authoring model
- Codex gets generated compatibility artifacts

That is the central design decision:

- Claude-side skills and agents are the only canonical authored source
- Codex-side skills, prompts, and config are generated projections
- this directory should never become a second hand-maintained skill or agent ecosystem

The canonical authored sources stay outside this package:

- project `AGENTS.md`
- Claude Code plugin-provided skills and agents
- related scripts, references, assets, and resources attached to that local Claude Code setup

## What It Generates

Project-local outputs:

- `CLAUDE.md`
  - generated only as the shim `@AGENTS.md`
- `.codex/config.toml`
  - inline Codex multi-agent config
- `.codex/prompts/agents/*.md`
  - generated prompt payloads for translated Claude agents
User-global outputs:

- `~/.codex/skills/<plugin>-<skill>/`
  - generated Codex skills derived from installed Claude skills

Important output boundary:

- `.codex/config.toml` is intentionally local-only and should not be committed
- Codex skills are intentionally materialized only under `~/.codex/skills/`
- there is no project-local Codex skill mirror

## Core Constraints

- `AGENTS.md` is canonical and hand-authored.
- `AGENTS.md` is strictly shared-only.
- The generator never modifies `AGENTS.md`.
- `AGENTS.md` must not contain Codex-only or Claude-only runtime wiring.
- Claude Code behavior must remain unchanged.
- The only Claude-facing generated artifact is `CLAUDE.md`, and only as `@AGENTS.md`.
- Inputs come from actually installed Claude Code plugins under `~/.claude/plugins/cache/...`.
- Project discovery is only `cwd` or `--project`.
- There is no filesystem-wide project crawling.
- If an installed plugin path resolves through a symlink into a repo checkout, the generator follows the resolved repo path.
- If multiple installed versions exist, the latest version is selected by semver.

## What It Does

1. Resolves the target project from `cwd` or `--project`.
2. Requires that the project root contains `AGENTS.md`.
3. Discovers installed Claude plugins from the local Claude cache.
4. Selects the latest installed version of each plugin by semver.
5. Translates Claude agents into:
   - `.codex/prompts/agents/*.md`
   - `.codex/config.toml` `[agents.<role>]` entries
6. Translates Claude skills into self-contained Codex skills under `~/.codex/skills/`.
7. Reconciles those generated artifacts safely and idempotently.

The intended split is:

- shared project guidance stays in `AGENTS.md`
- Claude-native behavior continues to come from Claude’s own plugin/runtime model
- Codex-specific runtime behavior lives only in generated `.codex/*` and `~/.codex/skills/*`

## Ownership and Safety

Never touched:

- `AGENTS.md`
- `plugins/**`
- hand-authored project files

Generator-owned:

- `CLAUDE.md` when it is the exact `@AGENTS.md` shim
- `.codex/config.toml`
- `.codex/prompts/agents/*`
- `~/.cc-codex-bridge/` (bridge state and registry)
- generated Codex skills in `~/.codex/skills/*`

Safety rules:

- refuses to overwrite a hand-authored `CLAUDE.md`
- refuses to overwrite non-generated `.codex` files
- refuses to overwrite non-generated Codex skill directories
- uses staged writes with rollback-safe backup/rename replacement
- keeps state deterministic so rerunning without input changes is a no-op

In practice, existing hand-authored files are treated as authoritative unless they are explicitly generator-owned artifacts.

## Commands

Normal macOS installs should use the GitHub Release installer:

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

The installer downloads a self-contained wheelhouse bundle from GitHub and installs with `pip --no-index`, so it does not need PyPI during installation.

Supported interpreter versions for the release installer are currently Python `3.11`, `3.12`, `3.13`, and `3.14`.

After install, verify the machine-level prerequisites:

```bash
cc-codex-bridge doctor
cc-codex-bridge doctor --json
```

After that you can use:

- `cc-codex-bridge ...`
- `python3 -m cc_codex_bridge ...`

For local development installs from a checkout, use:

```bash
python3 -m pip install -e .
```

Without installation from a raw checkout, use:

- `PYTHONPATH=src python3 -m cc_codex_bridge ...`

Examples below use the packaged CLI form. Run from the repo root or pass `--project`.

Validate only:

```bash
cc-codex-bridge validate --project .
```

Preview changes without writing:

```bash
cc-codex-bridge reconcile --dry-run --project .
```

Show file-level diffs:

```bash
cc-codex-bridge reconcile --dry-run --diff --project .
```

Inspect reconcile state:

```bash
cc-codex-bridge status --project .
cc-codex-bridge status --json --project .
```

Apply generated outputs:

```bash
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

Useful overrides:

- `--cache-dir /path/to/claude/plugins/cache`
- `--codex-home /path/to/codex/home`
- `--exclude-plugin marketplace/plugin` (repeatable)
- `--exclude-skill marketplace/plugin/skill` (repeatable)
- `--exclude-agent marketplace/plugin/agent.md` (repeatable)

These are mainly useful for testing or controlled local runs.

Persistent exclusions can be defined in `.codex/bridge.toml`:

```toml
[exclude]
plugins = ["market/pirategoat-tools"]
skills = ["market/prompt-engineer/internal-cc-only"]
agents = ["market/prompt-engineer/reviewer.md"]
```

CLI exclusion flags override config exclusions for that entity kind in the current run.

## Developer Workflow

Treat `cc_codex_bridge` as a local generator, not as a second authored system.

The normal workflow is:

1. Edit the canonical Claude-side sources that make up the local Claude Code setup.
2. Install the local package once with `python3 -m pip install -e .`
3. Make sure the relevant Claude plugins are actually installed locally so they exist in the Claude plugin cache.
4. Run `cc-codex-bridge validate --project .`
5. Run `cc-codex-bridge reconcile --dry-run --project .`
6. Optionally run `cc-codex-bridge reconcile --dry-run --diff --project .`
7. Inspect current state with `cc-codex-bridge status --project .`
8. Run `cc-codex-bridge reconcile --project .`
9. Use the generated Codex artifacts from `.codex/*`, `CLAUDE.md`, and `~/.codex/skills/*`

To reconcile all previously reconciled projects at once:

```bash
cc-codex-bridge reconcile --all
```

If you do not want to install the console script, either install the package and use `python3 -m cc_codex_bridge ...` or run from a raw checkout with `PYTHONPATH=src`.

Important:

- do not hand-edit generated `.codex/*` files
- do not hand-edit generated `~/.codex/skills/*`
- if generated output is wrong, change the canonical Claude-side source and rerun `reconcile`
- `.codex/config.toml` is local-only and should not be committed

Project selection is intentionally narrow:

- default target = current working directory
- override target = `--project /path/to/project`
- valid project = directory containing `AGENTS.md`
- no machine-wide repo discovery

## macOS Scheduling

The supported automation path is `launchd`, not a long-lived watcher.

That is also a deliberate decision:

- scheduled reconcile is the supported macOS automation mode
- watcher mode is deferred
- installed Claude plugin state remains the source of truth

Print the global LaunchAgent plist:

```bash
cc-codex-bridge print-launchagent
```

Install the global LaunchAgent plist into `~/Library/LaunchAgents/`:

```bash
cc-codex-bridge install-launchagent
```

The global LaunchAgent runs `reconcile --all` every 30 minutes (1800 seconds).

Optional scheduling overrides:

- `--interval 900`
- `--logs-dir ...`
- `--python-executable ...`
- `--cli-path ...`
- `--label ...`

`install-launchagent` writes the plist and prints the next `launchctl bootstrap` command. It does not run `launchctl` automatically. If existing per-project plists are found, it warns and prints `launchctl bootout` commands.

## Implementation Notes

Agent translation:

- Claude agent markdown frontmatter is parsed and mapped into Codex role config
- original Claude model hints are preserved as metadata/comments
- Claude tool names are translated to Codex tool identifiers where possible

Skill translation:

- official skill layout is copied: `SKILL.md`, `scripts/`, `references/`, `assets/`, `agents/`
- generated skill `name:` matches the generated parent directory name
- plugin-root path references and sibling-skill references are rewritten when relocation requires it
- non-skill junk such as `.venv/`, `__pycache__/`, `.pyc`, `.DS_Store` is excluded

## Tests

Run the test suite:

```bash
pytest tests -q
```

Run coverage:

```bash
pytest --cov=cc_codex_bridge --cov-report=term-missing tests -q
```

Current status changes over time. Run the commands above for the current local result.

## Scope Boundary

This implementation covers:

- installed-plugin discovery
- agent translation
- skill translation
- reconcile and state tracking
- project-level artifact cleanup via `clean`
- machine-level full artifact removal via `uninstall`
- macOS LaunchAgent generation/installation

It intentionally does not cover:

- a filesystem watcher mode
- changing Claude Code’s native runtime model

This README is the practical summary.
