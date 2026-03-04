# DESIGN.md

This document is the canonical architectural source for the current implemented state of `claude-code-codex-bridge`.

It is intended for agent use. Treat it as normative for how the system is structured today. When code changes alter architecture, data flow, ownership rules, or core constraints, update this file in the same change.

For supporting context:

- use `AGENTS.md` for shared agent/runtime instructions
- use `.claude/docs/analysis/` for investigations
- use `.claude/docs/plans/*-spec.md` for target contracts and constraints
- use `.claude/docs/plans/*-implementation-plan.md` for sequencing and roadmap

`DESIGN.md` should describe the current implemented architecture, not aspirational future behavior.

## 1. Purpose

`cc-codex-bridge` is a local generator and reconcile tool that projects a locally installed Claude Code setup into Codex-compatible artifacts.

The core architectural goal is:

- keep Claude-side sources canonical
- generate Codex-facing compatibility artifacts
- avoid maintaining a second hand-authored Codex skill/agent ecosystem

## 2. Canonical Source Hierarchy

The project has a strict source-of-truth split.

### Hand-authored canonical sources

These are authoritative inputs:

- project `AGENTS.md`
- installed Claude plugin skills discovered from the local Claude plugin cache
- installed Claude plugin agents discovered from the local Claude plugin cache
- plugin-local resources attached to those skills, such as `scripts/`, `references/`, `assets/`, and `agents/`

### Generated outputs

These are derived artifacts and must not become hand-maintained sources:

- `CLAUDE.md`
- `.codex/config.toml`
- `.codex/prompts/agents/*.md`
- `.codex/interop-state.json`
- `~/.codex/skills/*`

### Authority rule

If a behavior is described differently in multiple docs:

1. the implementation is authoritative for what exists now
2. this file should be updated to match that implementation
3. plans/specs describe intent and constraints, but do not override shipped behavior until the code changes

## 3. System Boundaries

### In scope

- project discovery from the current directory or `--project`
- Claude plugin discovery from the installed local Claude cache
- selection of the latest installed plugin version by semantic version
- translation of Claude agents into Codex role prompts and config entries
- translation of Claude skills into self-contained Codex skills
- safe reconcile of generated project files and generated Codex skill directories
- state tracking for generator-owned outputs
- macOS LaunchAgent rendering and installation for scheduled reconcile runs

### Out of scope

- changing Claude Code behavior
- hand-authored Codex-specific skills
- project-local Codex skill mirrors
- filesystem-wide project discovery
- watcher mode
- automatic `launchctl bootstrap`
- runtime execution of Claude slash commands

## 4. Runtime Model

The runtime is a deterministic pipeline:

1. resolve the target project root by searching upward for `AGENTS.md`
2. discover installed Claude plugins from `~/.claude/plugins/cache` or `--cache-dir`
3. choose the highest semantic version for each `<marketplace>/<plugin>`
4. translate plugin agents into `GeneratedAgentRole` objects
5. translate plugin skills into `GeneratedSkill` trees
6. render project-local Codex prompt files and `.codex/config.toml`
7. decide whether `CLAUDE.md` can be created or preserved as an `@AGENTS.md` shim
8. build a full desired state for project files plus Codex skill directories
9. diff or reconcile that desired state with ownership and rollback protections

Every CLI command except the LaunchAgent commands runs through the same discovery and translation pipeline first.

## 5. Implemented Output Contract

### Project-local outputs

- `CLAUDE.md`
  - only valid generated content is exactly `@AGENTS.md` plus a trailing newline
- `.codex/config.toml`
  - rendered inline multi-agent config
- `.codex/prompts/agents/*.md`
  - prompt bodies derived from Claude agent markdown bodies
- `.codex/interop-state.json`
  - ownership and selection state for reconcile safety

### User-global outputs

- `~/.codex/skills/<generated-skill-name>/`
  - self-contained Codex skill directories derived from Claude skills

### Local-only rule

Generated `.codex/*` outputs are local runtime artifacts. They are not hand-authored project source.

## 6. Ownership and Safety Model

The reconcile engine is conservative by design.

### Never overwritten unless already generator-owned

- project `AGENTS.md`
- hand-authored `CLAUDE.md`
- hand-authored `.codex/config.toml`
- hand-authored `.codex/prompts/agents/*.md`
- non-generated directories under `~/.codex/skills/`

### Generator-owned artifacts

Ownership is tracked through `.codex/interop-state.json`.

The state file records:

- project root
- Codex home path
- selected plugin identities
- managed project-relative file paths
- managed Codex skill directory names
- state version

### Safety rules

- project files are never overwritten unless they were previously recorded as managed
- symlinked managed project targets are rejected
- non-directory skill targets are rejected
- non-generated existing skill directories are rejected
- stale managed outputs are removed when no longer desired
- writes are staged and then swapped into place
- failures during apply trigger rollback of already-applied changes

## 7. Discovery Architecture

Discovery lives in `src/cc_codex_bridge/discover.py`.

### Project discovery

- the project root is the nearest ancestor containing `AGENTS.md`
- if `--project` points to a file, discovery starts from its parent
- missing `AGENTS.md` is a hard failure

### Claude plugin discovery

- plugins are read from `~/.claude/plugins/cache` by default
- the cache root can be overridden with `--cache-dir`
- structure is expected as `<cache>/<marketplace>/<plugin>/<version>/`
- only directories with valid semantic-version names are considered plugin versions
- malformed version directories are ignored
- if a plugin has no valid semantic versions, discovery fails
- if the cache is empty, discovery fails

### Version selection

- versions are grouped by `<marketplace>/<plugin>`
- the highest semantic version wins
- prerelease precedence follows semver rules implemented in `SemVer`

### Symlink rule

Installed plugin version directories are resolved with `Path.resolve()`. If the installed path is a symlink into a working repo, the resolved repo path becomes the effective source path, while the installed path is still recorded.

## 8. Translation Architecture

### 8.1 Claude shim planning

`src/cc_codex_bridge/claude_shim.py` decides what to do with `CLAUDE.md`.

Allowed outcomes:

- `create`
- `preserve`
- `fail`

`CLAUDE.md` is only generator-safe when:

- it does not exist
- it already contains `@AGENTS.md`
- it is a symlink to `AGENTS.md`

Anything else is treated as hand-authored and causes failure.

### 8.2 Agent translation

`src/cc_codex_bridge/translate_agents.py` converts Claude agent markdown files into `GeneratedAgentRole`.

Required Claude frontmatter:

- `name`
- `description`

Optional handled fields:

- `model`
- `tools`

Current mapping rules:

- role name = `<plugin_name>_<normalized_agent_name>`
- prompt file path = `.codex/prompts/agents/<plugin>-<agent-name>.md`
- prompt body = markdown body after frontmatter, normalized to end with a trailing newline when non-empty
- model = fixed default `gpt-5.3-codex`
- original Claude `model` is preserved only as metadata in the generated config comment

Current tool translation table:

- `Read` -> `read`
- `Glob` -> `glob`
- `Grep` -> `grep`
- `Write` -> `write`
- `Bash` -> `bash`
- `WebSearch` -> `web_search`

Unknown Claude tools are ignored, not preserved.

The frontmatter parser is intentionally minimal and supports the shapes used by current test fixtures and known docs:

- scalar values
- list values
- simple nested maps
- folded and literal block scalars

It is not a general YAML parser.

### 8.3 Codex config rendering

`src/cc_codex_bridge/render_codex_config.py` renders:

- prompt file content map
- inline `.codex/config.toml`

The config is deterministic:

- roles are sorted
- tools are sorted
- output contains a generated-file header
- prompt references are project-local `.codex/...` paths

### 8.4 Skill translation

`src/cc_codex_bridge/translate_skills.py` converts Claude skills into self-contained `GeneratedSkill` trees.

Current copied skill content:

- root files from the skill directory
- `scripts/`
- `references/`
- `assets/`
- `agents/`

Current ignored noise:

- `.DS_Store`
- `__pycache__`
- `.pyc`

Current skill rules:

- every source skill must have `SKILL.md`
- `SKILL.md` frontmatter must include `name`
- generated `SKILL.md` has its `name:` rewritten to match the generated install directory name
- skill trees are materialized as complete directory snapshots

Current relocation behavior:

- references to `<skill base directory>/../..` are rewritten to `<skill base directory>/_plugin`
- if plugin-root scripts are referenced this way, the plugin `scripts/` tree is vendored into `_plugin/scripts`
- sibling skill references matching `../<skill>/...` are rewritten to `_plugin/skills/<skill>/...`
- referenced sibling skill trees are vendored into `_plugin/skills/<skill>/`

### Skill naming

Base generated install directory:

- `<plugin_name>-<skill_directory_name>`

Conflict handling:

- if multiple generated skills would share the same base directory name, the name is expanded to `<marketplace>-<plugin_name>-<skill_directory_name>`

## 9. Reconcile Architecture

Reconcile lives in `src/cc_codex_bridge/reconcile.py`.

### Desired state

`DesiredState` is the full output model for one run:

- project root
- Codex home
- project files with desired bytes
- generated skills
- selected plugins
- path to the state file

### Diff model

Diffs are represented as `Change` records with:

- `kind`
- `path`
- optional `detail`

Supported kinds in current reporting:

- `create`
- `update`
- `remove`

### Reconcile flow

1. load previous state if present
2. compute desired project file changes
3. compute desired Codex skill directory changes
4. validate ownership constraints
5. stage all file and directory replacements in temporary roots
6. write or update the state file as part of the same transaction
7. swap staged content into place
8. remove stale managed outputs
9. finalize by deleting backups
10. rollback if any apply step fails

### Atomicity model

Project files are written atomically with temporary files in the destination directory.

Skill directories are staged as full directory trees, then swapped using rename-based replacement with backups. This is transactional within the assumptions of a local filesystem and the current process boundaries.

### Idempotence rule

If the desired state matches current managed outputs, reconcile becomes a no-op except that it may still write the state file when required to bring state into sync.

## 10. CLI Architecture

The CLI lives in `src/cc_codex_bridge/cli.py`.

### Main commands

- `validate`
  - run discovery, translation, and rendering in memory
  - print a summary
- `dry-run`
  - compute reconcile changes without writing
  - print change summary
- `diff`
  - compute reconcile changes without writing
  - print change summary plus unified diffs for managed text files
- `reconcile`
  - apply the desired state to disk
  - print summary and applied changes

### LaunchAgent commands

- `print-launchagent`
  - render a LaunchAgent plist to stdout
- `install-launchagent`
  - write the plist into a LaunchAgents directory and print the `launchctl bootstrap` next step

### CLI invariants

- all non-LaunchAgent commands share the same pipeline and error types
- `DiscoveryError`, `TranslationError`, and `ReconcileError` are surfaced as user-facing errors with exit code `1`
- successful commands return `0`

## 11. LaunchAgent Architecture

LaunchAgent support lives in `src/cc_codex_bridge/install_launchagent.py`.

Current design:

- macOS scheduling is supported through `launchd`
- the generated job runs `reconcile`
- the job always includes `--project <resolved-project-root>`
- optional `--cache-dir` and `--codex-home` overrides are embedded when supplied
- the plist uses `RunAtLoad` and `StartInterval`
- logs go to `~/Library/Logs/codex-interop/` by default

The tool installs the plist file but does not run `launchctl` automatically.

## 12. Module Map

Current runtime module responsibilities:

- `cli.py`
  - argument parsing, command dispatch, summary/error reporting
- `discover.py`
  - project root resolution and installed-plugin discovery
- `model.py`
  - core dataclasses and domain-specific error types
- `claude_shim.py`
  - `CLAUDE.md` ownership-safe shim planning
- `translate_agents.py`
  - Claude agent parsing and Codex role translation
- `render_codex_config.py`
  - prompt-file and inline config rendering
- `translate_skills.py`
  - Codex skill translation and skill tree materialization helpers
- `reconcile.py`
  - desired-state modeling, diffing, apply, rollback, report formatting
- `state.py`
  - managed-state serialization and validation
- `install_launchagent.py`
  - LaunchAgent label generation, plist rendering, plist installation

## 13. Testing Strategy

Tests are fixture-driven and isolated under `tests/`.

The suite currently verifies:

- project discovery rules
- semver behavior
- plugin symlink resolution
- `CLAUDE.md` shim safety
- agent translation and deterministic rendering
- skill translation, relocation rewriting, vendoring, and collision handling
- reconcile idempotence, stale cleanup, rollback safety conditions, and diff reporting
- CLI command behavior
- LaunchAgent rendering and installation

The test suite is the executable check for the invariants described in this file.

## 14. Current Constraints and Known Simplifications

These are current implemented simplifications, not necessarily permanent design ideals:

- only installed Claude plugins are inputs
- user-level Claude skills and agents are described in the repo docs but are not yet implemented as a discovered input source in the current codebase
- agent model mapping is fixed to one default Codex model
- unknown Claude tools are dropped rather than preserved or warned on
- frontmatter parsing is custom and intentionally narrow
- generated Codex config is inline rather than split across multiple files
- LaunchAgent scheduling is supported; watcher mode is not

Any change to these constraints should update this file.

## 15. Maintenance Rules

When making architectural changes, update `DESIGN.md` in the same change if you alter any of:

- canonical inputs or generated outputs
- ownership rules
- discovery paths or selection rules
- translation rules
- reconcile semantics
- CLI command contract
- LaunchAgent behavior
- major module responsibilities

If a code change would make this file inaccurate, the change is incomplete until `DESIGN.md` is updated.
