# claude-code-codex-bridge

[![Latest release](https://img.shields.io/github/v/release/vladolaru/claude-code-codex-bridge)](https://github.com/vladolaru/claude-code-codex-bridge/releases/latest)

Automatically bridge your local Claude Code setup into Codex so both stay equally effective.

`cc-codex-bridge` reads the Claude Code setup on your machine — plugins, skills, agents, commands, and instructions — and generates equivalent Codex artifacts. You install/set up/author once in Claude Code; the bridge keeps Codex in sync.

## What It Reads

The bridge discovers these canonical Claude Code sources:

| Source | Location |
|--------|----------|
| Installed plugins | `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/` |
| Plugin skills | `<plugin>/skills/<skill-name>/SKILL.md` |
| Plugin agents | `<plugin>/agents/<agent-name>.md` |
| Plugin commands | `<plugin>/commands/<command-name>.md` |
| User skills | `~/.claude/skills/<skill-name>/SKILL.md` |
| User agents | `~/.claude/agents/<agent-name>.md` |
| User commands | `~/.claude/commands/<command-name>.md` |
| Project skills | `.claude/skills/<skill-name>/SKILL.md` |
| Project agents | `.claude/agents/<agent-name>.md` |
| Project commands | `.claude/commands/<command-name>.md` |
| User global instructions | `~/.claude/CLAUDE.md` |
| Project instructions | `AGENTS.md` (hand-authored, never modified) |

Plugin enablement is checked by running `claude plugins list --json`, so only plugins you have enabled in Claude Code are bridged.

When multiple installed versions of the same plugin exist, the highest semantic version is selected.

## What It Generates

### Project-local outputs

| Artifact | Description |
|----------|-------------|
| `CLAUDE.md` | Shim containing `@AGENTS.md` so Codex reads the shared project instructions |
| `.codex/agents/*.toml` | Agent files from project-scope Claude agents |
| `.codex/skills/<name>/` | Skill directories from project-scope Claude skills |

### User-global outputs

| Artifact | Description |
|----------|-------------|
| `~/.codex/skills/<name>/` | Skill directories from plugin and user Claude skills |
| `~/.codex/agents/*.toml` | Agent files from plugin and user Claude agents |
| `~/.codex/prompts/*.md` | Prompt files from plugin, user, and project Claude commands |
| `~/.codex/AGENTS.md` | Global instructions bridged from `~/.claude/CLAUDE.md` |

### Bridge-internal state

| Artifact | Description |
|----------|-------------|
| `~/.cc-codex-bridge/registry.json` | Global ownership registry for skills, agents, prompts, and vendored resources |
| `~/.cc-codex-bridge/projects/<hash>/state.json` | Per-project managed-file tracking |
| `~/.cc-codex-bridge/plugins/<marketplace>-<plugin>/` | Vendored plugin resource directories (scripts, references, etc.) |

All generated outputs are derived artifacts. Do not hand-edit them — change the Claude Code source and re-run `reconcile`.

## How Claude Code Maps to Codex

The bridge translates between two different extensibility models. This table shows how each Claude Code mechanic maps to its Codex equivalent:

| Claude Code | Codex | How the bridge translates |
|-------------|-------|---------------------------|
| **Skills** (`SKILL.md` in a directory) | **Skills** (`SKILL.md` in a directory) | Copied as self-contained directory trees under `~/.codex/skills/`. The `name:` frontmatter is rewritten to match the generated directory name. Plugin resource paths (`$PLUGIN_ROOT`, sibling references) are rewritten to vendored absolute paths. |
| **Agents** (`.md` with frontmatter) | **Agents** (`.toml` with role config) | Translated into self-contained `.toml` files. `name` and `description` map directly. The markdown body becomes `developer_instructions`. Claude `tools` are mapped to Codex `sandbox_mode` (`Bash`/`Write`/`Edit` → `workspace-write`, read-only tools → `read-only`). |
| **Commands** (`.md` slash commands) | **Prompts** (`.md` in `~/.codex/prompts/`) | Translated into native Codex prompt files. `description` and `argument-hint` frontmatter are preserved. `$ARGUMENTS` and positional args (`$1`-`$9`) pass through natively — Codex supports the same syntax. `allowed-tools` is dropped (Codex controls tool access differently). |
| **`CLAUDE.md`** (project instructions) | **`AGENTS.md`** (project instructions) | The bridge generates `CLAUDE.md` as the shim `@AGENTS.md` so both CLIs read the same shared instructions. `AGENTS.md` is the canonical source, never modified by the bridge. |
| **`~/.claude/CLAUDE.md`** (global instructions) | **`~/.codex/AGENTS.md`** (global instructions) | Content is copied with a bridge ownership sentinel appended. |
| **Plugin resources** (`scripts/`, `references/`, etc.) | Vendored under `~/.cc-codex-bridge/plugins/` | Resource directories referenced by skills, agents, or commands via `$PLUGIN_ROOT` or `${CLAUDE_PLUGIN_ROOT}` are copied to bridge-internal storage. All references in generated content are rewritten to absolute vendored paths. Transitive dependencies are detected and vendored automatically. |

### Naming conventions

| Entity | Naming rule |
|--------|-------------|
| Skills | Bare directory name (e.g., `code-review`). Collisions get `-alt`, `-alt-2` suffixes. User skills win the bare name over plugin skills. |
| Agents | `<marketplace>-<plugin>-<agent>.toml` for plugins, `user-<agent>.toml` for user scope, `project-<agent>.toml` for project scope. |
| Prompts | Bare command filename (e.g., `review.md`). Project commands get a `--<project-dirname>` suffix (e.g., `build--my-app.md`). Collisions resolved with `-alt` suffixes. |

## Install

### From GitHub Releases (recommended)

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

The installer downloads a self-contained wheelhouse bundle and installs with `pip --no-index` — no PyPI needed. Supports Python 3.11, 3.12, 3.13, and 3.14.

To install a specific version:

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | \
  bash -s -- --version v0.15.0
```

### From a local checkout

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .
```

### Verify the setup

```bash
cc-codex-bridge doctor
```

## Usage

### Single project

```bash
cc-codex-bridge validate --project .           # check without writing
cc-codex-bridge reconcile --dry-run --project . # preview changes
cc-codex-bridge reconcile --dry-run --diff --project .  # preview with diffs
cc-codex-bridge status --project .             # inspect current state
cc-codex-bridge reconcile --project .          # apply changes
```

### All projects

```bash
cc-codex-bridge reconcile --all                # reconcile all registered projects
cc-codex-bridge reconcile --all --dry-run      # preview
cc-codex-bridge status --all                   # bulk status overview
cc-codex-bridge validate --all                 # bulk validation
```

Bulk operations merge scan-discovered projects (from `~/.cc-codex-bridge/config.toml`) with previously reconciled projects from the registry.

Configure scan discovery in `~/.cc-codex-bridge/config.toml`:

```toml
scan_paths = ["~/Work/projects/*"]
exclude_paths = ["~/Work/projects/scratch"]
```

### Cleanup

```bash
cc-codex-bridge clean --project .              # remove artifacts from one project
cc-codex-bridge uninstall                      # remove all bridge artifacts from the machine
cc-codex-bridge uninstall --dry-run            # preview what would be removed
```

### Activity log

State-changing operations (reconcile, clean, install-launchagent) are logged as daily JSONL files under `~/.cc-codex-bridge/logs/`.

```bash
cc-codex-bridge log show                     # last 7 days
cc-codex-bridge log show --days 30           # last 30 days
cc-codex-bridge log show --since 2025-01-01  # from a specific date
cc-codex-bridge log show --project .         # filter by project
cc-codex-bridge log show --action reconcile  # filter by action
cc-codex-bridge log show --json              # raw JSONL output
cc-codex-bridge log prune                    # manually prune expired logs
```

Configure log retention in `~/.cc-codex-bridge/config.toml`:

```toml
[log]
log_retention_days = 90  # default
```

Expired logs are automatically pruned after each logged operation.

### Scheduled reconciliation (macOS)

```bash
cc-codex-bridge install-launchagent            # install LaunchAgent plist
cc-codex-bridge print-launchagent              # preview plist without installing
```

The LaunchAgent runs `reconcile --all` every 30 minutes. Set a different interval with `--interval <seconds>` when installing.

## Exclusions

Skip specific plugins, skills, agents, or commands with CLI flags (repeatable):

```bash
cc-codex-bridge reconcile --project . \
  --exclude-plugin marketplace/plugin \
  --exclude-skill marketplace/plugin/skill \
  --exclude-agent marketplace/plugin/agent.md \
  --exclude-command marketplace/plugin/command.md
```

Persist exclusions globally in `~/.cc-codex-bridge/config.toml` (applies to all projects):

```toml
[exclude]
plugins = ["vladolaru-claude-code-plugins/yoloing-safe"]
```

Or per project in `.codex/bridge.toml`:

```toml
[exclude]
plugins = ["market/pirategoat-tools"]
skills = ["market/prompt-engineer/internal-cc-only"]
agents = ["market/prompt-engineer/reviewer.md"]
commands = ["market/plugin/debug.md"]
```

Global and project exclusions are **combined** (both apply). CLI `--exclude-*` flags **replace** the combined set for that entity kind in the current run.

## Ownership and Safety

The reconcile engine is conservative. It tracks which files it created and refuses to overwrite anything it did not generate.

**Never modified:**
- `AGENTS.md` (hand-authored project instructions)
- Hand-authored `CLAUDE.md` files
- Hand-authored `.codex/agents/*.toml` files
- Existing non-generated skill directories or prompt files

**Generator-owned (tracked in registry and state):**
- `CLAUDE.md` (only when it is the `@AGENTS.md` shim)
- `.codex/agents/*.toml` (generated from Claude agents)
- `~/.codex/skills/`, `~/.codex/agents/`, `~/.codex/prompts/` (generated entries)
- `~/.codex/AGENTS.md` (bridged from `~/.claude/CLAUDE.md`)

**Safety guarantees:**
- Files are written atomically (temp file + rename) to prevent partial reads
- Stale artifacts are cleaned up when their source is removed
- Reconcile is idempotent — running without source changes is a no-op
- Content hashing detects and rejects cross-project conflicts for shared global artifacts
- Symlinked targets are rejected to prevent writes outside expected directories

## Contributor Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
pytest tests -q
```

## Release

```bash
make release VERSION=X.Y.Z
```

Run from a clean `main` checkout using the repository `.venv`. Update `CHANGELOG.md` and both version declarations (`pyproject.toml`, `__init__.py`) first.

The release workflow creates a GitHub Release with a self-contained wheelhouse bundle, `install.sh`, and `SHA256SUMS`.

## License

MIT. See [`LICENSE`](LICENSE).
