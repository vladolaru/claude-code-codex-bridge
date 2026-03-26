# claude-code-codex-bridge

[![Latest release](https://img.shields.io/github/v/release/vladolaru/claude-code-codex-bridge)](https://github.com/vladolaru/claude-code-codex-bridge/releases/latest)

Automatically bridge your local Claude Code setup into Codex so both stay equally effective.

`cc-codex-bridge` reads the Claude Code setup on your machine — plugins, skills, agents, commands, and instructions — and generates equivalent Codex artifacts. You install/set up/author once in Claude Code; the bridge keeps Codex in sync.

**This is a one-way bridge: Claude Code → Codex.** Changes made directly in Codex (editing generated files, adding skills manually under `~/.codex/`) are not reflected back into Claude Code and will be overwritten on the next reconcile.

## Quick setup

### 1. Install

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

### 2. Verify the environment

```bash
cc-codex-bridge doctor
```

This checks that Python, the `claude` CLI, and the Codex home directory are all accessible. Fix any reported errors before continuing.

### 3. Reconcile your first project

```bash
cd ~/path/to/your/project
cc-codex-bridge reconcile
```

The bridge reads your Claude Code plugins, skills, agents, and commands and writes the Codex equivalents under `.codex/` and `~/.codex/`. Run `status` any time to see what's in sync.

### 4. Register all your projects for bulk operations

Projects are registered individually each time you run `reconcile` inside them. To avoid doing that one by one, add a scan path glob that covers your project directories:

```bash
cc-codex-bridge config scan add "~/path/to/projects/*"
cc-codex-bridge reconcile --all
```

`reconcile --all` then discovers every matching project and syncs them all in one shot.

### 5. Keep everything in sync automatically (macOS)

Install a background agent that runs `reconcile --all` every 30 minutes:

```bash
cc-codex-bridge autosync install
```

### 6. Confirm everything is in sync

```bash
cc-codex-bridge status --all
```

You should see `STATUS: in_sync` for every project. Done — Claude Code and Codex now share the same plugins, skills, agents, and instructions.

### Staying up to date

```bash
cc-codex-bridge upgrade           # upgrade to the latest release
cc-codex-bridge upgrade --check   # check without installing
```

`doctor` also reports if a newer version is available each time you run it.

---

## What it reads

A **project** is any directory the bridge treats as a Claude Code project root — one that contains `AGENTS.md`, `CLAUDE.md`, or a `.claude/` directory. The bridge maintains per-project state, writes project-local Codex artifacts (`.codex/`) there, and tracks it in the registry so `--all` operations can find it again.

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
| Project instructions | `AGENTS.md` (created by bootstrap if absent, never overwritten once it exists) |

Plugin enablement is checked by running `claude plugins list --json`, so only plugins you have enabled in Claude Code are bridged.

When multiple installed versions of the same plugin exist, the highest semantic version is selected.

## What it generates

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

## How Claude Code concepts map to Codex ones

The bridge translates between two different extensibility models. This table shows how each Claude Code mechanic maps to its Codex equivalent:

| Claude Code | Codex | How the bridge translates |
|-------------|-------|---------------------------|
| **Skills** (`SKILL.md` in a directory) | **Skills** (`SKILL.md` in a directory) | Copied as self-contained directory trees under `~/.codex/skills/`. The `name:` frontmatter is rewritten to match the generated directory name. Plugin resource paths (`$PLUGIN_ROOT`, sibling references) are rewritten to vendored absolute paths. |
| **Agents** (`.md` with frontmatter) | **Agents** (`.toml` with role config) | Translated into self-contained `.toml` files. `name` and `description` map directly. The markdown body becomes `developer_instructions`. Claude `tools` are mapped to Codex `sandbox_mode` (`Bash`/`Write`/`Edit` → `workspace-write`, read-only tools → `read-only`). |
| **Commands** (`.md` slash commands) | **Prompts** (`.md` in `~/.codex/prompts/`) | Translated into native Codex prompt files. `description` and `argument-hint` frontmatter are preserved. `$ARGUMENTS` and positional args (`$1`-`$9`) pass through natively — Codex supports the same syntax. `allowed-tools` is dropped (Codex controls tool access differently). |
| **`CLAUDE.md`** (project instructions) | **`AGENTS.md`** (project instructions) | The bridge generates `CLAUDE.md` as the shim `@AGENTS.md` so both CLIs read the same shared instructions. `AGENTS.md` is the canonical source; the bridge creates it during bootstrap but never overwrites it once it exists. |
| **`~/.claude/CLAUDE.md`** (global instructions) | **`~/.codex/AGENTS.md`** (global instructions) | Content is copied with a bridge ownership sentinel appended. |
| **Plugin resources** (`scripts/`, `references/`, etc.) | Vendored under `~/.cc-codex-bridge/plugins/` | Resource directories referenced by skills, agents, or commands via `$PLUGIN_ROOT` or `${CLAUDE_PLUGIN_ROOT}` are copied to bridge-internal storage. All references in generated content are rewritten to absolute vendored paths. Transitive dependencies are detected and vendored automatically. |

### Naming conventions

| Entity | Naming rule |
|--------|-------------|
| Skills | Bare directory name (e.g., `code-review`). Collisions get `-alt`, `-alt-2` suffixes. User skills win the bare name over plugin skills. |
| Agents | Bare agent file stem (e.g., `reviewer.toml`). Collisions get `-alt`, `-alt-2` suffixes. User/project agents win the bare name over plugin agents. |
| Prompts | Bare command filename (e.g., `review.md`). Project commands get a `--<project-dirname>` suffix (e.g., `build--my-app.md`). Collisions resolved with `-alt` suffixes. |

## Install

### From GitHub releases (recommended)

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

The installer downloads a self-contained wheelhouse bundle and installs with `pip --no-index` — no PyPI needed. Supports Python 3.11, 3.12, 3.13, and 3.14.

To install a specific version:

```bash
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | \
  bash -s -- --version v0.15.0
```

### Upgrade

```bash
cc-codex-bridge upgrade           # upgrade to the latest release
cc-codex-bridge upgrade --check   # check for a newer version without installing
```

`upgrade` fetches the latest release from GitHub, compares it with the installed version, and runs the official install script in place if a newer version is available. After upgrading, run `cc-codex-bridge reconcile --all` to pick up any changes in how the new version generates Codex artifacts.

`doctor` also reports if a newer version is available as part of its environment checks.

`upgrade` does not work with development installs (editable installs via `pip install -e .`). To switch from a development install to a release install, run the install script in a shell without an active venv:

```bash
deactivate  # or open a new terminal
curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash
```

To update a development install, use git instead:

```bash
git pull && pip install -e .
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
cc-codex-bridge status --project .             # inspect sync state (discovery + pending changes)
cc-codex-bridge reconcile --dry-run --project . # preview changes
cc-codex-bridge reconcile --dry-run --diff --project .  # preview with diffs
cc-codex-bridge reconcile --project .          # apply changes
```

### All projects

```bash
cc-codex-bridge reconcile --all                # reconcile all registered projects
cc-codex-bridge reconcile --all --dry-run      # preview
cc-codex-bridge status --all                   # bulk status overview
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

### Configuration

```bash
cc-codex-bridge config show                       # display effective config
cc-codex-bridge config show --global              # global config only
cc-codex-bridge config show --json                # machine-readable output
cc-codex-bridge config check                      # audit config against environment

cc-codex-bridge config scan add "~/Work/projects/*"    # add a scan path
cc-codex-bridge config scan remove "~/Work/projects/*" # remove a scan path
cc-codex-bridge config scan list                  # list current scan paths

cc-codex-bridge config exclude add skill security-reviewer   # add exclusion
cc-codex-bridge config exclude add plugin market/plugin      # add plugin exclusion
cc-codex-bridge config exclude remove skill security-reviewer
cc-codex-bridge config exclude list               # list current exclusions

cc-codex-bridge config log set-retention 30       # set log retention to 30 days
```

Omit values for interactive guided flows (requires a terminal):

```bash
cc-codex-bridge config exclude add                # pick kind, then entity
cc-codex-bridge config scan add                   # prompted for glob
```

Config commands auto-detect scope: inside a project targets `.codex/bridge.toml`, otherwise `~/.cc-codex-bridge/config.toml`. Use `--global` to force global scope.

### Scheduled reconciliation (macOS)

```bash
cc-codex-bridge autosync install    # set up automatic background sync
cc-codex-bridge autosync uninstall  # stop and remove it
cc-codex-bridge autosync status     # check whether it is running
```

Runs `reconcile --all` every 30 minutes. Set a different interval with `--interval <seconds>`. `autosync uninstall` stops scheduled reconciliation without touching any reconciled project artifacts.

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
plugins = ["my-marketplace/plugin-name"]
```

Or per project in `.codex/bridge.toml`:

```toml
[exclude]
plugins = ["market/heavy-plugin"]
skills = ["market/prompt-engineer/internal-cc-only"]
agents = ["market/prompt-engineer/reviewer.md"]
commands = ["market/plugin/debug.md"]
```

Global and project exclusions are **combined** (both apply). CLI `--exclude-*` flags **replace** the combined set for that entity kind in the current run.

## Ownership and safety

The reconcile engine is conservative. It tracks which files it created and refuses to overwrite anything it did not generate.

**Never modified:**
- Hand-authored `CLAUDE.md` files
- Hand-authored `.codex/agents/*.toml` files
- Existing non-generated skill directories or prompt files

**Generator-owned (tracked in registry and state):**
- `AGENTS.md` (created by bootstrap from original `CLAUDE.md`; never overwritten once it exists; removed by `clean` only during bootstrap reversal when unedited)
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

## Contributor setup

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
