# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Status and reconcile reports now render a `release` column and
  per-item `!` lines for pending ownership drops on shared global
  artifacts (skills, agents, prompts, vendored plugin resource dirs,
  MCP servers). Previously these mutations were silent: `status` showed
  `remove = 0` and `in_sync` even though the next reconcile would
  update the global registry.
- Activity log `summary` now carries a `released` count alongside
  `created`, `updated`, and `removed`, so daily JSONL logs correctly
  reflect runs that only dropped ownership on shared entries.

### Changed

- The five global-registry planners (`_plan_skill_mutations`,
  `_plan_global_agent_mutations`, `_plan_prompt_mutations`,
  `_plan_plugin_resource_mutations`, `_plan_mcp_server_mutations`) emit
  a new `Change` with kind `"release"` when the current project drops
  an ownership claim on a shared entry that other projects still own.
  `_apply_changes` treats `release` as a filesystem no-op; the registry
  write queued on the same plan carries the state transition.
- `_apply_mcp_server_changes` now excludes released global MCP names
  from the "previously owned by this project" set, so a project
  releasing a shared MCP entry no longer rewrites the global
  `~/.codex/config.toml` and briefly removes an entry that other
  projects still rely on.

## [1.4.0] - 2026-04-23

### Added

- `config exclude add` now surfaces scope-aware guidance when the excluded
  entity is user-global (plugin, `marketplace/plugin/name` skill/agent/command,
  `user/<name>` skill/agent/command, or an MCP server with global scope).
  Project-scope exclusions print a hint that the exclusion only drops this
  project's ownership claim and suggest rerunning with `--global` to apply
  across all projects. Every successful user-global exclusion also prints a
  reminder to run `cc-codex-bridge reconcile --all`, since the shared Codex
  entry is only removed once the last owning project drops it.

## [1.3.3] - 2026-04-06

### Fixed

- Bridged skills using `${CLAUDE_SKILL_DIR}` references now resolve to the Codex install path instead of the Claude Code plugin cache path

## [1.3.2] - 2026-04-02

### Fixed

- Offline wheelhouse now includes x86_64 wheels alongside arm64. Previously
  only arm64 wheels were bundled, causing install failures when the system
  `python3` was an x86_64 binary (e.g. Homebrew under Rosetta).

## [1.3.1] - 2026-04-02

### Fixed

- Installer checksum verification always failed due to a spurious leading
  space in the grep pattern matching `shasum -c` output.

## [1.3.0] - 2026-04-02

### Added

- MCP translation now falls back to a bridge-owned stdio launcher when Claude
  Code stdio env values use template expansion, preserving Claude-style
  runtime semantics while forwarding referenced source vars via `env_vars`.
- MCP translation now routes `${VAR}` and `$VAR` header values to Codex's
  `env_http_headers` field instead of treating them as literal strings in
  `http_headers`.

### Fixed

- HTTP MCP URLs containing `${VAR}` or `$VAR` references now emit a
  diagnostic instead of being passed through silently as if Codex could
  expand them.
- Non-string values in Claude Code MCP stdio `env` maps are now filtered out
  instead of producing broken TOML output for Codex.
- stdio env templates now preserve Claude Code semantics for unset, aliased,
  inline, and defaulted refs instead of collapsing some cases to lossy native
  Codex env forwarding.
- mixed Bearer Authorization header templates are now preserved with a
  diagnostic instead of being dropped as literal bearer tokens.

## [1.2.2] - 2026-04-01

### Added

- `config exclude add --global` now removes redundant project-level
  exclusions for the same entity across all registered projects.

## [1.2.1] - 2026-04-01

### Changed

- `config exclude add/remove plugin` now prints a note reminding users
  that MCP servers must be excluded separately.
- `--exclude-plugin` CLI help text clarifies that MCP servers are not
  covered by plugin exclusions.

### Fixed

- `status` command now shows MCP server names instead of repeating the
  config.toml path for each pending MCP change.
- `status` command now shows `EXCLUDED_MCP_SERVERS` section when MCP
  servers are excluded.
- `config show` now displays the `Exclude mcp_servers:` section with
  correct source attribution.

## [1.2.0] - 2026-03-29

### Added

- MCP server bridging: Claude Code MCP server definitions are now discovered,
  translated, and written to Codex's `config.toml`. Supports stdio and HTTP
  transports. Global servers write to `~/.codex/config.toml`, project servers
  to `.codex/config.toml`. Ownership tracked with drift detection.
- MCP servers visible in `status` and `reconcile` output.
- MCP server exclusion support via `bridge.toml` `mcp_servers` list,
  `--exclude-mcp-server` CLI flag, and `config exclude add mcp_server:<name>`.
- Clean and uninstall remove bridge-owned MCP entries from config.toml files.
- New runtime dependency: `tomlkit` for round-trip TOML editing.
- New reference docs: `docs/claude-code-mcp-reference.md` and
  `docs/mcp-bridge-mapping.md`.
- Degraded MCP discovery: corrupt `~/.claude.json` or `.mcp.json` files no
  longer trigger removal of previously-bridged MCP entries. The bridge warns
  and preserves existing state until the file is fixed.

### Fixed

- MCP reconcile now verifies on-disk presence of bridge-owned entries in
  `config.toml`, not just stored content hashes — externally deleted entries
  are restored on the next run.
- MCP planning scope-gates `config.toml` reads: a corrupt global config no
  longer blocks project-only MCP sync, and vice versa.
- First-time adoption of an existing bridge-managed global MCP server now
  updates `config.toml` when the definition changed, instead of silently
  skipping the entry.
- Unreadable MCP config files (`PermissionError`, I/O failures) and non-dict
  JSON roots now trigger degraded discovery instead of being treated as empty.
- MCP type validation: `command` and `url` must be strings at discovery;
  `args` must be a list, `env` must be a dict, and header values must be
  strings at translation — non-conforming values are skipped instead of
  crashing.
- `config exclude add/remove` CLI now accepts `mcp_server` as an entity kind.
- `uninstall_all` catches `ValueError` and `OSError` during MCP config
  cleanup, preventing one corrupt project from aborting the entire uninstall.
- Project `.codex/config.toml` paths are now containment-checked against the
  project root, preventing writes through symlinked `.codex/` directories.

## [1.1.0] - 2026-03-28

### Added
- `config exclude add` and `config exclude remove` now show scope attribution
  in output messages — "(global)" or "(project: path)" — so the user always
  knows which config file was modified
- All `config exclude` and `config log` subcommands now have full help text:
  descriptions, positional argument explanations, and option documentation

### Fixed
- `config exclude add` exception handler narrowed from broad `except Exception`
  to `except (DiscoveryError, OSError, UnicodeError)` — unexpected errors now
  propagate instead of being silently swallowed
- `config exclude add` in global scope no longer offers project-scoped entities
  (`project/skill-name`) as valid exclusion targets — global config only
  validates plugin and user entities
- `config exclude` scope detection now falls back to CWD (consistent with
  `config show` and `config check`) — previously it always targeted global
  config when `--project` was omitted, regardless of working directory
- Removed `--cache-dir` and `--claude-home` from `config exclude add` — these
  discovery override flags were unique to this subcommand and unnecessary
  (defaults cover all practical cases)

## [1.0.1] - 2026-03-27

### Fixed
- DESIGN.md synced with current architecture: `autosync` subcommand group,
  `render.py` module, LaunchAgent label and PATH baking changes
- Stale `install-launchagent` references in README.md activity log section and
  `LogEntry.action` docstring updated to `autosync install` / `autosync-install`

## [1.0.0] - 2026-03-27

First stable release. The core bridge pipeline (reconcile, status, clean,
doctor, autosync, config, log) is complete and has been running in daily use.
No breaking changes from 0.26.0 — this is a stability declaration.

## [0.26.0] - 2026-03-27

### Added
- `autosync status` now displays full agent configuration: label, plist path,
  interval (raw + human-readable), executable path, working directory, PATH
  environment status (`set (N entries)` / `not set`), start-at-login, and log
  file paths — values matching their defaults are marked `(default)` in dim
- `autosync status` output restructured: overall status and command at the top,
  followed by an indented `CONFIGURATION:` section
- `autosync install` bakes the current shell `PATH` into the plist
  `EnvironmentVariables` so the LaunchAgent can locate the `claude` CLI at
  runtime (macOS strips PATH in LaunchAgents, causing "Claude CLI not found"
  errors on every scheduled run)

### Fixed
- LaunchAgent was silently failing on every scheduled run because `claude` was
  not on the stripped macOS LaunchAgent PATH — re-run `autosync install` to
  pick up the fix

## [0.25.0] - 2026-03-27

### Changed
- CLI output: introduce `render.py` as single source of truth for key-value
  column width (`KEY_WIDTH=18`), change-line symbols (+/~/−), and exclusion
  block rendering
- Rename `TRANSLATED_PROMPTS` key to `GENERATED_PROMPTS` in `reconcile` and
  `status` output — consistent terminology with agents and skills
- `reconcile`/`clean` change list now uses colored +/~/− symbols instead of
  `CREATE:`/`UPDATE:`/`REMOVE:` prefixes — matches `log show` output
- `status` pending-change detail lines now use indented +/~/− format
  (consistent with `reconcile`/`clean`) instead of `SKILL_CREATE:`/`AGENT_UPDATE:` flat keys
- `status`/`reconcile` exclusion block suppressed entirely when nothing is
  excluded (was always emitting 4 zero-count lines)
- `reconcile`/`status` summary keys padded to `KEY_WIDTH` for column alignment
- `log show` change symbols and action column are now colored
- `uninstall` output now uses colors (was the only major formatter with no colors)
- `doctor` CHECK lines use fixed-width column layout instead of `status=ok message=`
  inline key=value format
- `autosync status`: `AUTOSYNC:` label always uses key color (was `warn` when inactive)
- `autosync install`: only `WARNING:` label colored, not the full warning sentence
- `reconcile --all --dry-run` banner: "the following changes are pending" (was
  "no changes applied")
- `upgrade`: `padded_key()` used for `INSTALLED:`/`LATEST:` alignment (was
  manual hardcoded spacing)
- `config show`: exclusion section empty-state uses consistent indented `(none)`
  line (was appended to padded label at inconsistent column)

## [0.24.0] - 2026-03-26

### Added
- `config show` and `config check` now lead with an empty line, consistent with other commands
- `config check` output colored: heading in bold blue, ✓ in bold green, ✗ in bold red, summary verdict colored by outcome
- `--all` output colored: `OK` in green, pending change counts in yellow, errors in red, scan notes in yellow, filtered entries dimmed

### Changed
- `status --all` no longer shows "Dry run" — status is always read-only, not a dry run of reconcile
- `status --all` uses context-aware wording: `in sync` / `N pending changes` instead of `no changes` / `N changes`

## [0.23.0] - 2026-03-26

### Added
- `autosync status` subcommand — reports whether the background sync agent is installed and loaded via launchd
- Shell command fragments colored bold cyan (`cmd` color) throughout CLI output and `--help` descriptions: `reconcile --all`, `cc-codex-bridge upgrade`, `git pull && pip install -e .`, `deactivate`, `curl` install script, `launchctl` commands
- Flag names (`--check`, `--all`, `--project`, `--diff`, `--dry-run`, `--json`) colored bold cyan in descriptions and error messages, consistent with argparse long-option styling

### Changed
- LaunchAgent label renamed from `com.openai.codex-bridge.*` to `cc-codex-bridge.*` — the old prefix used OpenAI's domain, which was wrong; the new prefix matches the tool name. Existing users must manually remove the old plist and re-run `autosync install`
- `autosync install` (formerly `install-launchagent`) now loads the agent via `launchctl bootstrap` immediately after writing the plist; re-runs boot out the old instance first. No manual `launchctl` step needed
- macOS background-activity notification now shows `cc-codex-bridge` instead of `python3.14` — the plist `Program` key is now set to the console script (detected via `shutil.which`) rather than the Python interpreter

## [0.22.0] - 2026-03-26

### Added
- `autosync` command with `install`, `uninstall`, and `status` subcommands — `autosync status` reports whether the background sync agent is installed and loaded via launchd

### Changed
- `install-launchagent` and `uninstall-launchagent` top-level commands replaced by `autosync install` and `autosync uninstall`; `--dry-run` dropped from uninstall (one-file op, trivially reversible with `autosync install`)
- `upgrade` moved in help output to appear just above `uninstall`

## [0.21.0] - 2026-03-26

### Added
- `upgrade` command — checks the latest GitHub release, compares with the installed version, and runs the official install script in place; `--check` reports the available version without installing; editable (development) installs are detected and refused with a clear message
- `doctor` version check — best-effort network check reports `warning` when a newer release is available on GitHub (with a hint to run `upgrade`), `ok` when up to date or when GitHub is unreachable (3-second timeout, never delays doctor)
- ANSI color output across all commands using Python 3.14's `_colorize` theme (same palette as argparse help): keys in bold blue, status values in green/yellow/red by meaning, create/update/remove counts colored semantically, excluded items dimmed; colors suppressed automatically when output is not a TTY
- `status` now shows full discovery info matching `reconcile` output: project root, AGENTS.md path, CLAUDE.md action, plugin list with per-plugin skill/agent/prompt counts, generated totals — output grouped into sections with blank lines between them
- Help description colors: program name in bold magenta, version in bold green; `Detailed documentation:` link added to `--help`
- Error messages colored bold red; program-name prefix removed; blank line before message for visual breathing room

### Added
- `autosync` command with `install`, `uninstall`, and `status` subcommands — replaces the former `install-launchagent` top-level command with a cleaner grouped interface; `autosync status` reports whether the LaunchAgent is installed and loaded

### Changed
- `validate` command removed — `status` now covers everything `validate` did (discovery, plugin list, generated counts, exclusions) plus the disk diff; use `status` where `validate` was used
- `install-launchagent` and `uninstall-launchagent` top-level commands replaced by `autosync install` and `autosync uninstall`
- `print-launchagent` command removed — use `autosync install` directly
- `reconcile` empty-state message changed from "No changes." to "All good. No changes needed."
- Status output keys padded so values align in a single column; plugin sub-lines (source, skills, agents, prompts) each on their own indented line with aligned `=` signs

## [0.20.0] - 2026-03-26

### Added
- Content hash tracking for managed project files — state now records sha256 content hashes alongside managed file paths
- Drift detection on reconcile: externally modified managed files are preserved instead of being overwritten
- Drift detection on clean: externally modified managed files are preserved instead of being removed
- Drift reporting in `status` command output (both text and JSON) — shows which managed files were externally modified
- Clean bootstrap reversal: when both AGENTS.md and CLAUDE.md are unedited, clean restores CLAUDE.md to its original content

### Fixed
- First reconcile after a v8→v9 state migration now backfills hashes for preserved managed files, so later edits are reported as drift and protected from `clean`
- First reconcile after a v8→v9 state migration now treats empty migrated hashes as "preserve only", so managed project files are not overwritten or removed until a trusted hash can be recorded
- Drift-skipped stale project files now stay bridge-managed in state, so later reconciles preserve ownership instead of failing with "Refusing to overwrite non-generated project file"
- `compute_project_drift()` now rejects corrupted managed project paths from bridge state instead of hashing paths outside the project root
- Top-level `status` fields now treat drifted files as pending work, even when reconcile has no file mutations to apply
- `clean` now preserves `AGENTS.md` when `CLAUDE.md` is a symlink to it, avoiding a dangling project instructions symlink
- Preserved symlink state no longer gets backfilled into a trusted hash when the symlink is later replaced with a regular file, so `clean` cannot re-adopt and remove or bootstrap-restore user-authored instructions

### Changed
- Bootstrap is no longer a separate code path — it is handled by normal reconcile, including both AGENTS.md creation and CLAUDE.md shim replacement
- `managed_project_files` in bridge state changed from a list of paths to a path-to-content-hash mapping
- State version bumped from 8 to 10 (v8-v9 state files are automatically migrated on read)
- `AGENTS.md` is now a managed project file (added to the allowlist alongside `CLAUDE.md` and `.codex/agents/*.toml`)

### Removed
- `execute_bootstrap()` function and all CLI/reconcile bootstrap handling code
- Separate dry-run bootstrap change synthesis (now handled naturally by the reconcile pipeline)

## [0.19.8] - 2026-03-26

### Fixed
- `GENERATED_SKILLS` count now excludes skills whose target root is a symlink, matching actual bridge behavior

## [0.19.7] - 2026-03-26

### Changed
- Project skill management is skipped when `.codex/skills/` is a symlink (e.g. to `.ai/skills/`), since the project already provides Codex-visible skills through the symlink
- Global skill management is skipped when `~/.codex/skills/` is a symlink

## [0.19.6] - 2026-03-26

### Fixed
- Reconcile now refuses to write through symlinked `.codex/skills/` or `~/.codex/skills/` directories, preventing silent modification of symlink targets (e.g. `.ai/skills/` → source files tracked by git)

## [0.19.5] - 2026-03-26

### Changed
- Reconcile now adopts hand-bridged project skill directories when their file tree is a subset of what the bridge would generate, instead of refusing to overwrite them

## [0.19.4] - 2026-03-26

### Fixed
- `reconcile --dry-run` now previews bootstrap changes (AGENTS.md creation + CLAUDE.md update) without modifying files, instead of erroring with "Bootstrap required"
- `reconcile --all --dry-run` now previews bootstrap-pending projects instead of reporting them as errors

## [0.19.3] - 2026-03-26

### Fixed
- `status` plain text output now includes `GENERATED_AGENTS` and `GENERATED_SKILLS` counts (previously only showed `TRANSLATED_PROMPTS`)
- `status` plain text output now enumerates individual paths for agent, prompt, and global changes (previously only enumerated project file and skill paths)
- `validate` and `reconcile` plain text output now includes `VERSION` line, matching JSON output and `status` command

## [0.19.2] - 2026-03-26

### Added
- `--json` support on `validate`, `clean`, single-project `reconcile`, `config scan list`, and `config exclude list` — all commands now support structured JSON output

### Changed
- `--exclude-*` flags removed from `status` and `validate` (exclusions on read-only commands are now driven solely by config files; flags remain on `reconcile`)
- `--codex-home` removed from `validate` (read-only, never writes to codex) and `clean` (reads codex home from bridge state)
- `reconcile --json` no longer requires `--all`

## [0.19.1] - 2026-03-25

### Changed
- Interactive pickers show the prompt label as a heading before the numbered list, with `Enter choice [1-N]:` for input
- ESC key cancels the current selection and clears its output; in multi-step flows (e.g. `config exclude add`) ESC goes back one level instead of exiting
- Already-excluded entities are filtered from the `config exclude add` picker; kinds with no remaining candidates are hidden

### Fixed
- Confirmation messages after interactive selection no longer start mid-line (raw terminal mode now emits carriage return before newline)

## [0.19.0] - 2026-03-25

### Added
- `config show` command — displays effective config with source attribution (global/project/default)
- `config check` command — audits config files against current environment (TOML validity, scan paths, exclusion entities, structural correctness)
- `config scan add/remove/list` — manage scan paths from CLI with glob validation
- `config exclude add/remove/list` — manage exclusions from CLI with discovery-backed validation
- `config log set-retention` — set log retention period from CLI
- Interactive guided flows for all config mutations when values are omitted (TTY-only)
- `doctor` now includes a config health check section
- `tomli-w` runtime dependency for TOML writing

### Changed
- CLI help groups commands by purpose: core workflow → configuration → inspection → cleanup → automation

## [0.18.0] - 2026-03-25

### Added
- Global `[exclude]` section in `~/.cc-codex-bridge/config.toml` for machine-wide plugin/skill/agent/command exclusions — unioned with per-project `.codex/bridge.toml`; CLI `--exclude-*` flags replace the merged set for that category

## [0.17.3] - 2026-03-24

### Added
- Every command and subcommand now shows a description paragraph in `--help` explaining what it does and when to use it
- Top-level help shows a `Commands:` section with one-line descriptions instead of the argparse default "positional arguments" group
- Section headings capitalized throughout (`Usage:`, `Options:`, `Commands:`)

### Changed
- All `--help` flag descriptions rewritten for precision: defaults shown for every overridable value, valid filter values documented, consistent phrasing across commands
- Top-level description updated to "Bridge your local Claude Code setup into Codex so both tools stay equally effective"
- All help text descriptions start with a capital letter, including argparse built-in flags

### Fixed
- CLI help text alignment now auto-sizes per subcommand instead of wrapping descriptions onto the next line for longer flags like `--exclude-command`

## [0.17.1] - 2026-03-23

### Fixed
- Generated agent `.toml` files now escape backslashes and TOML-disallowed control characters in `developer_instructions`, fixing Codex CLI parse errors on agents with grep patterns (`\|`), CSS Unicode escapes (`\2197`), or similar content
- `clean` command now reports "Ownership released" instead of the misleading "Nothing to clean." when a co-owner project is cleaned without deleting shared artifacts

## [0.17.0] - 2026-03-21

### Added
- Daily JSONL activity log for state-changing CLI operations (reconcile, clean, install-launchagent)
- Global config file (`~/.cc-codex-bridge/config.toml`) with `[log] log_retention_days` setting (default: 90 days)
- `log show` subcommand with filters: `--since`, `--until`, `--days`, `--project`, `--action`, `--type`, `--json`
- `log prune` subcommand for manual log retention cleanup
- Auto-prune of expired logs after every logged operation
- Uninstall now cleans up activity log files alongside other bridge artifacts

## [0.16.0] - 2026-03-21

### Added
- Plugin-qualified references (`plugin:skill-name`, `plugin:command-name`) in generated content are rewritten to Codex equivalents (`$codex-name`)

### Changed
- Status output now categorizes pending changes into `PROJECT_FILES`, `SKILLS`, `AGENTS`, `PROMPTS`, and `GLOBAL` instead of lumping global artifacts into `PROJECT_FILES`

### Fixed
- `--all` mode no longer shows "No registered projects." when the scan summary already reported candidates
- Scan config validation errors now reference the correct top-level key names (`scan_paths`, `exclude_paths`) instead of the incorrect `scan.` prefix

## [0.15.1] - 2026-03-21

### Fixed
- Prompt `description` and `argument-hint` values are now single-quoted in generated frontmatter, preventing invalid YAML when values contain colons, brackets, or single quotes
- Shared global skills and prompts now allow existing owners to advance the content hash on plugin or source upgrade, while non-owner projects with different content at the same name are still rejected as conflicts
- Bulk `--all` mode now forwards `--exclude-plugin`, `--exclude-skill`, `--exclude-agent`, `--exclude-command`, `--claude-home`, and `--cache-dir` flags to each per-project build

## [0.15.0] - 2026-03-20

### Changed
- Commands are now translated to native Codex prompt files (`~/.codex/prompts/*.md`) instead of Codex skills (`~/.codex/skills/cmd-*/SKILL.md`)
- `$ARGUMENTS` and positional args (`$1`-`$9`) are passed through natively instead of being replaced with fallback text
- `argument-hint` frontmatter is preserved in prompt output (previously dropped for skills)
- Project-level commands get a `--<project-dirname>` suffix (e.g., `build--my-app.md`)
- CLI output label `TRANSLATED_COMMANDS` renamed to `TRANSLATED_PROMPTS`

### Removed
- `translate_commands` module (replaced by `translate_prompts`)
- `cmd-` prefix naming convention for command-derived artifacts

## [0.14.0] - 2026-03-20

### Added

- Bulk scan discovery via `~/.cc-codex-bridge/config.toml` with `scan_paths` and `exclude_paths` glob lists. Scan discovers git repos with Claude Code presence (`AGENTS.md`, `CLAUDE.md`, or `.claude/` directory) while rejecting symlinks, submodules, and worktrees.
- `--all` flag for `reconcile`, `validate`, and `status` commands. Merges scan-discovered projects with registry projects and operates on the union.
- `validate --all` for lightweight bulk project validation.
- `status --all` for bulk sync state overview (supports `--json`).
- Scan discovery reporting in `--all` output (text and JSON): bridgeable, not-bridgeable, and filtered candidates.

### Changed

- LaunchAgent plist now runs `reconcile --all` instead of the removed `reconcile-all` command.

### Fixed

- Symlinks in skill, command, and agent source trees are now followed instead of rejected, matching Claude Code's behavior.
- Agent frontmatter `tools:` field now accepts comma-separated strings (e.g., `tools: Read, Write, Edit`) in addition to YAML lists.
- Unrecognized Claude tools (MCP tools, WebFetch, NotebookEdit, Agent, etc.) are now silently accepted instead of blocking agent translation. Only the core read/write tools affect sandbox mode derivation.
- Frontmatter values containing YAML-confusing characters (colons in MCP tool names, brackets in `argument-hint`) are auto-quoted before parsing.
- Sibling skill reference detection (`../name/`) now skips fenced code blocks to avoid false positives from shell commands.
- CLAUDE.md containing any reference to AGENTS.md (e.g., `@AGENTS.md` without trailing newline, `Read and follow AGENTS.md`) is now preserved instead of rejected.
- Hand-authored CLAUDE.md no longer blocks project reconciliation. The bridge skips CLAUDE.md management and proceeds with agents, skills, and commands.

### Removed

- `reconcile-all` subcommand (replaced by `reconcile --all`).

## [0.13.0] - 2026-03-19

### Added

- Plugin resource ownership tracking in the global registry with multi-project ownership and content hash fast path. Vendored plugin resource directories are now shared safely across projects and removed only when no project claims them.

### Changed

- State version bumped to 8. Existing bridge state files are re-created on next reconcile.

### Removed

- `managed_plugin_dirs` field removed from BridgeState (replaced by global registry tracking).

## [0.12.1] - 2026-03-18

### Fixed

- `reconcile` now includes the state file write in its change report. Previously, when global artifacts were already present (e.g. a second project onboarded after a first), the reconcile printed "No changes." even though it created the project state file.

## [0.12.0] - 2026-03-18

### Added

- `-v`/`--version` flag: `cc-codex-bridge --version` now prints the installed version.
- `VERSION` field in `status` output (both text and JSON formats).
- `VERSION` field in `doctor` output (both text and JSON formats).

## [0.11.0] - 2026-03-18

### Changed

- Agent naming redesign: generated agent install names and TOML filenames now use bare file stems instead of `marketplace-plugin-agent` or `scope-agent` prefixes, matching the skill naming convention introduced in 0.9.0. Agent names like `some-cc-plugin:architecture-reviewer.toml` become `architecture-reviewer.toml`.
- Agent collision resolution: when multiple agents share a file stem, standalone agents (user/project) win the bare name over plugin agents; among plugins, `(marketplace, plugin_name)` sort order determines priority. Collisions get `-alt`, `-alt-2`, etc. suffixes.
- Agent name validation: generated agent names exceeding 64 characters after suffixing are now a hard error.

### Added

- `assign_agent_names()` function for deterministic, collision-free agent name assignment across all global agent candidates, mirroring `assign_skill_names()`.
- `marketplace` and `plugin_name` fields on `GeneratedAgentFile` for priority-based collision resolution.

### Removed

- Three agent name normalization functions (`_normalize_role_namespace`, `_normalize_name`, `_normalize_prompt_component`) and their regex constants — agent identity now derives from the filesystem name, not the frontmatter name.

## [0.10.3] - 2026-03-18

### Fixed

- Doctor tests no longer depend on `claude` CLI being available in CI.
- CI smoke checks tolerate missing `claude` CLI in the runner environment.
- Release workflow updated from macos-13 (deprecated) to macos-14/macos-15 runners.

## [0.10.0] - 2026-03-18

### Changed

- Agent translation now produces native Codex `.toml` files instead of `.md` prompt files and `config.toml` entries. Global agents (plugin + user) install to `~/.codex/agents/` with registry tracking. Project agents install to `.codex/agents/`.
- Claude tool lists now map to `sandbox_mode` (`workspace-write`, `read-only`, or omitted) instead of per-agent Codex tool arrays.
- State version bumped to 5 to trigger re-reconciliation for migration.

### Added

- `render_agent_toml.py` module for rendering self-contained Codex agent `.toml` files.
- `derive_sandbox_mode()` for mapping Claude tools to Codex sandbox modes.
- `GlobalAgentEntry` and `hash_agent_file()` for agent ownership tracking in the global registry.
- Plugin enablement filtering: `discover_latest_plugins()` now queries `claude plugins list --json` to only sync plugins that are actually enabled. The `doctor` command checks for `claude` CLI availability.

### Fixed

- `clean` now fails when the global registry is missing or corrupt instead of silently orphaning global skills and agents.
- `uninstall` treats accessible projects with missing state files as errors, preventing premature global artifact deletion.
- Global skill directories now reject symlinks during reconcile, matching the project skill hardening.
- Uninstall JSON output now includes global agent file removals.
- Uninstall dry-run text summary uses `will_clean` instead of `cleaned`.
- Triple-quote sequences in agent TOML rendering are now escaped to prevent TOML parse errors.
- Shared global agents now update correctly when a plugin upgrades.

### Removed

- `render_codex_config.py` module — `config.toml` and `.codex/prompts/agents/*.md` are no longer generated.
- `GeneratedAgentRole` dataclass — replaced by `GeneratedAgentFile`.
- `validate_merged_roles()` — replaced by `validate_merged_agents()`.
- `translate_tools()` and `TOOL_TRANSLATIONS` — superseded by `derive_sandbox_mode()`.
- Legacy `.codex/config.toml` and `.codex/prompts/agents/*.md` from managed-path allowlist.
- Pre-projects-list registry owner fallback in `uninstall`.
- Dead `codex_home` parameter from `clean_project()`.

## [0.9.0] - 2026-03-17

### Changed

- Skill naming redesign: all generated skill install names now use bare skill directory names instead of `marketplace-plugin-skill` or `user-skill` prefixes. This complies with the Agent Skills standard's 64-character name limit and eliminates Codex silently skipping skills with long names.
- Collision resolution: when multiple skills share a directory name, user skills win the bare name over plugin skills; among plugins, `(marketplace, plugin_name)` sort order determines priority. Collisions get `-alt`, `-alt-2`, etc. suffixes.
- Name validation: generated skill names exceeding 64 characters after suffixing are now a hard error.

### Added

- `assign_skill_names()` function for deterministic, collision-free skill name assignment across all global skill candidates.
- `Edit` tool translation: Claude agents using the `Edit` tool now translate to Codex `edit` instead of producing hard diagnostic errors.
- Authoritative reference docs in `docs/`: `agent-skills-standard.md` (open Agent Skills spec) and `codex-cli-reference.md` (Codex CLI behaviors).

### Fixed

- Sibling skill reference regex no longer matches `../name/` inside triple-dot ellipsis paths (e.g., `~/.claude/plugins/cache/.../plugin-name/` in comments).
- `clean` no longer deletes a pre-existing `CLAUDE.md` that the bridge preserved but did not create. Previously, any `CLAUDE.md` containing `@AGENTS.md` was recorded as managed regardless of origin.

## [0.8.1] - 2026-03-17

### Fixed

- Fixed `clean` trusting corrupted `managed_project_files` from state without allowlist validation, which could delete hand-authored files like `AGENTS.md` when the state file was corrupted.
- Fixed `uninstall` exiting 0 unconditionally even when accessible projects had cleanup failures, masking partial uninstall results.
- Fixed `clean` and `uninstall` crashing with `NotADirectoryError` when a regular file existed at a managed skill directory path.
- Added trailing summary line to `uninstall` text output showing cleaned/skipped/no-state project counts.

## [0.8.0] - 2026-03-17

### Added

- Bootstrap support: when a project has `CLAUDE.md` but no `AGENTS.md`, `reconcile` copies `CLAUDE.md` to `AGENTS.md` and replaces `CLAUDE.md` with the `@AGENTS.md` shim. Read-only commands (`status`, `validate`, `reconcile --dry-run`) report that bootstrap is needed without mutating files.
- Project discovery now accepts `CLAUDE.md` as a fallback project marker when `AGENTS.md` is absent.

### Fixed

- Bootstrap now rejects `CLAUDE.md` that is the `@AGENTS.md` shim (prevents creating a self-referencing AGENTS.md when the original is lost)
- Bootstrap now refuses to write through a symlinked AGENTS.md target (prevents writing outside the project tree)
- `reconcile-all` now allows projects with `CLAUDE.md` but no `AGENTS.md` to reach the bootstrap path
- Reverted malformed plugin cache tolerance — a plugin directory with no valid semantic versions is again a hard discovery failure, preventing silent artifact removal when the cache is temporarily corrupted

## [0.7.1] - 2026-03-16

### Fixed

- Cross-scope agent role_name and prompt_relpath collisions are now detected after merging all scopes, preventing silently corrupt output
- Installed agent translation now checks for duplicate prompt paths, consistent with standalone agent translation
- Skill translation rejects symlinked files, symlinked subdirectories, and symlinked SKILL.md — not just symlinked resource directories
- `reconcile-all` and `uninstall` now use the same strict registry loader as normal reconcile, failing on symlinked registries instead of silently skipping them

## [0.7.0] - 2026-03-16

### Fixed

- Reconcile and clean now validate that all write/delete targets resolve within their
  expected root directory, preventing symlinked ancestors from redirecting operations
  outside the project.
- `status` and `reconcile --dry-run` now fail with the same path-containment errors as
  real reconcile when managed write targets resolve outside the project.
- Corrupted bridge state can no longer authorize arbitrary project-skill directory
  removals; managed project skill directory names are now validated before planning and
  cleanup.
- Skill translation rejects symlinked resource directories and sibling references
  instead of following them.
- `status` and `reconcile --dry-run` now report bridge state file mutations (create/update)
  that a real reconcile would perform.

### Changed

- Project skills now use directory-snapshot comparison matching global skills — extra files
  in managed project skill directories are detected and trigger updates.
- Bridge state version bumped to 4 with new `managed_project_skill_dirs` field.
- `print-launchagent` and `install-launchagent` no longer accept `--project`,
  `--cache-dir`, `--claude-home`, or `--codex-home` flags.

## [0.6.1] - 2026-03-16

### Fixed

- Fixed `clean` using caller-supplied `--codex-home` instead of the state-recorded value, which could orphan global registry claims when the Codex home changed.
- Fixed `clean --dry-run` under-reporting removals by omitting the state file from the preview.
- Fixed project-local generated skill cleanup removing only tracked files instead of the full skill directory.
- Fixed stale bridge-generated `~/.codex/AGENTS.md` persisting when the source `~/.claude/CLAUDE.md` was removed; hand-authored files without the bridge sentinel are now preserved.
- Fixed reconcile and uninstall deleting hand-authored `~/.codex/AGENTS.md` that the bridge did not create; bridge-generated files now carry a sentinel comment for ownership detection.
- Fixed `reconcile --dry-run --diff` crashing with `KeyError` when the diff included global instructions changes.
- Fixed `reconcile-all`, `uninstall`, `clean`, and `doctor` silently accepting CLI flags they ignore (`--project`, `--claude-home`, `--cache-dir`); each command now declares only the flags it uses.
- Fixed standalone agents with names that normalize to the same role silently colliding instead of failing fast.
- Fixed `doctor` reporting the plugin cache as healthy when plugins had no valid semver versions that discovery would reject.
- Fixed reconcile silently overwriting hand-authored `~/.codex/AGENTS.md` when `~/.claude/CLAUDE.md` exists with different content; now raises an error when the existing file lacks the bridge sentinel.
- Fixed codex-home migration silently dropping the projects list from the previous registry, breaking `reconcile-all` and `uninstall` discovery for other registered projects.
- Fixed stale project-local skill directories persisting after their source was removed; reconcile now escalates to directory-level removal when all tracked files from a skill directory are stale.
- Fixed `clean` command failing when `AGENTS.md` was missing from a project that still had bridge state; now falls back to bridge state for project root resolution.

### Changed

- Extracted the hardcoded Codex model name to a single `DEFAULT_CODEX_MODEL` constant.
- Extracted the shared project build pipeline into `build_project_desired_state()` so single-project and reconcile-all paths cannot drift.

## [0.6.0] - 2026-03-12

### Added

- Added `reconcile-all` command to reconcile all registered projects in one pass.
- Added `projects` list to global registry for tracking reconciled project roots.
- Added single global LaunchAgent that runs `reconcile-all` every 30 minutes.

### Changed

- Changed `install-launchagent` to produce a global plist instead of per-project plists.
- Changed `uninstall` project discovery to use the registry projects list.
- Changed default LaunchAgent interval from 300 to 1800 seconds.

### Removed

- Removed per-project LaunchAgent support from CLI commands.

## [0.5.0] - 2026-03-11

### Added

- Added `clean` command to remove all bridge-generated artifacts from one project, release global skill registry claims, and delete last-owner skill directories.
- Added `uninstall` command to remove all bridge artifacts from the machine: discovers projects from the global registry, cleans each accessible one, removes remaining global skills/registry/AGENTS.md, and removes bridge LaunchAgent plists.
- Added `--dry-run` support for both `clean` and `uninstall`.
- Added `--json` output for `uninstall --dry-run`.
- Added `find_bridge_launchagents()` for discovering bridge LaunchAgent plists by label prefix.

## [0.4.0] - 2026-03-11

### Added

- Added discovery and translation of user-level skills (`~/.claude/skills/`) and agents (`~/.claude/agents/`).
- Added discovery and translation of project-level skills (`.claude/skills/`) and agents (`.claude/agents/`).
- Added bridging of user-level `~/.claude/CLAUDE.md` to `~/.codex/AGENTS.md` (Codex's native user-global instructions file).
- Added `--claude-home` CLI flag to override the Claude home path for user-level discovery.
- Allowed the bridge to operate without any marketplace plugins when user-level or project-level sources are present.
- Added a machine-level `doctor` command with text and JSON output so installs can verify Python, Codex-home, Claude-cache, LaunchAgents, and PATH visibility before the project pipeline runs.
- Added self-contained GitHub Release installer assets: a generated `install.sh`, a bundled macOS wheelhouse archive, and release checksums for GitHub-only installs without PyPI access.
- Added a maintainer-facing `make release VERSION=X.Y.Z` command that verifies a clean worktree, runs tests, tags the release, and hands artifact publishing off to GitHub Actions.

### Changed

- Simplified plugin skill naming to always use `<marketplace>-<plugin>-<skill>` prefix, removing context-dependent collision resolution.
- Extended exclusion IDs to support 1-part (all scopes), 2-part (scope-specific), and 3-part (plugin-specific) formats via part-count disambiguation.
- Simplified the reconcile engine by replacing the transaction staging, rollback,
  and dual-lock machinery with direct atomic writes. Mid-apply failures are
  self-healed by the next idempotent reconcile run.
- Started reconcile hardening with a validated global skill-registry model, deterministic generated-skill hashing, and a trimmed project-local state payload that no longer records selected plugin identities.
- Moved generated Codex skill ownership into a global registry under the resolved Codex home, allowing identical skills to be shared safely across projects and keeping last-owner cleanup aligned with registry claims.
- Simplified the project-local bridge state so it now tracks only project-local managed files plus the last reconciled Codex home.
- Added project-first/global-second reconcile locking for mutating runs so concurrent reconciles fail fast instead of racing shared project or Codex-home writes.
- Changed unsupported Claude agent tools from silent drops into hard diagnostics, with `status` now reporting an explicit `invalid` state instead of pretending the project only has pending changes.
- Switched shared agent and skill frontmatter parsing to PyYAML safe loading for frontmatter blocks, while keeping strict post-parse validation and explicit errors for unsupported runtime shapes.
- Removed the old test-only generated-skill materialization helper so reconcile remains the single production path that writes installed Codex skill trees.
- Changed CI and release packaging smoke tests to validate offline installs from a GitHub-hosted wheelhouse bundle using `pip --no-index`.

### Fixed

- Aligned the offline installer and release wheelhouse contract so the installer now fails fast outside the explicitly bundled Python minors and the release workflow now includes Python 3.14 wheelhouse slices.
- Tightened `make release` so it only runs from `main` and atomically pushes the branch plus tag, avoiding split branch/tag release states.
- Added an early maintainer release preflight for `pytest` and `setuptools` so unsupported local interpreters fail with a `.venv` setup hint instead of dying later inside tests or editable installs.

- Hardened generated agent role and prompt naming so unsafe Claude agent names cannot escape the managed prompt directory, and cross-marketplace collisions are disambiguated deterministically.
- Escaped generated `.codex/config.toml` string values correctly so multiline Claude frontmatter fields still produce valid TOML.
- Validated bridge state schema types and migrated managed skill directories correctly when `--codex-home` changes, preventing crashes and orphaned generated skills.
- Tightened `CLAUDE.md` shim ownership checks so only the exact generated shim content is treated as generator-owned.
- Preserved generator-managed `CLAUDE.md -> AGENTS.md` symlinks across later reconciles instead of treating them as stale managed files and deleting them.
- Rejected foreign or malformed reconcile state before stale skill cleanup, including invalid managed skill directory entries and symlinked state files.
- Failed fast when a generated Codex skill references a missing sibling Claude skill instead of emitting a broken relocated path.
- Surfaced filesystem `OSError` failures as user-facing CLI errors with exit code `1` instead of uncaught exceptions.
- Surfaced UTF-8 decode failures in `CLAUDE.md`, frontmatter files, config files, state files, registries, and diffed managed text as user-facing errors instead of uncaught tracebacks.
- Accepted quoted scalar frontmatter values and simple inline lists such as `tools: [Read, Write]` in the shared parser.
- Reclaimed same-host stale reconcile lock files automatically when their recorded pid is no longer running.
- Resolved LaunchAgent commands from nested project paths using the same upward `AGENTS.md` discovery as the main pipeline.

## [0.3.0] - 2026-03-05

### Added

- Added exclusion controls for sync inputs via project `.codex/bridge.toml` and repeatable CLI flags: `--exclude-plugin`, `--exclude-skill`, and `--exclude-agent`.
- Added status/summary reporting for effective excluded plugins, skills, and agents.

## [0.2.0] - 2026-03-05

### Changed

- Renamed the project-local reconcile state artifact to `.codex/claude-code-bridge-state.json`.
- Clarified internal naming in reconcile/discovery command flow (no behavior change).
- Consolidated preview mode under `reconcile --dry-run` and `reconcile --dry-run --diff`.

### Added

- Added `status` command with `in_sync` vs `pending_changes`, categorized change reporting, and optional `--json` output.

## [0.1.0] - 2026-03-04

### Added

- Initial `cc-codex-bridge` CLI for discovering installed Claude Code plugins and generating Codex-compatible artifacts.
- Project-local generation for `CLAUDE.md`, `.codex/config.toml`, `.codex/prompts/agents/*.md`, and `.codex/bridge-state.json`.
- User-global generated Codex skill installation under `~/.codex/skills/`.
- Conservative reconcile/state management and LaunchAgent support for scheduled reconcile runs.
