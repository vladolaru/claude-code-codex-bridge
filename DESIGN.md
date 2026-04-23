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
- optional project exclusion config at `.codex/bridge.toml` (user-editable; also managed by `config exclude` CLI commands)
- installed Claude plugin skills discovered from the local Claude plugin cache
- installed Claude plugin agents discovered from the local Claude plugin cache
- installed Claude plugin commands discovered from the local Claude plugin cache
- plugin-local resources attached to those skills, including `scripts/`, `references/`, `assets/`, and any additional directories
- user-level Claude skills from `~/.claude/skills/`
- user-level Claude agents from `~/.claude/agents/`
- project-level Claude skills from `.claude/skills/`
- project-level Claude agents from `.claude/agents/`
- user-level global instructions from `~/.claude/CLAUDE.md`
- MCP server definitions from `~/.claude.json` (user-global and per-project scopes)
- MCP server definitions from `<project>/.mcp.json` (project-shared scope)

### Generated outputs

These are derived artifacts and must not become hand-maintained sources:

- `CLAUDE.md`
- `.codex/agents/*.toml`
- `~/.codex/agents/*.toml`
- `~/.codex/skills/*`
- `~/.codex/prompts/*.md`
- `~/.cc-codex-bridge/projects/<hash>/state.json`
- `~/.cc-codex-bridge/registry.json`
- `~/.cc-codex-bridge/plugins/<marketplace>-<plugin>/`
- `~/.cc-codex-bridge/logs/YYYY-MM-DD.jsonl` (daily activity logs)
- `~/.codex/config.toml` `[mcp_servers.*]` entries (bridge-owned only)
- `<project>/.codex/config.toml` `[mcp_servers.*]` entries (bridge-owned only)

### Authority rule

If a behavior is described differently in multiple docs:

1. the implementation is authoritative for what exists now
2. this file should be updated to match that implementation
3. plans/specs describe intent and constraints, but do not override shipped behavior until the code changes

## 3. System Boundaries

### In scope

- project discovery from the current directory or `--project`
- querying the `claude` CLI for plugin enablement status
- Claude plugin discovery from the installed local Claude cache
- user-level skill and agent discovery from `~/.claude/skills/` and `~/.claude/agents/`
- project-level skill and agent discovery from `.claude/skills/` and `.claude/agents/`
- user-level global instructions discovery from `~/.claude/CLAUDE.md`
- selection of the latest installed plugin version by semantic version
- translation of Claude agents into self-contained Codex agent `.toml` files
- translation of Claude skills into self-contained Codex skills
- translation of Claude commands into native Codex prompt files with plugin resource vendoring
- discovery of MCP server definitions from `~/.claude.json` and `.mcp.json`
- translation of MCP server configs (stdio and HTTP) into Codex `config.toml` format
- surgical editing of `config.toml` files with `tomlkit` (round-trip preserving)
- safe reconcile of generated project files, generated Codex agent files, generated Codex skill directories, and MCP server config entries
- state tracking for generator-owned outputs
- project-level artifact cleanup via `clean`
- machine-level full artifact removal via `uninstall`
- macOS LaunchAgent rendering, installation, uninstallation, and status reporting for scheduled reconcile runs via `autosync` subcommands
- activity logging for state-changing operations with retention-based auto-prune
- log viewing and manual pruning via `log show` and `log prune`
- CLI-native config management via `config` subcommands (show, check, scan, exclude, log)
- strict inline validation of config mutations against current discovery state
- interactive guided flows when config values are omitted (TTY-only)

### Out of scope

- changing Claude Code behavior
- hand-authored Codex-specific skills
- filesystem-wide project discovery (scan-based discovery via `config.toml` is in scope; unbounded filesystem traversal is not)
- watcher mode
- runtime execution of Claude commands (translated prompts serve as context/instructions, not executable workflows)

## 4. Runtime Model

The runtime is a deterministic pipeline:

1. resolve the target project root by searching upward for `AGENTS.md`
2. query `claude plugins list --json` for enabled plugin IDs (using the project root as CWD for project-scoped settings)
3. discover installed Claude plugins from `~/.claude/plugins/cache` or `--cache-dir`
4. choose the highest semantic version for each `<marketplace>/<plugin>` and filter to only enabled plugins
5. discover user-level skills, agents, and global instructions from `~/.claude/` (or `--claude-home`)
6. discover project-level skills and agents from `.claude/`
6b. discover MCP server definitions from `~/.claude.json` (user-global and per-project) and `.mcp.json` (project-shared)
7. load optional `.codex/bridge.toml` exclusions and merge any CLI exclusion flags
8. filter discovered plugins/skills/agents/MCP servers by the effective exclusion set
9. translate plugin agents into `GeneratedAgentFile` objects
10. translate standalone user and project agents into `GeneratedAgentFile` objects
11. translate plugin and user skills into `GeneratedSkill` trees (global registry)
12. translate project skills into project-local `GeneratedSkill` trees
13. translate plugin commands into `GeneratedPrompt` objects (global prompts), with plugin resource vendoring when `bridge_home` is provided
14. translate standalone user commands into `GeneratedPrompt` objects (global prompts)
15. translate project commands into `GeneratedPrompt` objects (global prompts) with `--<project-dirname>` filename suffix
16. merge all agents, render project-local agent `.toml` files to `.codex/agents/`, and collect global agents for `~/.codex/agents/`
17. translate discovered MCP servers into `GeneratedMcpServer` objects with Codex-compatible TOML tables
18. decide whether `CLAUDE.md` can be created or preserved as an `@AGENTS.md` shim
19. build a full desired state for project files, Codex skill directories, global agent files, global instructions, and MCP server entries
20. inspect/preview or reconcile that desired state with ownership and rollback protections
21. for MCP servers: surgically edit `~/.codex/config.toml` (global) and `<project>/.codex/config.toml` (project) using `tomlkit`, preserving user-authored content

The reconcile pipeline is shared by `status` and `reconcile`.

Utility commands such as `doctor` and the LaunchAgent commands are intentionally separate from the reconcile pipeline.

## 5. Implemented Output Contract

### Project-local outputs

- `CLAUDE.md`
  - only valid generated content is exactly `@AGENTS.md` plus a trailing newline
- `.codex/agents/*.toml`
  - self-contained Codex agent files derived from project-scope Claude agents
  - each `.toml` contains `name`, `description`, `developer_instructions`, and optional `sandbox_mode`
  - Codex discovers these automatically (no `config.toml` needed)
- `.codex/skills/<skill-name>/`
  - project-local Codex skill directories derived from Claude project skills

### User-global outputs

- `~/.codex/agents/*.toml`
  - self-contained Codex agent files derived from plugin and user-scope Claude agents
  - tracked in the global registry alongside skills
- `~/.codex/skills/<generated-skill-name>/`
  - self-contained Codex skill directories derived from Claude plugin and user skills
- `~/.codex/prompts/*.md`
  - native Codex prompt files derived from Claude plugin, user, and project commands
  - project-level commands get a `--<project-dirname>` suffix (e.g., `build--my-app.md`)
  - tracked in the global registry under the `prompts` section
- `~/.codex/AGENTS.md`
  - user-global Codex instructions bridged from `~/.claude/CLAUDE.md`

### Bridge-internal outputs

These are internal state files stored under `~/.cc-codex-bridge/` (configurable via `$CC_BRIDGE_HOME`):

- `~/.cc-codex-bridge/projects/<hash>/state.json`
  - per-project ownership state for reconcile safety
  - project directory keyed by SHA-256 hash of the resolved project root path
  - tracks managed project files, managed project skill directory names, bridge home, codex home, and version
- `~/.cc-codex-bridge/registry.json`
  - global generated-skill, agent, and plugin resource ownership registry keyed by install directory name (skills), filename (agents), and vendored directory name (plugin resources)
- `~/.cc-codex-bridge/plugins/<marketplace>-<plugin>/<dir>/`
  - vendored copies of plugin-level resource directories (e.g., `scripts/`, `agents/`)
  - written during reconciliation when skills or agents reference `$PLUGIN_ROOT` paths
  - paths in skill and agent content are rewritten to absolute vendored locations
  - transitive dependencies (e.g., scripts referencing `agents/shared/`) are detected and vendored automatically
- `~/.cc-codex-bridge/logs/YYYY-MM-DD.jsonl`
  - daily activity log files in newline-delimited JSON format
  - one file per day, entries appended as operations occur

### Local-only rule

Generated `.codex/*` outputs are local runtime artifacts. They are not hand-authored project source.

## 6. Ownership and Safety Model

The reconcile engine is conservative by design.

### Never overwritten unless already generator-owned

- project `AGENTS.md`
- project `.codex/bridge.toml` exclusion config (the reconcile pipeline never overwrites this file, but `config exclude` CLI commands intentionally mutate it)
- hand-authored `CLAUDE.md`
- hand-authored `.codex/agents/*.toml`
- non-generated files under `~/.codex/agents/`
- non-generated directories under `~/.codex/skills/`

### Generator-owned artifacts

Ownership is split across project-local and user-global state.

The project state file (at `~/.cc-codex-bridge/projects/<hash>/state.json`) records:

- project root
- Codex home path
- bridge home path
- managed project files as a path-to-content-hash mapping (including `CLAUDE.md`, `AGENTS.md`, and agent `.toml` files under `.codex/agents/`)
- managed project skill directory names (tracked separately for directory-snapshot comparison)
- managed MCP server names as a name-to-content-hash mapping (for project-scope MCP servers in `.codex/config.toml`)
- state version (currently 11; v8–v10 state files are migrated on read)

The global registry records:

- generated skill install directory names with content hashes and owning project roots
- generated agent `.toml` filenames with content hashes and owning project roots
- vendored plugin resource directory names with content hashes and owning project roots
- bridge-owned global MCP server names with content hashes and owning project roots
- a sorted list of all reconciled project roots (the `projects` list)

### Change kinds and ownership transitions

Planners emit `Change` records with one of five kinds. The reconcile and
status reports render all of them so pending mutations are never silent:

- `create` — a new file or directory is being written.
- `update` — an existing generator-owned file is being rewritten.
- `remove` — the entry is fully gone from the registry (this project was
  the last owner). The on-disk file or `config.toml` entry is deleted.
- `release` — this project drops its ownership claim on a shared global
  artifact, but other projects still own the entry. Only the registry is
  updated; the on-disk file is preserved for remaining owners.
  Applies to all five global-registry artifact types: skills, agents,
  prompts, vendored plugin resource dirs, and MCP server entries.
- `restore` — legacy shim undo for the `CLAUDE.md` / `AGENTS.md`
  bootstrap path (unchanged by the release-kind addition).

`_apply_changes` treats `release` as an explicit no-op at the filesystem
layer; the registry write queued on the same plan carries the state
transition. `_apply_mcp_server_changes` additionally excludes released
global MCP names from the "previously owned by this project" set before
writing `~/.codex/config.toml`, so the apply phase does not rewrite the
shared entry out from under other owners.

### Safety rules

- project files are never overwritten unless they were previously recorded as managed, and their on-disk content hash matches the stored hash (drift detection)
- migrated v8 entries with an empty stored hash do not authorize update or removal; they are preserved until reconcile can record a trusted hash from the on-disk file
- externally modified managed files are preserved: reconcile skips updates, clean skips removal
- `clean` preserves `AGENTS.md` when `CLAUDE.md` is a symlink to it, so cleanup does not leave a dangling project instructions symlink
- the state file may only authorize generator-owned project paths: `CLAUDE.md`, `AGENTS.md`, and `.codex/agents/*.toml` (project skill directories are tracked separately via `managed_project_skill_dirs`)
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
- existing global agent `.toml` files are adopted only when their content hash matches the desired generated content exactly
- conflicting content for an existing generated agent file is a hard error
- generated agent files are removed only when the global registry shows no remaining owners
- vendored plugin resource directories are removed only when the global registry shows no remaining owners
- vendored plugin resource directories use content hash tracking for fast change detection — on-disk comparison is skipped when the registry hash matches the desired hash
- all write targets must resolve within their expected root (`project_root` for project files, `codex_home` for global Codex files, `bridge_home` for state and registry files) after symlink resolution — this catches symlinked ancestor directories that would redirect operations outside the expected tree
- skill translation follows symlinks unconditionally, matching Claude Code's behavior
- project skills are tracked as managed directory names and compared using exact directory-snapshot matching, consistent with global skills
- project files are written atomically via temp-file-then-rename to avoid partial reads
- if a write fails mid-apply, the next idempotent reconcile run self-heals
- stale managed outputs are removed when no longer desired
- cleanup uses the codex_home recorded in bridge state, not the caller-supplied value, to ensure registry operations target the correct global state
- project-local generated skill directories are removed as whole directories, not individual files, consistent with global skill directory ownership
- MCP entries in `config.toml` are ownership-aware: only bridge-owned entries (tracked in state or registry) are modified; user-authored entries with the same name are never touched or adopted
- user-authored MCP entries that collide with bridge-discovered servers are skipped during apply and excluded from state/registry tracking — `clean` will not remove them
- when MCP discovery is degraded (config file exists but contains malformed JSON), stale-entry removal is suppressed — previously-bridged entries survive until the file is fixed

#### Known limitation: multi-project global MCP update on first reconcile

When project B first encounters a global MCP server already created by project A with different content, B adds itself as a co-owner in the registry but cannot update `config.toml` because the entry is not in B's `owned` set. The registry records B's desired hash while the disk retains A's content. This self-heals on B's second reconcile (B is then in `previously_owned_global`) or on A's next reconcile. This is accepted because MCP servers overwhelmingly come from plugins with identical configs across projects — the scenario of two projects wanting different content for the same global MCP server name is not realistic in practice. Do not attempt to fix this without a concrete real-world use case that demonstrates the need.

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

### Command discovery

- plugin commands are discovered from `<plugin_path>/commands/` as `*.md` files
- user-level commands are discovered from `~/.claude/commands/` as `*.md` files
- project-level commands are discovered from `<project>/.claude/commands/` as `*.md` files
- command discovery follows the same pattern as agent discovery (top-level `.md` files in a directory)

### MCP server discovery

`src/cc_codex_bridge/discover_mcp.py` reads MCP server definitions from Claude Code configuration files.

Sources (in precedence order, highest first):

1. **Project-local**: `~/.claude.json` → `projects.<project_root>.mcpServers` → scope `project`
2. **Project-shared**: `<project>/.mcp.json` → `mcpServers` → scope `project`
3. **User-global**: `~/.claude.json` → top-level `mcpServers` → scope `global`

When the same server name appears at multiple scopes, highest precedence wins. SSE transport servers are skipped (unsupported in Codex). Plugin-provided MCP servers (using `${CLAUDE_PLUGIN_ROOT}`) are not discovered — standalone equivalents cover them.

Transport detection: `command` field → stdio; `type: "http"` or `url` field → HTTP.

When a source file exists but contains malformed JSON, discovery is *degraded*: servers from other valid sources are still discovered, but the `mcp_discovery_degraded` flag is set on the `DiscoveryResult`. The reconcile pipeline uses this flag to suppress stale-entry removal — previously-bridged MCP entries survive until the corrupt file is fixed and a subsequent reconcile runs normally.

### Plugin enablement

- the `claude` CLI is a hard runtime dependency for the bridge
- `query_enabled_plugin_ids()` shells out to `claude plugins list --json` using the project root as CWD
- the CLI output provides an authoritative enablement status that reflects the merged settings hierarchy (user → project → local)
- the CLI's `id` format (`plugin-name@marketplace`) is converted to the bridge's internal `marketplace/plugin_name` format
- after cache scanning, `discover_latest_plugins()` filters to only plugins present in the enabled set
- uninstalled-but-cached plugins are excluded because the CLI only lists installed plugins
- the CLI's `installPath` and `version` fields are not used — they are stale for a significant portion of enabled plugins due to auto-updates that don't refresh the install metadata
- if the `claude` CLI is not on PATH, discovery raises a `DiscoveryError` with installation instructions
- `doctor` includes a `claude_cli` check as the first machine-level verification

### Version selection

- versions are grouped by `<marketplace>/<plugin>`
- the highest semantic version wins
- prerelease precedence follows semver rules implemented in `SemVer`

### Symlink rule

Installed plugin version directories are resolved with `Path.resolve()`. If the installed path is a symlink into a working repo, the resolved repo path becomes the effective source path, while the installed path is still recorded.

### Scan-based discovery

Scan-based discovery lives in `src/cc_codex_bridge/scan.py` and enables bulk project operations via `--all` flags.

**Config file:** `~/.cc-codex-bridge/config.toml` (user-authored, never overwritten by the bridge)

```toml
scan_paths = ["~/Work/projects/*"]
exclude_paths = ["~/Work/projects/scratch"]
```

- `scan_paths`: shell-style glob patterns with `~` expansion
- `exclude_paths`: glob patterns for directories to skip
- both fields optional, unknown keys ignored
- missing file means no scanning (backwards compatible)
- malformed TOML is a hard error

**Discovery pipeline:**

1. expand `scan_paths` globs into candidate directories
2. remove candidates matching `exclude_paths` globs
3. reject symlinked directories
4. reject directories without `.git/` directory (excludes submodules and worktrees where `.git` is a file)
5. reject directories without Claude presence (`AGENTS.md`, `CLAUDE.md`, or `.claude/`)
6. categorize: bridgeable (has `AGENTS.md` or `CLAUDE.md`) vs not-bridgeable (has `.claude/` only)

The pipeline is deterministic: same filesystem state always produces the same sorted candidate set.

`reconcile_all()` merges scan-discovered bridgeable projects with registry projects (set union, deduplicated by resolved path) before the per-project reconcile loop.

## 8. Translation Architecture

### 8.1 Claude shim planning

`src/cc_codex_bridge/claude_shim.py` decides what to do with `CLAUDE.md`.

Allowed outcomes:

- `create` — CLAUDE.md does not exist; generate `@AGENTS.md\n`
- `preserve` — CLAUDE.md is valid; leave it alone
- `skip` — CLAUDE.md is hand-authored without AGENTS.md reference; proceed without managing it
- `bootstrap` — CLAUDE.md exists without AGENTS.md; include both files in desired state (AGENTS.md with original content, CLAUDE.md with shim)
- `fail` — corrupted state (shim without AGENTS.md, or broken AGENTS.md symlink)

`CLAUDE.md` is treated as valid (`preserve`) when:

- it exactly matches `@AGENTS.md` plus a trailing newline
- it is a symlink to `AGENTS.md`
- it contains the substring `AGENTS.md` anywhere in its content (lenient matching)

When `AGENTS.md` does not exist but `CLAUDE.md` is a regular file, the outcome is `bootstrap`. Both files are included in the desired project files: AGENTS.md receives the original CLAUDE.md content, and CLAUDE.md becomes the `@AGENTS.md` shim. Normal reconcile creates both files and tracks them with content hashes. Drift detection protects them on subsequent reconciles. Bootstrap is not a separate code path — it is handled by the normal reconcile pipeline.

Hand-authored `CLAUDE.md` without an AGENTS.md reference produces `skip` — the bridge omits CLAUDE.md from managed project files but proceeds with agents, skills, commands, and state.

### 8.2 Agent translation

`src/cc_codex_bridge/translate_agents.py` converts Claude agent markdown files into `GeneratedAgentFile`.

Required Claude frontmatter:

- `name`
- `description`

Optional handled fields:

- `model` (preserved as metadata only)
- `tools` (mapped to `sandbox_mode`)

Current mapping rules:

- plugin agents: agent name = bare stem, install filename = `<stem>.toml`, scope = `global`
- user agents: agent name = bare stem, install filename = `<stem>.toml`, scope = `global`
- project agents: agent name = bare stem, install filename = `<stem>.toml`, scope = `project`
- collisions across scopes resolved by `assign_agent_names()`: user/project agents win the bare name; plugin agents receive `-alt`, `-alt-2`, … suffixes
- normalized generated names reject absolute paths, `..` traversal, and values that collapse to an empty identifier
- developer_instructions = markdown body after frontmatter, normalized to end with a trailing newline when non-empty
- sandbox_mode derived from Claude tool list via `derive_sandbox_mode()`:
  - write tools (`Bash`, `Write`, `Edit`) → `workspace-write`
  - read-only tools (`Read`, `Grep`, `Glob`, `WebSearch`) → `read-only`
  - no tools → omit (inherit from parent session)

Unsupported Claude tools are hard diagnostics. They invalidate agent generation for that run instead of being silently dropped.

Installed-agent translation checks for duplicate `install_filename` values as well as duplicate `agent_name` values, consistent with standalone agent translation.

Plugin resource vendoring:

- agent `developer_instructions` referencing `$PLUGIN_ROOT/scripts/` or similar patterns gets paths rewritten to absolute vendored locations under `~/.cc-codex-bridge/plugins/`
- referenced plugin directories are copied as `VendoredPluginResource` objects
- the same detection and rewriting rules apply as for skill translation (section 8.4)
- transitive dependencies (e.g., scripts referencing `agents/shared/` via `os.path.join`) are detected and vendored automatically

After merging all scopes (plugin, user, project), `validate_merged_agents()` checks uniqueness of both `agent_name` and `install_filename` across the full merged set. Per-scope checks provide early detection with better error messages; the post-merge check is the global invariant that prevents cross-scope collisions from producing silently corrupt output.

Frontmatter parsing is shared through `src/cc_codex_bridge/frontmatter.py`.

The parser extracts only frontmatter blocks and parses them with PyYAML's safe
loader.

Post-parse validation keeps the runtime contract narrow:

- top-level frontmatter must be a mapping
- mapping keys must be strings
- accepted values are strings plus nested lists/mappings composed of the same
  allowed value shapes
- malformed YAML and unsupported runtime shapes are hard translation errors

### 8.3 Agent TOML rendering

`src/cc_codex_bridge/render_agent_toml.py` renders self-contained Codex agent `.toml` files.

Each file contains:

- `name` — the agent identifier
- `description` — human-facing guidance for when to use the agent
- `developer_instructions` — the prompt body from the Claude agent markdown
- `sandbox_mode` — optional, derived from Claude tool lists

Output is deterministic: fields appear in a fixed order, strings use TOML-compatible escaping, and each file includes a generated-file header.

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
- symlinks in skill source trees (SKILL.md, resource files, subdirectories) are followed unconditionally during translation, matching Claude Code's behavior

Plugin resource vendoring:

- skill content referencing `$PLUGIN_ROOT/scripts/` or `<skill base directory>/../..` patterns gets paths rewritten to absolute vendored locations under `~/.cc-codex-bridge/plugins/`
- referenced plugin directories are copied as `VendoredPluginResource` objects
- transitive dependencies (e.g., scripts referencing `agents/shared/` via `os.path.join`) are detected and vendored automatically
- detection covers `$PLUGIN_ROOT`, `${PLUGIN_ROOT}`, and `<skill base directory>/../..` forms
- the multi-line runtime discovery block (`cat /tmp/...`, `find ~/.claude ...`) is removed and replaced with direct absolute paths

Current relocation behavior:

- sibling skill references matching `../<name>/` are resolved relative to the skill's disk location
- referenced sibling trees are vendored directly into the generated skill directory
- collisions between referenced sibling names and existing skill subdirectories are a hard error
- missing referenced siblings are treated as a hard translation error

`${CLAUDE_SKILL_DIR}` variable handling:

- Claude Code resolves `${CLAUDE_SKILL_DIR}` at runtime to the skill's own directory
- during translation, references within the skill directory are replaced with a placeholder (`__BRIDGE_SKILL_DIR__/relative/path`)
- references that escape the skill directory (via `../`) fall back to absolute source paths
- placeholders are resolved to the actual Codex install path after `assign_skill_names()` in the reconcile pipeline
- global skills resolve to `~/.codex/skills/<install_dir_name>/`
- project skills resolve to `<project>/.codex/skills/<install_dir_name>/`

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

### 8.5 Command translation (prompts)

`src/cc_codex_bridge/translate_prompts.py` translates Claude commands into `GeneratedPrompt` objects.

Commands are translated to native Codex prompt files (`~/.codex/prompts/*.md`). Each command markdown file produces one `GeneratedPrompt` with a single markdown file.

Required command frontmatter:

- `description`

Preserved command frontmatter:

- `argument-hint` (included in generated prompt output)

Dropped command frontmatter (not carried to generated output):

- `allowed-tools`

Variable handling:

- `$ARGUMENTS`, `$ARGUMENTS[N]`, `$1`-`$9` are passed through natively in the prompt body (Codex handles them at runtime)
- `${CLAUDE_PLUGIN_ROOT}` → when `bridge_home` is provided, replaced with the vendored root path under `~/.cc-codex-bridge/plugins/` instead of the raw plugin cache path; without `bridge_home`, falls back to the resolved absolute path from `InstalledPlugin.source_path` (plugin commands only; standalone commands leave it as-is)

Plugin resource vendoring:

- when `bridge_home` is provided, `$PLUGIN_ROOT` patterns in command bodies are detected and rewritten to absolute vendored locations using the same engine as skill/agent vendoring (`detect_plugin_resource_dirs()`, `rewrite_plugin_paths()`)
- `${CLAUDE_PLUGIN_ROOT}` references (already replaced with the vendored root path) are scanned for directory references and those directories are vendored
- transitive dependency detection applies to vendored command scripts (same as skills/agents)
- runtime discovery blocks are removed and replaced with direct absolute paths, consistent with skill vendoring

Generated prompt file structure:

- frontmatter: `description` (from command frontmatter), optional `argument-hint` (preserved from command frontmatter)
- body: command body with plugin root replacements applied
- provenance marker appended at end: `<!-- translated from Claude Code command -->`

### Prompt naming

Prompt filenames are derived from the command filename stem (e.g., `code-review.md` → `code-review.md`).

Project-level commands get a `--<project-dirname>` suffix (e.g., `build.md` from project `my-app` → `build--my-app.md`).

When multiple prompts share the same filename across sources, collisions are resolved with deterministic suffixes via `assign_prompt_names()`:

- 1st (priority winner): `<name>.md` (bare)
- 2nd: `<name>-alt.md`
- 3rd: `<name>-alt-2.md`
- Nth: `<name>-alt-<N-1>.md`

Priority order for collision resolution:
1. User prompts (standalone user commands)
2. Project prompts (standalone project commands, with `--<dirname>` suffix)
3. Plugin prompts, sorted by `(marketplace, plugin_name)`

All generated prompts are installed to `~/.codex/prompts/` and tracked in the global registry under the `prompts` section.

### 8.6 Reference rewriting

`src/cc_codex_bridge/rewrite_references.py` rewrites plugin-qualified skill and command references in generated content to their Codex equivalents.

After skill names (`assign_skill_names`) and prompt names (`assign_prompt_names`) are finalized, `build_reference_map()` constructs a lookup table:

- plugin skills: `plugin_name:original_skill_name` → `$codex_skill_name`
- plugin commands: `plugin_name:command_stem` → `$prompt_stem`

Only plugin-scoped artifacts are included (marketplace not starting with `_`). User and project artifacts are excluded because their names are not plugin-qualified in source content.

`rewrite_content()` applies exact byte-string replacement to generated content, processing keys longest-first to prevent partial matches. The rewrite applies to:

- skill `SKILL.md` bodies (global and project)
- agent `developer_instructions` (global and project)
- prompt bodies
- global instructions (`user_claude_md`)

Agent references (`plugin:agent-name`) are not rewritten because agent invocation is structurally different between the two CLIs — a name-only rewrite without changing the invocation pattern would be misleading.

### Skill routing

User-level and plugin skills are installed to the global Codex skill registry at `~/.codex/skills/`. Project-level skills are installed to project-local `.codex/skills/` directories and tracked as managed project skill directory names in the bridge state. Both global and project skills use exact directory-snapshot comparison for change detection.

### 8.6 MCP server translation

`src/cc_codex_bridge/translate_mcp.py` converts `DiscoveredMcpServer` objects into `GeneratedMcpServer` objects.

stdio mapping: `command` → `command`, `args` → `args`, `env` → `env` (omitted when empty; non-string values filtered). The CC `type` field is stripped (Codex infers transport from field presence). Env values containing `${VAR}`, `$VAR`, or `${VAR:-default}` references are removed from `env`, their referenced source vars are added to `env_vars`, and the original templates are expanded at runtime by a bridge-owned stdio launcher so Claude-style semantics are preserved.

HTTP mapping: `url` → `url`, `headers` → `http_headers`. URL values containing `${VAR}` references are kept literal with a diagnostic because Codex has no URL env-expansion mechanism. Special case: `Authorization: "Bearer ${VAR}"` headers are extracted into `bearer_token_env_var` and the header is removed. Header values that are whole-value `${VAR}` or `$VAR` references are routed to `env_http_headers` instead of `http_headers`, so Codex resolves them from the host environment at runtime. Mixed header values containing inline `${VAR}` references are kept in `http_headers` with a diagnostic.

Diagnostics (warnings, not errors): `headersHelper` (no Codex equivalent), `oauth` (user must run `codex mcp login`), HTTP URL/header template references that Codex cannot expand natively, and literal credential values in env/headers.

### 8.7 MCP TOML editing

`src/cc_codex_bridge/toml_config.py` provides surgical editing of Codex `config.toml` files using `tomlkit` for round-trip preservation of comments and formatting.

Key functions:
- `read_codex_config(path)` → parse or return empty doc
- `write_codex_config(path, doc)` → atomic write (temp + rename); removes empty files
- `apply_mcp_changes(doc, desired, owned)` → add/update/remove bridge-owned `[mcp_servers.*]` entries
- `hash_mcp_server_table(table_dict)` → deterministic `sha256:` content hash

The editing is ownership-aware: only entries tracked in the bridge registry or state are modified. User-authored MCP entries are never touched.

## 9. Reconcile Architecture

Reconcile lives in `src/cc_codex_bridge/reconcile.py`.

### Desired state

`DesiredState` is the full output model for one run:

- project root
- Codex home
- bridge home
- project files with desired bytes (includes `CLAUDE.md` shim and project-local agent `.toml` files under `.codex/agents/`)
- project skills (directory-snapshot comparison, installed to `.codex/skills/`)
- generated skills (global registry, installed to `~/.codex/skills/`)
- global agents (global registry, installed as `.toml` files to `~/.codex/agents/`)
- global instructions content (for `~/.codex/AGENTS.md`)
- vendored plugin resources (bridge-internal, written to `~/.cc-codex-bridge/plugins/`)
- path to the state file (under bridge home)

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
2. load the current global registry (skills, agents, and plugin resources) from bridge home
3. compute desired project file changes (shim, project-local agent `.toml` files)
4. compute desired project skill directory mutations using directory-snapshot comparison
5. compute desired generated-skill claims and reconcile changes from registry ownership plus on-disk content hashes
6. compute desired global agent file mutations and reconcile changes from registry ownership plus content hashes
7. compute desired global instructions changes for `~/.codex/AGENTS.md`
8. validate ownership constraints
9. write project file and skill directory changes directly
10. compute vendored plugin resource content hashes, claim registry ownership, and write vendored plugin resource directories under bridge home
11. detect stale plugin resources no longer in the desired state and release registry ownership (last-owner directories are removed)
12. write global agent files and instructions file if needed
13. write updated global registry file (a single file tracks skills, agents, and plugin resources)
14. write the state file under bridge home
15. remove stale managed outputs whose last owner released them

`diff_desired_state()` additionally reports state file create/update changes that `reconcile_desired_state()` would perform, ensuring `status` and `reconcile --dry-run` show the same pending changes as a real reconcile.

### Write model

Project files, global agent files, and registry files are written atomically using temp-file-then-rename in the same directory.

Skill directories are written directly. On update, the old directory is removed before the new one is written.

If a write fails mid-apply, the next reconcile run detects the mismatch and repairs it. The reconcile pipeline is idempotent by design.

### Idempotence rule

If the desired state matches current managed outputs, reconcile becomes a no-op except that it may still write the state file when required to bring state into sync.

## 10. CLI Architecture

The CLI lives in `src/cc_codex_bridge/cli.py`.

### Main commands

- `doctor`
  - run machine-level environment checks without requiring a project
  - report Python version support, Claude CLI availability, Claude cache visibility, Codex-home writability, LaunchAgents directory access, CLI PATH visibility, and available release version (best-effort GitHub check, 3-second timeout, silent on network failure)
  - support JSON output with `--json`
- `upgrade`
  - fetch the latest release version from the GitHub releases API and compare with the installed version
  - download and execute the official `install.sh` in place when a newer version is available
  - `--check` flag to report available version without installing
  - blocks editable (development) installs with a message explaining both how to update the checkout and how to switch to a release install
- `reconcile --dry-run`
  - compute reconcile changes without writing
  - print change summary
- `reconcile --dry-run --diff`
  - compute reconcile changes without writing
  - print change summary plus unified diffs for managed text files
- `status`
  - run discovery, translation, and rendering in memory (supersedes the former `validate` command)
  - compute reconcile changes without writing
  - report `in_sync` vs `pending_changes`
  - report `invalid` when agent translation contains unsupported Claude tools
  - print full discovery summary: project root, AGENTS.md path, CLAUDE.md action, plugin list with per-plugin skill/agent/prompt counts, generated totals, exclusions
  - report categorized project-file vs skill create/update/remove changes
  - report drifted managed files as a separate `DRIFTED` category (drift is computed by comparing stored content hashes against on-disk content for all managed project files; missing files and symlinks are excluded, and v8-migrated managed files with empty hashes are preserved until reconcile can backfill a hash)
  - include agent translation diagnostics in both text and JSON output when invalid
  - report effective excluded plugin/skill/agent ids
  - support JSON output with `--json`
- `reconcile`
  - fail before any writes on unsupported-agent diagnostics
  - apply the desired state to disk
  - print summary and applied changes
- `clean`
  - remove all bridge-generated artifacts from one project
  - release global skill and agent registry claims for the project
  - delete last-owner skill directories and agent files
  - preserve hand-authored files (AGENTS.md, bridge.toml)
  - do not touch global instructions (~/.codex/AGENTS.md)
  - support `--dry-run` for preview
  - when cleaning a bootstrapped project where both AGENTS.md and CLAUDE.md are unedited (content hashes match stored values), the clean operation reverses the bootstrap: AGENTS.md content is written back to CLAUDE.md, and AGENTS.md is removed; if either file was externally modified, the drifted file is preserved and the reversal is skipped
- `uninstall`
  - discover all projects from the global registry projects list (with fallback to skill owners for backwards compatibility)
  - clean each accessible project (skip and report inaccessible ones)
  - remove remaining global skills, agents, registry, and AGENTS.md
  - remove bridge LaunchAgent plists
  - exit code 0 if all accessible projects cleaned successfully, 1 if any accessible project had a cleanup error (vanished project directories are not treated as errors)
  - support `--dry-run` for preview
  - support `--dry-run --json` for structured output
  - support `--launchagents-dir` override
Pipeline commands (`reconcile`, `status`) support `--all` to operate on all projects from the registry and scan config:

- `reconcile --all`: run the full discover-translate-reconcile pipeline for each project
- `status --all`: show sync state for each project (always dry-run)

`--all` behavior:

- reads the projects list from the global registry and merges with scan-discovered projects
- skips inaccessible or invalid projects with an error entry in the report
- exit code 0 if all succeed, 1 if any error
- supports `--dry-run` for preview (`reconcile --all` only; `status` is always dry-run)
- supports `--json` for structured output (`reconcile --all` and `status --all`)
- `--all` is mutually exclusive with `--project` (runtime check)
- scan discovery results are included in both text and JSON output when scan config exists

`reconcile` also supports exclusion flags:

- `--exclude-plugin marketplace/plugin`
- `--exclude-skill name` or `scope/name` or `marketplace/plugin/skill`
- `--exclude-agent name.md` or `scope/name.md` or `marketplace/plugin/agent.md`
- `--exclude-command name` or `scope/name` or `marketplace/plugin/name`

Both `status` and `reconcile` support:

- `--claude-home` to override the `~/.claude` base path for user-level discovery

Exclusion IDs use part-count disambiguation: 1 part matches all scopes, 2 parts match by scope (`user` or `project`), 3 parts match plugin sources.

All exclusion flags are repeatable. Exclusions merge from three layers:

1. **Global** `~/.cc-codex-bridge/config.toml` `[exclude]` — machine-wide defaults
2. **Project** `.codex/bridge.toml` `[exclude]` — per-project additions

Global and project exclusions are **unioned** (both apply). CLI `--exclude-*` flags **replace** the merged set for that entity kind in the current run.

Plugin and MCP server exclusions are **independent**. Plugins are discovered from the Claude plugin cache and provide skills, agents, and commands. MCP servers are discovered from `~/.claude.json` and `.mcp.json`. Excluding a plugin filters its skills, agents, and commands but does not affect MCP servers — there is no plugin-to-MCP-server association in the Claude Code plugin format. MCP servers must be excluded separately by name.

When a global exclusion is added via `config exclude add --global`, the bridge automatically removes redundant project-level exclusions for the same entity from all registered projects' `.codex/bridge.toml` files.

### Log commands

- `log show`
  - display activity log entries from `~/.cc-codex-bridge/logs/`
  - date range filters: `--since YYYY-MM-DD`, `--until YYYY-MM-DD`, `--days N`
  - content filters: `--project PATH`, `--action NAME`, `--type TYPE`
  - supports `--json` for JSONL output
- `log prune`
  - manually trigger retention cleanup using `log_retention_days` from config (default: 90)
  - reports removed files or confirms no expired logs

Log commands are utility commands independent of the reconcile pipeline.

### Autosync commands

The `autosync` subcommand group manages automatic background reconciliation on macOS via LaunchAgents. Autosync commands have their own parser and do not accept pipeline flags (`--project`, `--cache-dir`, `--claude-home`, `--codex-home`).

- `autosync install`
  - render a global LaunchAgent plist that runs `reconcile --all` on a recurring interval
  - boot out any running instance, write the plist atomically, and load it via `launchctl bootstrap`
  - re-running updates the plist and reloads the agent
  - warn about stale per-project plists with removal commands
  - log the operation as `autosync-install` in the activity log
  - flags: `--interval`, `--label`, `--python-executable`, `--cli-path`, `--logs-dir`, `--launchagents-dir`
- `autosync uninstall`
  - boot out and remove the bridge LaunchAgent plist
  - flags: `--label`, `--launchagents-dir`
- `autosync status`
  - report whether the bridge LaunchAgent is installed and loaded
  - display full configuration: label, plist path, interval, executable, working directory, PATH env status, starts-at-login, log paths
  - indicate default values with `(default)` labels
  - flags: `--launchagents-dir`

### CLI invariants

- `status` and `reconcile` share the same pipeline and error types
- `doctor` is project-independent and does not require `AGENTS.md`
- `DiscoveryError`, `TranslationError`, and `ReconcileError` are surfaced as user-facing errors with exit code `1`
- filesystem `OSError` failures during CLI execution are also surfaced as user-facing errors with exit code `1`
- UTF-8 decode failures for runtime text inputs are also surfaced as user-facing errors with exit code `1`
- successful commands return `0`

## 11. LaunchAgent Architecture

LaunchAgent support lives in `src/cc_codex_bridge/install_launchagent.py`.

Current design:

- macOS scheduling is supported through `launchd`
- a single global LaunchAgent runs `reconcile --all` to reconcile all registered projects
- the global plist label is `cc-codex-bridge.autosync`
- the label prefix for all bridge plists is `cc-codex-bridge.`
- the default interval is 1800 seconds (30 minutes)
- the plist uses `RunAtLoad` and `StartInterval`
- the plist includes `WorkingDirectory` set to `$HOME`
- the current process `PATH` is baked into the plist's `EnvironmentVariables.PATH` at install time, so the agent can locate the `claude` CLI even though macOS LaunchAgents run with a stripped PATH
- the plist prefers the `cc-codex-bridge` console script (located via `shutil.which`) over direct Python execution, so macOS shows the tool name in background-activity notifications instead of the Python interpreter
- falls back to invoking `cli.py` via the Python interpreter when the console script is not on PATH
- logs go to `~/Library/Logs/codex-bridge/` by default
- `autosync install` writes the plist atomically, boots out any running instance, and loads the new plist via `launchctl bootstrap` — the agent is active immediately after install
- `autosync uninstall` boots out and removes the plist
- `autosync status` reports install/load state and full plist configuration

Per-project LaunchAgent plists are no longer generated. The legacy `build_launchagent_plist()` function is preserved for backwards compatibility but is not used by the CLI.

## 12. Activity Log

Activity logging lives in `src/cc_codex_bridge/activity_log.py`.

### Storage

Log entries are stored as daily JSONL files at `~/.cc-codex-bridge/logs/YYYY-MM-DD.jsonl`. Each line is a self-contained JSON object. New entries are appended to the current day's file. The logs directory is created on first write.

### Logged operations

State-changing CLI operations write a log entry after successful completion:

- `reconcile` (single-project and `--all`)
- `clean`
- `autosync install`

`uninstall` is excluded from logging because it removes bridge infrastructure including the logs directory. Operations with no changes (e.g., an idempotent reconcile) are not logged.

### Log entry schema

Each JSON line contains:

- `timestamp` — ISO 8601 datetime
- `action` — the operation name (`reconcile`, `clean`, `autosync-install`)
- `project` — resolved project root path (or `global` for machine-level operations)
- `changes` — array of `{type, artifact, path}` objects where:
  - `type` is `create`, `update`, or `remove`
  - `artifact` is `skill`, `agent`, `prompt`, `project_file`, or `plugin_resource`
  - `path` is the absolute filesystem path of the changed artifact
- `summary` — computed counts: `{created, updated, removed}`

### Auto-prune

After every state-changing operation, expired log files are automatically pruned based on the configured retention period. This keeps log storage bounded without manual intervention.

### CLI access

- `log show` reads and displays log entries with optional filters: `--since`, `--until`, `--days`, `--project`, `--action`, `--type`, `--json`
- `log prune` manually triggers retention cleanup, reporting which files were removed

## 13. Global Configuration

Global configuration lives in `src/cc_codex_bridge/config.py`.

### Config file

`~/.cc-codex-bridge/config.toml` is a user-authored file that the bridge reads and — since the `config` CLI commands — also writes. The file is optional — all settings have defaults that apply when the file is missing.

The config file serves three purposes:

1. scan-based discovery configuration (`scan_paths`, `exclude_paths`) — loaded by `scan.py`
2. activity log configuration (`[log]` section) — loaded by `config.py`
3. global sync exclusions (`[exclude]` section) — loaded by `config.py`, merged into the exclusion pipeline

### Current settings

```toml
[log]
log_retention_days = 90   # default; minimum 1

[exclude]
plugins = ["my-marketplace/plugin-name"]
skills = []
agents = []
commands = []
```

- `log_retention_days` controls how many days of activity log files are retained before auto-prune removes them
- non-integer or sub-1 values fall back to the default (90 days)
- a missing `[log]` section or missing config file both produce the default
- `[exclude]` lists are unioned with per-project `.codex/bridge.toml` exclusions; CLI `--exclude-*` flags replace the merged set for that kind
- malformed `[exclude]` values are silently ignored (logged as warnings) and fall back to empty exclusions

### CLI config commands

The `config` subcommand group provides CLI-native flows for viewing, validating, and mutating bridge configuration.

**Scope resolution:** commands auto-detect whether to target the global config (`~/.cc-codex-bridge/config.toml`) or the project config (`.codex/bridge.toml`) based on whether the CWD is inside a project with `AGENTS.md`. The `--global` flag forces global scope.

**Subcommand tree:**

- `config show` — display effective config with source attribution (default/global/project)
- `config check` — audit config files against current environment
- `config scan add/remove/list` — manage scan path globs (global-only)
- `config exclude add/remove/list` — manage sync exclusions with discovery-backed validation
- `config log set-retention` — set log retention period (global-only)

**Validation model:** every mutation validates strictly against current state. Invalid values are refused (exit 1), not written with warnings. `config exclude add` runs the discovery pipeline to verify entity existence. Discovery validation is scope-aware: global scope only validates plugin and user entities, not project entities.

**Scope feedback:** `config exclude add` and `config exclude remove` include scope attribution in their output messages — either "(global)" or "(project: path)" — so the user always knows which config file was modified.

**Interactive mode:** when a required value is omitted and stdin is a TTY, commands offer numbered selection lists or text prompts. Non-TTY invocations error with "missing required argument" instead of hanging.

**TOML writing:** uses `tomli-w` for config writes. Comments are not preserved on write.

### Design constraint

The config file is shared between scan discovery and activity log configuration. Each module reads only the keys it needs and ignores the rest. Unknown keys are tolerated (but flagged by `config check`).

## 14. Module Map

Current runtime module responsibilities:

- `activity_log.py`
  - daily JSONL activity log: entry data model (`LogEntry`, `LogChange`), serialization, file I/O (write, read with date range), filtering (project, action, change type), retention pruning, and human-readable formatting
- `bridge_home.py`
  - bridge home directory resolution (`~/.cc-codex-bridge/`, configurable via `$CC_BRIDGE_HOME`), project-specific state path computation, plugin resource path computation, and logs directory path
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
- `config.py`
  - global bridge configuration: `BridgeConfig` dataclass, TOML loading from `config.toml` `[log]` section, default value handling
- `config_check.py`
  - config validation: TOML well-formedness, scan path resolution, unknown key detection, global-only key rejection for project configs
- `config_exclude_commands.py`
  - config exclude subcommand handlers: discovery-backed add/remove/list for plugin/skill/agent/command/mcp_server exclusions
- `config_scan_commands.py`
  - config scan subcommand handlers: glob-validated add/remove/list for scan paths
- `config_scope.py`
  - config scope resolution: auto-detects global vs project config target based on AGENTS.md presence, `--global` override
- `config_show.py`
  - config show formatting: human-readable and JSON output with source attribution (default/global/project)
- `config_writer.py`
  - TOML read-modify-write helpers using `tomli-w`: read, write, add/remove from string lists, set nested values
- `_colors.py`
  - ANSI color helper: `color_fns()` returns a dict of callables (key, good, warn, bad, create, update, remove, dim) using Python 3.14's `_colorize` theme; returns no-op callables outside a TTY or when `_colorize` is unavailable
- `interactive.py`
  - interactive CLI helpers: numbered list selection, text value prompts, TTY detection
- `claude_shim.py`
  - `CLAUDE.md` ownership-safe shim planning
- `frontmatter.py`
  - safe YAML frontmatter parsing plus strict runtime-shape validation for
    Claude/Codex markdown assets
- `text.py`
  - shared UTF-8 text loading helpers for runtime-managed text files
- `translate_agents.py`
  - Claude agent translation to `GeneratedAgentFile` objects, sandbox mode derivation, and unsupported-tool diagnostics
- `render_agent_toml.py`
  - self-contained Codex agent `.toml` file rendering and Claude-tool-to-sandbox-mode mapping
- `registry.py`
  - global generated-skill, agent, and plugin resource ownership registry serialization, deterministic skill, agent, and plugin resource content hashing
- `scan.py`
  - scan-based project discovery: config loading from `config.toml`, glob expansion with tilde support, structural filtering (symlinks, submodules, worktrees, Claude presence), top-level `scan_for_projects()` entry point
- `translate_skills.py`
  - Codex skill translation for plugins and standalone sources, plus relative-reference resolution, vendoring helpers, and collision-free name assignment via `assign_skill_names()`
- `vendor_plugin.py`
  - plugin resource path detection and rewriting for `$PLUGIN_ROOT` references, transitive dependency detection for vendored scripts
- `translate_prompts.py`
  - Claude command translation to `GeneratedPrompt` objects (native Codex prompt files), variable pass-through, provenance marking, and plugin resource vendoring support (when `bridge_home` is provided)
- `rewrite_references.py`
  - rewrites plugin-qualified skill and command references in generated content to their Codex equivalents
- `validate_skill.py`
  - validates generated skill metadata against the Agent Skills Standard (name, description, file structure)
- `render.py`
  - shared rendering primitives for consistent CLI output: key-value column width (`KEY_WIDTH`), change-line symbols and colors (`CHANGE_SYMBOLS`), padded key formatting (`padded_key()`), change line rendering (`render_change_line()`), change list rendering (`render_change_list()`), and exclusion block rendering (`render_exclusion_block()`)
- `reconcile.py`
  - desired-state modeling, diffing, atomic apply, report formatting
  - shared project build pipeline via `build_project_desired_state()`
  - project-level cleanup via `clean_project()`
  - multi-project reconcile via `reconcile_all()`
  - machine-level uninstall via `uninstall_all()`
- `state.py`
  - project-local managed-state serialization and validation
- `install_launchagent.py`
  - LaunchAgent label generation, plist rendering (per-project and global with PATH baking), plist installation via `launchctl bootstrap`, plist uninstallation via `launchctl bootout`
  - bridge LaunchAgent plist discovery via `find_bridge_launchagents()`
- `release_bundle.py`
  - GitHub Release installer asset generation for offline wheelhouse installs

## 15. Testing Strategy

Tests are fixture-driven and isolated under `tests/`.

The suite currently verifies:

- project discovery rules
- semver behavior
- plugin symlink resolution
- `CLAUDE.md` shim safety
- agent translation, `.toml` file rendering, sandbox mode derivation, and global agent registry tracking
- skill translation, relocation rewriting, vendoring, and standalone skill translation
- prompt translation from commands, variable pass-through, provenance marking, plugin resource vendoring through prompt translation, and standalone command-to-prompt translation
- exclusion filtering for plugins and standalone sources with part-count disambiguation
- reconcile idempotence, stale cleanup, ownership safety, diff reporting, and global instructions bridging
- CLI command behavior including multi-source integration
- end-to-end multi-source scenario testing with all discovery scopes
- LaunchAgent rendering, installation, uninstallation, and status reporting (global plist model with PATH baking)
- plugin resource detection, path rewriting, and transitive dependency detection
- plugin resource vendoring through skill and agent translation pipelines
- plugin resource ownership tracking in the global registry with multi-project ownership and content hash fast path
- plugin resource cleanup in clean and uninstall commands
- project-level clean and machine-level uninstall
- multi-project reconcile --all with missing project error handling
- scan-based project discovery: config loading, glob expansion, exclude filtering, structural filters (symlinks, submodules, worktrees, git roots, Claude presence), scan/registry merge, deduplication
- global registry projects list round-tripping and backwards compatibility
- doctor reporting and release-bundle generation
- activity log entry serialization, JSONL round-tripping, date-range reads, filtering, retention pruning, and human-readable formatting
- global config loading: default fallback, valid TOML, missing file, invalid values

The test suite is the executable check for the invariants described in this file.

## 16. Current Constraints and Known Simplifications

These are current implemented simplifications, not necessarily permanent design ideals:

- the `claude` CLI must be available on PATH — without it, `discover()` raises a `DiscoveryError` and the bridge cannot function
- Claude `model` hints are preserved as metadata only; agents rely on Codex's native model selection
- Claude tools are mapped to a coarse `sandbox_mode` value (`workspace-write`, `read-only`, or omitted) rather than individual tool identifiers — unsupported Claude tools are hard errors rather than being silently dropped; the `tools` frontmatter field accepts both YAML lists and comma-separated strings
- agents use Codex's native auto-discovery from `.codex/agents/` and `~/.codex/agents/` — no `config.toml` is generated
- frontmatter parsing uses safe YAML loading for frontmatter blocks plus strict
  post-parse validation of supported runtime shapes
- exclusion ids are exact-match identifiers, not wildcard/glob patterns
- commands are translated to native Codex prompt files (`~/.codex/prompts/`) rather than Codex skills, avoiding namespace collisions with the skill directory entirely
- LaunchAgent scheduling with automatic `launchctl bootstrap/bootout` is supported; autosync uses a periodic schedule, not a file-watcher
- MCP planning assumes `mcp_servers` is a TOML table when the document parses successfully — a hand-crafted scalar value (`mcp_servers = "oops"`) would pass TOML validation but crash during apply, leaving the registry inconsistent
- MCP translation remaps exact whole-value `${VAR}` / `$VAR` header values to native Codex header fields when possible; stdio env templates always run through a bridge-owned launcher that expands them at runtime before execing the original MCP server command, preserving Claude-style behavior for unset, aliased, inline, and defaulted refs
- MCP server names must match `[A-Za-z0-9_-]`; servers with dots, spaces, or other characters are skipped with a diagnostic — this is stricter than TOML's quoted-key support but required for registry key safety and Codex `mcp__<server>__<tool>` naming

Any change to these constraints should update this file.

## 17. Maintenance Rules

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

If you're deferring something, add it to `FOLLOW_UPS.md`, not here. This document describes what IS; FOLLOW_UPS.md tracks what's next.
