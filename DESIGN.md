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
- optional project exclusion config at `.codex/bridge.toml`
- installed Claude plugin skills discovered from the local Claude plugin cache
- installed Claude plugin agents discovered from the local Claude plugin cache
- plugin-local resources attached to those skills, such as `scripts/`, `references/`, `assets/`, and `agents/`
- user-level Claude skills from `~/.claude/skills/`
- user-level Claude agents from `~/.claude/agents/`
- project-level Claude skills from `.claude/skills/`
- project-level Claude agents from `.claude/agents/`
- user-level global instructions from `~/.claude/CLAUDE.md`

### Generated outputs

These are derived artifacts and must not become hand-maintained sources:

- `CLAUDE.md`
- `.codex/config.toml`
- `.codex/prompts/agents/*.md`
- `.codex/claude-code-bridge-state.json`
- `~/.codex/claude-code-bridge-global-state.json`
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
- user-level skill and agent discovery from `~/.claude/skills/` and `~/.claude/agents/`
- project-level skill and agent discovery from `.claude/skills/` and `.claude/agents/`
- user-level global instructions discovery from `~/.claude/CLAUDE.md`
- selection of the latest installed plugin version by semantic version
- translation of Claude agents into Codex role prompts and config entries
- translation of Claude skills into self-contained Codex skills
- safe reconcile of generated project files and generated Codex skill directories
- state tracking for generator-owned outputs
- project-level artifact cleanup via `clean`
- machine-level full artifact removal via `uninstall`
- macOS LaunchAgent rendering and installation for scheduled reconcile runs

### Out of scope

- changing Claude Code behavior
- hand-authored Codex-specific skills
- filesystem-wide project discovery
- watcher mode
- automatic `launchctl bootstrap`
- runtime execution of Claude slash commands

## 4. Runtime Model

The runtime is a deterministic pipeline:

1. resolve the target project root by searching upward for `AGENTS.md`
2. discover installed Claude plugins from `~/.claude/plugins/cache` or `--cache-dir`
3. choose the highest semantic version for each `<marketplace>/<plugin>`
4. discover user-level skills, agents, and global instructions from `~/.claude/` (or `--claude-home`)
5. discover project-level skills and agents from `.claude/`
6. load optional `.codex/bridge.toml` exclusions and merge any CLI exclusion flags
7. filter discovered plugins/skills/agents by the effective exclusion set
8. translate plugin agents into `GeneratedAgentRole` objects
9. translate standalone user and project agents into `GeneratedAgentRole` objects
10. translate plugin and user skills into `GeneratedSkill` trees (global registry)
11. translate project skills into project-local `GeneratedSkill` trees
12. merge all agent roles and render project-local Codex prompt files and `.codex/config.toml`
13. decide whether `CLAUDE.md` can be created or preserved as an `@AGENTS.md` shim
14. build a full desired state for project files, Codex skill directories, and global instructions
15. inspect/preview or reconcile that desired state with ownership and rollback protections

The reconcile pipeline is shared by `validate`, `status`, and `reconcile`.

Utility commands such as `doctor` and the LaunchAgent commands are intentionally separate from the reconcile pipeline.

## 5. Implemented Output Contract

### Project-local outputs

- `CLAUDE.md`
  - only valid generated content is exactly `@AGENTS.md` plus a trailing newline
- `.codex/config.toml`
  - rendered inline multi-agent config
- `.codex/prompts/agents/*.md`
  - prompt bodies derived from Claude agent markdown bodies
- `.codex/claude-code-bridge-state.json`
  - project-local ownership state for reconcile safety
  - tracks managed project files, managed project skill directory names, and version

### User-global outputs

- `~/.codex/claude-code-bridge-global-state.json`
  - global generated-skill ownership registry keyed by install directory name
- `~/.codex/skills/<generated-skill-name>/`
  - self-contained Codex skill directories derived from Claude plugin and user skills
- `~/.codex/AGENTS.md`
  - user-global Codex instructions bridged from `~/.claude/CLAUDE.md`
- `.codex/skills/<skill-name>/`
  - project-local Codex skill directories derived from Claude project skills

### Local-only rule

Generated `.codex/*` outputs are local runtime artifacts. They are not hand-authored project source.

## 6. Ownership and Safety Model

The reconcile engine is conservative by design.

### Never overwritten unless already generator-owned

- project `AGENTS.md`
- hand-authored project `.codex/bridge.toml` exclusion config
- hand-authored `CLAUDE.md`
- hand-authored `.codex/config.toml`
- hand-authored `.codex/prompts/agents/*.md`
- non-generated directories under `~/.codex/skills/`

### Generator-owned artifacts

Ownership is split across project-local and user-global state.

The project-local state file records:

- project root
- Codex home path
- managed project-relative file paths
- managed project skill directory names (tracked separately for directory-snapshot comparison)
- state version (currently 4)

The global registry records:

- generated skill install directory names
- deterministic content hashes for generated skill trees
- owning project roots for each generated skill directory
- a sorted list of all reconciled project roots (the `projects` list)

### Safety rules

- project files are never overwritten unless they were previously recorded as managed
- the state file may only authorize generator-owned project paths: `CLAUDE.md`, `.codex/config.toml`, `.codex/claude-code-bridge-state.json`, and `.codex/prompts/agents/*` (project skill directories are tracked separately via `managed_project_skill_dirs`)
- state-tracked project skill directories must be plain generated directory names; traversal, absolute paths, and nested paths are rejected as corrupted state
- generated project-relative paths are normalized and may not use absolute paths or `..` traversal
- corrupted or unexpected managed project paths in state are treated as a hard error — this applies to both reconcile and cleanup paths
- `status` and `reconcile --dry-run` validate the same planned write targets as mutating reconcile, so preview mode fails when apply would fail on containment checks
- state is rejected if it belongs to a different project root than the current reconcile target
- malformed state payload field types are treated as a hard error
- malformed state path fields are treated as a hard error
- symlinked managed project targets are rejected
- symlinked bridge state files are rejected
- malformed or symlinked global registry files are treated as a hard error
- non-directory skill targets are rejected during reconcile; during cleanup and uninstall, non-directory paths at skill locations are removed with `unlink()` instead of `rmtree()`
- existing skill directories are adopted only when their content matches the desired generated tree exactly
- conflicting content for an existing generated skill directory is a hard error
- generated skill directories are removed only when the global registry shows no remaining owners
- all write targets must resolve within their expected root (`project_root` for project files, `codex_home` for global files) after symlink resolution — this catches symlinked ancestor directories that would redirect operations outside the expected tree
- skill translation rejects symlinked resource directories, symlinked files (including SKILL.md), and symlinked subdirectories within resource directories
- project skills are tracked as managed directory names and compared using exact directory-snapshot matching, consistent with global skills
- project files are written atomically via temp-file-then-rename to avoid partial reads
- if a write fails mid-apply, the next idempotent reconcile run self-heals
- stale managed outputs are removed when no longer desired
- cleanup uses the codex_home recorded in bridge state, not the caller-supplied value, to ensure registry operations target the correct global state
- project-local generated skill directories are removed as whole directories, not individual files, consistent with global skill directory ownership
- if the configured Codex home changes, the current project's old registry claims are released from the previous Codex home during the same reconcile

## 7. Discovery Architecture

Discovery lives in `src/cc_codex_bridge/discover.py`.

### Project discovery

- the project root is the nearest ancestor containing `AGENTS.md`
- if `AGENTS.md` is not found, discovery falls back to the nearest ancestor containing `CLAUDE.md` as a project marker for bootstrap
- if `--project` points to a file, discovery starts from its parent
- missing both `AGENTS.md` and `CLAUDE.md` is a hard failure

### Claude plugin discovery

- plugins are read from `~/.claude/plugins/cache` by default
- the cache root can be overridden with `--cache-dir`
- structure is expected as `<cache>/<marketplace>/<plugin>/<version>/`
- only directories with valid semantic-version names are considered plugin versions
- malformed version directories are ignored
- if a plugin has no valid semantic-version subdirectories, discovery fails
- an empty or missing plugin cache returns an empty tuple (non-fatal) when other sources exist

### User-level discovery

- user-level skills are read from `~/.claude/skills/` (or `--claude-home`)
- each subdirectory containing `SKILL.md` is a discovered skill
- user-level agents are read from `~/.claude/agents/` as `*.md` files
- user-level global instructions are read from `~/.claude/CLAUDE.md` if present
- `--claude-home` overrides the `~/.claude` base path for all user-level discovery

### Project-level discovery

- project-level skills are read from `<project>/.claude/skills/`
- each subdirectory containing `SKILL.md` is a discovered skill
- project-level agents are read from `<project>/.claude/agents/` as `*.md` files

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
- `bootstrap`
- `fail`

`CLAUDE.md` is only generator-safe when:

- it does not exist
- it exactly matches `@AGENTS.md` plus a trailing newline
- it is a symlink to `AGENTS.md`

When `AGENTS.md` does not exist but `CLAUDE.md` is a regular file, the outcome is `bootstrap`. Only `reconcile` (non-dry-run) and `reconcile-all` execute the bootstrap, which copies `CLAUDE.md` to `AGENTS.md` and replaces `CLAUDE.md` with the shim. Read-only commands report that bootstrap is required and exit without mutating files.

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

- plugin agents: role name = `<marketplace>_<plugin>_<normalized_agent>`, prompt path = `.codex/prompts/agents/<marketplace>-<plugin>-<agent>.md`
- user agents: role name = `user_<normalized_agent>`, prompt path = `.codex/prompts/agents/user-<agent>.md`
- project agents: role name = `project_<normalized_agent>`, prompt path = `.codex/prompts/agents/project-<agent>.md`
- normalized generated names reject absolute paths, `..` traversal, and values that collapse to an empty identifier
- prompt body = markdown body after frontmatter, normalized to end with a trailing newline when non-empty
- model = fixed default `gpt-5.3-codex`
- original Claude `model` is preserved only as metadata in the generated config comment

Current tool translation table:

- `Read` -> `read`
- `Edit` -> `edit`
- `Glob` -> `glob`
- `Grep` -> `grep`
- `Write` -> `write`
- `Bash` -> `bash`
- `WebSearch` -> `web_search`

Unsupported Claude tools are hard diagnostics. They invalidate agent generation for that run instead of being silently dropped.

Installed-agent translation checks for duplicate `prompt_relpath` values as well as duplicate `role_name` values, consistent with standalone agent translation.

After merging all scopes (plugin, user, project), `validate_merged_roles()` checks uniqueness of both `role_name` and `prompt_relpath` across the full merged set. Per-scope checks provide early detection with better error messages; the post-merge check is the global invariant that prevents cross-scope collisions from producing silently corrupt output.

Frontmatter parsing is shared through `src/cc_codex_bridge/frontmatter.py`.

The parser extracts only frontmatter blocks and parses them with PyYAML's safe
loader.

Post-parse validation keeps the runtime contract narrow:

- top-level frontmatter must be a mapping
- mapping keys must be strings
- accepted values are strings plus nested lists/mappings composed of the same
  allowed value shapes
- malformed YAML and unsupported runtime shapes are hard translation errors

### 8.3 Codex config rendering

`src/cc_codex_bridge/render_codex_config.py` renders:

- prompt file content map
- inline `.codex/config.toml`

The config is deterministic:

- roles are sorted
- tools are sorted
- output contains a generated-file header
- prompt references are project-local `.codex/...` paths
- string values use TOML-compatible escaping, including multiline content

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
- symlinked resource directories, symlinked files (including `SKILL.md`), and symlinked subdirectories within resource directories are all rejected during translation — no symlinks anywhere in copied skill content

Current relocation behavior:

- sibling skill references matching `../<name>/` are resolved relative to the skill's disk location
- referenced sibling trees are vendored directly into the generated skill directory
- collisions between referenced sibling names and existing skill subdirectories are a hard error
- missing referenced siblings are treated as a hard translation error

### Skill naming

All generated skill install names use the bare skill directory name by default.
When multiple skills share the same directory name across sources, collisions are
resolved with deterministic suffixes:

- 1st (priority winner): `<skill-dir-name>` (bare)
- 2nd: `<skill-dir-name>-alt`
- 3rd: `<skill-dir-name>-alt-2`
- Nth: `<skill-dir-name>-alt-<N-1>`

Priority order for collision resolution:
1. User skills (marketplace `_user`)
2. Plugin skills, sorted by `(marketplace, plugin_name)`

Project skills are unchanged: `<skill-dir-name>` (raw) → project-local `.codex/skills/`.

All generated names must comply with the Agent Skills standard: max 64 characters,
lowercase a-z/0-9/hyphens only, no consecutive hyphens, matching the parent
directory name.

### Skill routing

User-level and plugin skills are installed to the global Codex skill registry at `~/.codex/skills/`. Project-level skills are installed to project-local `.codex/skills/` directories and tracked as managed project skill directory names in the bridge state. Both global and project skills use exact directory-snapshot comparison for change detection.

## 9. Reconcile Architecture

Reconcile lives in `src/cc_codex_bridge/reconcile.py`.

### Desired state

`DesiredState` is the full output model for one run:

- project root
- Codex home
- project files with desired bytes
- project skills (directory-snapshot comparison, installed to `.codex/skills/`)
- generated skills (global registry, installed to `~/.codex/skills/`)
- global instructions content (for `~/.codex/AGENTS.md`)
- path to the state file

### Diff model

Diffs are represented as `Change` records with:

- `kind`
- `path`
- optional `resource_kind`

Supported kinds in current reporting:

- `create`
- `update`
- `remove`

### Reconcile flow

1. load previous state if present
2. load the current global skill registry under the resolved Codex home
3. compute desired project file changes (config, prompts)
4. compute desired project skill directory mutations using directory-snapshot comparison
5. compute desired generated-skill claims and reconcile changes from registry ownership plus on-disk content hashes
6. compute desired global instructions changes for `~/.codex/AGENTS.md`
7. validate ownership constraints
8. write project file and skill directory changes directly
9. write global instructions file if needed
10. write updated global registry files
11. write the project-local state file
12. remove stale managed outputs whose last owner released them

`diff_desired_state()` additionally reports state file create/update changes that `reconcile_desired_state()` would perform, ensuring `status` and `reconcile --dry-run` show the same pending changes as a real reconcile.

### Write model

Project files and registry files are written atomically using temp-file-then-rename in the same directory.

Skill directories are written directly. On update, the old directory is removed before the new one is written.

If a write fails mid-apply, the next reconcile run detects the mismatch and repairs it. The reconcile pipeline is idempotent by design.

### Idempotence rule

If the desired state matches current managed outputs, reconcile becomes a no-op except that it may still write the state file when required to bring state into sync.

## 10. CLI Architecture

The CLI lives in `src/cc_codex_bridge/cli.py`.

### Main commands

- `doctor`
  - run machine-level environment checks without requiring a project
  - report Python version support, Claude cache visibility, Codex-home writability, LaunchAgents directory access, and CLI PATH visibility
  - support JSON output with `--json`
- `validate`
  - run discovery, translation, and rendering in memory
  - fail non-zero on unsupported-agent diagnostics
  - print a summary
- `reconcile --dry-run`
  - compute reconcile changes without writing
  - print change summary
- `reconcile --dry-run --diff`
  - compute reconcile changes without writing
  - print change summary plus unified diffs for managed text files
- `status`
  - compute reconcile changes without writing
  - report `in_sync` vs `pending_changes`
  - report `invalid` when agent translation contains unsupported Claude tools
  - report categorized project-file vs skill create/update/remove changes
  - include agent translation diagnostics in both text and JSON output when invalid
  - report effective excluded plugin/skill/agent ids
  - support JSON output with `--json`
- `reconcile`
  - fail before any writes on unsupported-agent diagnostics
  - apply the desired state to disk
  - print summary and applied changes
- `clean`
  - remove all bridge-generated artifacts from one project
  - release global skill registry claims for the project
  - delete last-owner skill directories
  - preserve hand-authored files (AGENTS.md, bridge.toml)
  - do not touch global instructions (~/.codex/AGENTS.md)
  - support `--dry-run` for preview
- `uninstall`
  - discover all projects from the global registry projects list (with fallback to skill owners for backwards compatibility)
  - clean each accessible project (skip and report inaccessible ones)
  - remove remaining global skills, registry, and AGENTS.md
  - remove bridge LaunchAgent plists
  - exit code 0 if all accessible projects cleaned successfully, 1 if any accessible project had a cleanup error (vanished project directories are not treated as errors)
  - support `--dry-run` for preview
  - support `--dry-run --json` for structured output
  - support `--launchagents-dir` override
- `reconcile-all`
  - read the projects list from the global registry
  - run the full discover-translate-reconcile pipeline for each registered project using default paths
  - skip inaccessible or invalid projects with an error entry in the report
  - exit code 0 if all succeed, 1 if any error
  - support `--dry-run` for preview
  - support `--json` for structured output

Pipeline commands (`validate`, `status`, `reconcile`) support:

- `--claude-home` to override the `~/.claude` base path for user-level discovery
- `--exclude-plugin marketplace/plugin`
- `--exclude-skill name` or `scope/name` or `marketplace/plugin/skill`
- `--exclude-agent name.md` or `scope/name.md` or `marketplace/plugin/agent.md`

Exclusion IDs use part-count disambiguation: 1 part matches all scopes, 2 parts match by scope (`user` or `project`), 3 parts match plugin sources.

All exclusion flags are repeatable. `.codex/bridge.toml` can define persistent exclusions, and CLI exclusions override config values for the same entity kind in the current run.

### LaunchAgent commands

- `print-launchagent`
  - render the global reconcile-all LaunchAgent plist to stdout
- `install-launchagent`
  - write the global plist into a LaunchAgents directory and print the `launchctl bootstrap` next step
  - warn about existing per-project plists with removal commands

LaunchAgent commands have their own parser and do not accept pipeline flags (`--project`, `--cache-dir`, `--claude-home`, `--codex-home`). They produce a global plist that runs `reconcile-all`.

### CLI invariants

- `validate`, `status`, and `reconcile` share the same pipeline and error types
- `doctor` is project-independent and does not require `AGENTS.md`
- `DiscoveryError`, `TranslationError`, and `ReconcileError` are surfaced as user-facing errors with exit code `1`
- filesystem `OSError` failures during CLI execution are also surfaced as user-facing errors with exit code `1`
- UTF-8 decode failures for runtime text inputs are also surfaced as user-facing errors with exit code `1`
- successful commands return `0`

## 11. LaunchAgent Architecture

LaunchAgent support lives in `src/cc_codex_bridge/install_launchagent.py`.

Current design:

- macOS scheduling is supported through `launchd`
- a single global LaunchAgent runs `reconcile-all` to reconcile all registered projects
- the global plist label is `com.openai.codex-bridge.reconcile-all`
- the default interval is 1800 seconds (30 minutes)
- the plist uses `RunAtLoad` and `StartInterval`
- logs go to `~/Library/Logs/codex-bridge/` by default
- `install-launchagent` does not require `--project` — it produces the global plist
- `install-launchagent` warns about existing per-project plists and prints `launchctl bootout` commands

The tool installs the plist file but does not run `launchctl` automatically.

Per-project LaunchAgent plists are no longer generated. The legacy `build_launchagent_plist()` function is preserved for backwards compatibility but is not used by the CLI.

## 12. Module Map

Current runtime module responsibilities:

- `cli.py`
  - argument parsing, command dispatch, summary/error reporting
- `discover.py`
  - project root resolution, installed-plugin discovery, user/project skill and agent discovery, and user-level CLAUDE.md discovery
- `doctor.py`
  - machine-level environment checks and doctor report rendering
- `exclusions.py`
  - exclusion config loading, validation, CLI-override resolution, and discovery filtering for plugins and standalone sources
- `model.py`
  - core dataclasses and domain-specific error types
- `claude_shim.py`
  - `CLAUDE.md` ownership-safe shim planning
- `frontmatter.py`
  - safe YAML frontmatter parsing plus strict runtime-shape validation for
    Claude/Codex markdown assets
- `text.py`
  - shared UTF-8 text loading helpers for runtime-managed text files
- `translate_agents.py`
  - Claude agent translation and unsupported-tool diagnostics
- `render_codex_config.py`
  - prompt-file and inline config rendering
- `registry.py`
  - global generated-skill registry serialization and deterministic skill hashing
- `translate_skills.py`
  - Codex skill translation for plugins and standalone sources, plus relative-reference resolution, vendoring helpers, and collision-free name assignment via `assign_skill_names()`
- `reconcile.py`
  - desired-state modeling, diffing, atomic apply, report formatting
  - shared project build pipeline via `build_project_desired_state()`
  - project-level cleanup via `clean_project()`
  - multi-project reconcile via `reconcile_all()`
  - machine-level uninstall via `uninstall_all()`
- `state.py`
  - project-local managed-state serialization and validation
- `install_launchagent.py`
  - LaunchAgent label generation, plist rendering, plist installation
  - bridge LaunchAgent plist discovery via `find_bridge_launchagents()`
- `release_bundle.py`
  - GitHub Release installer asset generation for offline wheelhouse installs

## 13. Testing Strategy

Tests are fixture-driven and isolated under `tests/`.

The suite currently verifies:

- project discovery rules
- semver behavior
- plugin symlink resolution
- `CLAUDE.md` shim safety
- agent translation and deterministic rendering
- skill translation, relocation rewriting, vendoring, and standalone skill translation
- exclusion filtering for plugins and standalone sources with part-count disambiguation
- reconcile idempotence, stale cleanup, ownership safety, diff reporting, and global instructions bridging
- CLI command behavior including multi-source integration
- end-to-end multi-source scenario testing with all discovery scopes
- LaunchAgent rendering and installation (global plist model)
- project-level clean and machine-level uninstall
- multi-project reconcile-all with missing project error handling
- global registry projects list round-tripping and backwards compatibility
- doctor reporting and release-bundle generation

The test suite is the executable check for the invariants described in this file.

## 14. Current Constraints and Known Simplifications

These are current implemented simplifications, not necessarily permanent design ideals:

- agent model mapping is fixed to one default Codex model
- unsupported Claude tools are hard errors rather than being preserved or partially translated
- frontmatter parsing uses safe YAML loading for frontmatter blocks plus strict
  post-parse validation of supported runtime shapes
- generated Codex config is inline rather than split across multiple files
- exclusion ids are exact-match identifiers, not wildcard/glob patterns
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
