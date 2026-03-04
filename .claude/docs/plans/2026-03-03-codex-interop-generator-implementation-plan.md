# Codex Interop Generator Implementation Plan

Date: 2026-03-03
Status: in progress
Depends on:
- `.claude/docs/analysis/2026-03-03-codex-claude-skills-agents-interop-analysis.md`
- `.claude/docs/plans/2026-03-03-codex-interop-generator-spec.md`

## Goal

Implement a generator that reads:

- hand-authored `AGENTS.md`
- actually installed Claude Code plugins

and produces:

- `CLAUDE.md` as `@AGENTS.md`
- `.codex/config.toml`
- `.codex/prompts/agents/*.md`
- `~/.codex/skills/*`

without changing Claude Code behavior.

## Locked Decisions

1. `AGENTS.md` is canonical and the generator must not touch it.
2. `AGENTS.md` remains strictly shared-only.
3. `CLAUDE.md` is a generated one-line shim containing only `@AGENTS.md`.
4. Generated `.codex/config.toml` is local-only and not committed.
5. Codex role config is inline in `.codex/config.toml` for v1.
6. V1 uses only actually installed Claude Code plugins as inputs.
7. If an installed plugin resolves through a symlink into a repo checkout, follow that symlink.
8. If multiple installed versions exist, pick the highest semantic version.
9. Do not generate `.claude/agents/*.md` in v1.
10. Do not use project-local Codex skill mirrors.

## Progress

### Completed: Phase 1

Implemented:

1. `codex_interop/cli.py`
2. `codex_interop/discover.py`
3. `codex_interop/model.py`
4. `codex_interop/tests/test_discovery.py`
5. `codex_interop/tests/conftest.py`
6. `codex_interop/tests/__init__.py`

Delivered behavior:

1. project resolution from `cwd`
2. project resolution from `--project`
3. clear failure when `AGENTS.md` is missing
4. installed Claude plugin discovery from cache
5. semantic-version ordering for installed plugin selection
6. malformed version directory rejection
7. symlink-following for installed plugin paths
8. combined project + installed-plugin discovery via CLI and library entrypoints
9. tests use isolated temporary project/cache fixtures rather than current project state

Verification:

```bash
pytest codex_interop/tests/test_discovery.py -q
```

Current result:

- `8 passed`

### Completed: Phase 2

Implemented:

1. `codex_interop/claude_shim.py`
2. `codex_interop/translate_agents.py`
3. `codex_interop/render_codex_config.py`
4. `codex_interop/tests/test_agents.py`
5. `codex_interop/tests/test_claude_shim.py`
6. Phase 2 validation wiring in `codex_interop/cli.py`

Delivered behavior:

1. safe `CLAUDE.md` shim decision tree
2. preservation of existing exact shim
3. preservation of `CLAUDE.md -> AGENTS.md` symlink
4. non-destructive failure for hand-authored non-shim `CLAUDE.md`
5. Claude agent frontmatter/body parsing
6. deterministic Codex role-name generation
7. Claude tool translation to Codex tool identifiers
8. deterministic prompt-file rendering under `.codex/prompts/agents/*.md`
9. deterministic inline `.codex/config.toml` rendering
10. CLI `validate` now exercises translation/rendering in memory
11. CLI validation is covered by an isolated fixture-based test

Verification:

```bash
pytest codex_interop/tests/test_discovery.py codex_interop/tests/test_agents.py codex_interop/tests/test_claude_shim.py codex_interop/tests/test_cli.py -q
python3 codex_interop/cli.py validate --project . --cache-dir ~/.claude/plugins/cache
```

Current results:

- `16 passed`
- local validate run succeeded against installed Claude plugins as a supplemental smoke check, not as part of the test contract

### Completed: Phase 3

Implemented:

1. `codex_interop/translate_skills.py`
2. `codex_interop/tests/test_skills.py`
3. Phase 3 validation wiring in `codex_interop/cli.py`
4. shared frontmatter parser hardening in `codex_interop/translate_agents.py`

Delivered behavior:

1. translation of installed Claude skills into self-contained Codex skill trees
2. spec-aligned generated skill naming where `SKILL.md` `name:` matches the installed parent directory
3. copying of official skill-layout resources from skill roots: `SKILL.md`, `scripts/`, `references/`, `assets/`, and `agents/` when present
4. preservation of executable file modes during skill materialization
5. relocation rewriting for plugin-root script references such as `<skill base directory>/../..`
6. vendoring of referenced sibling skills when a skill depends on `../<other-skill>/...` paths
7. deterministic conflict handling for generated skill install directory names
8. CLI `validate` now exercises skill translation in memory and reports generated skill counts
9. shared frontmatter parsing now supports folded block scalars and simple nested maps used by real installed plugins

Verification:

```bash
pytest codex_interop/tests/test_discovery.py codex_interop/tests/test_agents.py codex_interop/tests/test_claude_shim.py codex_interop/tests/test_cli.py codex_interop/tests/test_skills.py -q
python3 codex_interop/cli.py validate --project . --cache-dir ~/.claude/plugins/cache
```

Current results:

- `21 passed`
- local validate run succeeded against the real installed Claude plugin cache
- current smoke-check output included `GENERATED_SKILLS: 46`

### Completed: Phase 4

Implemented:

1. `codex_interop/reconcile.py`
2. `codex_interop/state.py`
3. `codex_interop/tests/test_reconcile.py`
4. CLI reconcile wiring in `codex_interop/cli.py`

Delivered behavior:

1. desired-state construction for `CLAUDE.md`, `.codex/config.toml`, `.codex/prompts/agents/*`, and `~/.codex/skills/*`
2. project-local ownership tracking via `.codex/interop-state.json`
3. ownership-safe writes that refuse to overwrite non-generated project files or skill directories
4. atomic file replacement for project artifacts
5. staged skill-directory replacement with rollback-safe backup/rename flow
6. stale generated artifact cleanup for previously managed files and skills
7. `reconcile`, `dry-run`, and `diff` command behavior in the CLI
8. deterministic state payloads so rerunning reconcile without source changes is a no-op
9. `--codex-home` override for tests and controlled local execution

Verification:

```bash
pytest codex_interop/tests/test_discovery.py codex_interop/tests/test_agents.py codex_interop/tests/test_claude_shim.py codex_interop/tests/test_cli.py codex_interop/tests/test_skills.py codex_interop/tests/test_reconcile.py -q
python3 codex_interop/cli.py dry-run --project . --cache-dir ~/.claude/plugins/cache --codex-home /tmp/codex-interop-smoke
```

Current results:

- `27 passed`
- local dry-run succeeded against the real installed Claude plugin cache without writing to the repo or `~/.codex`
- the smoke check reported the expected generated local `.codex/*` files plus `46` generated Codex skill directories

### Completed: Phase 5

Implemented:

1. `codex_interop/install_launchagent.py`
2. `codex_interop/tests/test_launchagent.py`
3. LaunchAgent CLI wiring in `codex_interop/cli.py`

Delivered behavior:

1. deterministic macOS LaunchAgent label generation from an explicit project path
2. LaunchAgent plist rendering for scheduled `reconcile` runs with `RunAtLoad` and `StartInterval`
3. support for `--cache-dir`, `--codex-home`, Python path, CLI path, label, interval, and logs-dir overrides
4. `print-launchagent` CLI command for dry inspection of the generated plist
5. `install-launchagent` CLI command that installs the plist into a LaunchAgents directory and prints the `launchctl bootstrap` next step
6. LaunchAgent generation refuses invalid project roots that do not contain `AGENTS.md`

Verification:

```bash
pytest codex_interop/tests/test_discovery.py codex_interop/tests/test_agents.py codex_interop/tests/test_claude_shim.py codex_interop/tests/test_cli.py codex_interop/tests/test_skills.py codex_interop/tests/test_reconcile.py codex_interop/tests/test_launchagent.py -q
python3 codex_interop/cli.py print-launchagent --project . --cache-dir ~/.claude/plugins/cache --codex-home /tmp/codex-interop-smoke --logs-dir /tmp/codex-interop-logs
```

Current results:

- `31 passed`
- local `print-launchagent` smoke check succeeded against the real repo path
- the rendered plist targets `reconcile` with explicit `--project`, `--cache-dir`, and `--codex-home` arguments

## Deliverables

### Deliverable 1: Generator CLI skeleton

New file:

- `codex_interop/cli.py`

Responsibilities:

1. parse CLI arguments
2. dispatch subcommands
3. surface diagnostics and exit codes cleanly
4. resolve project root from `cwd` or `--project`

Supported subcommands in v1:

1. `reconcile`
2. `validate`
3. `dry-run`
4. `diff`

Defer:

1. `clean`
2. watcher mode

Project selection in v1:

1. default project root = current working directory
2. optional override = `--project /path/to/project`
3. target project must contain `AGENTS.md`
4. no filesystem-wide project discovery

### Deliverable 2: Discovery layer

New files:

- `codex_interop/discover.py`
- `codex_interop/model.py`

Responsibilities:

1. locate project root
2. verify `AGENTS.md` exists
3. enumerate installed Claude plugins from `~/.claude/plugins/cache/`
4. group installed plugin versions
5. semver-sort versions
6. choose latest installed version
7. resolve symlinks with `realpath`
8. build an in-memory source model

Output model should include:

1. project root
2. `AGENTS.md` path
3. selected plugin name
4. installed version
5. original installed path
6. resolved source path
7. discovered skills
8. discovered agents

### Deliverable 3: Skill translation

New file:

- `codex_interop/translate_skills.py`

Responsibilities:

1. map Claude installed skills to `~/.codex/skills/<plugin>-<skill>/`
2. copy `SKILL.md`
3. copy `scripts/`, `references/`, `assets/`
4. rewrite paths only if required by relocation
5. detect collisions
6. optionally generate `agents/openai.yaml` if needed

Output:

- desired-state representation for user-global Codex skill trees

### Deliverable 4: Agent translation

New file:

- `codex_interop/translate_agents.py`

Responsibilities:

1. parse Claude agent frontmatter/body
2. translate role names
3. translate descriptions
4. translate tools
5. preserve original model hints in diagnostics/metadata
6. emit prompt file contents for `.codex/prompts/agents/*.md`
7. emit in-memory role definitions for `.codex/config.toml`

### Deliverable 5: Codex config rendering

New file:

- `codex_interop/render_codex_config.py`

Responsibilities:

1. render `.codex/config.toml`
2. inline generated role definitions
3. reference generated prompt paths
4. keep stable ordering for deterministic diffs

Stable ordering rules:

1. sort plugins by name
2. sort skills by plugin then skill name
3. sort agent roles by role name
4. sort tool lists deterministically

### Deliverable 6: Reconcile engine

New files:

- `codex_interop/reconcile.py`
- `codex_interop/state.py`

Responsibilities:

1. build desired state
2. validate desired state
3. stage outputs in temp directories
4. atomically replace generated outputs
5. write state file
6. support dry-run and diff behavior
7. apply the safe `CLAUDE.md` generation decision tree

Generated targets in v1:

1. `<project>/CLAUDE.md`
2. `<project>/.codex/config.toml`
3. `<project>/.codex/prompts/agents/*.md`
4. `~/.codex/skills/*`

State file:

- `<project>/.codex/interop-state.json`

### Deliverable 7: Tests

New test files:

- `codex_interop/tests/test_discovery.py`
- `codex_interop/tests/test_skills.py`
- `codex_interop/tests/test_agents.py`
- `codex_interop/tests/test_reconcile.py`

Test fixture area:

- `codex_interop/tests/fixtures/`

Fixture contents:

1. fake installed Claude plugin cache layout
2. multiple version directories
3. symlinked plugin install path fixture
4. malformed version directory fixture
5. minimal `AGENTS.md`

## Implementation Phases

### Phase 1: Discovery and version resolution

Status: completed

Scope:

1. resolve project root from `cwd` or `--project`
2. validate `AGENTS.md`
3. discover installed Claude plugins
4. choose latest semantic version
5. resolve symlinked source paths
6. enumerate skills and agents

Files:

- `codex_interop/cli.py`
- `codex_interop/discover.py`
- `codex_interop/model.py`
- `codex_interop/tests/test_discovery.py`

Exit criteria:

1. project resolution works from `cwd` and `--project`
2. missing `AGENTS.md` fails clearly
3. discovery works against fake cache fixtures
4. semver ordering is deterministic
5. symlink-following behavior is verified
6. malformed version directories are ignored with clear diagnostics

Completion notes:

1. Implemented in `codex_interop/{cli,discover,model}.py`
2. Covered by `codex_interop/tests/test_discovery.py`
3. Version selection uses semantic version precedence, not filesystem metadata

### Phase 2: `CLAUDE.md` and Codex prompt/config generation

Status: completed

Scope:

1. generate `CLAUDE.md` only when safe
2. refuse to overwrite hand-authored non-shim `CLAUDE.md`
3. translate agents
4. render `.codex/prompts/agents/*.md`
5. render inline `.codex/config.toml`

Files:

- `codex_interop/translate_agents.py`
- `codex_interop/render_codex_config.py`
- `codex_interop/tests/test_agents.py`

Exit criteria:

1. generated `CLAUDE.md` is exactly `@AGENTS.md`
2. existing shim `CLAUDE.md` is preserved
3. symlinked `CLAUDE.md -> AGENTS.md` is preserved
4. hand-authored non-shim `CLAUDE.md` causes a clear non-destructive failure
5. agent prompts are deterministic
6. `.codex/config.toml` is deterministic
7. role names are collision-safe

Completion notes:

1. Implemented in `codex_interop/{claude_shim,translate_agents,render_codex_config}.py`
2. Covered by `codex_interop/tests/{test_agents,test_claude_shim}.py`
3. CLI `validate` now runs discovery plus Phase 2 translation/rendering

### Phase 3: Codex skill generation

Status: complete

Scope:

1. translate Claude installed skills into `~/.codex/skills/`
2. copy bundled resources
3. perform path rewriting where required

Files:

- `codex_interop/translate_skills.py`
- `codex_interop/tests/test_skills.py`

Exit criteria:

1. generated skills are installed into a temp fake Codex home during tests
2. copied resources remain usable
3. naming conflicts are handled deterministically
4. generated skill names match installed parent directories
5. validate passes against the real installed plugin cache

### Phase 4: Reconcile engine

Status: complete

Scope:

1. build desired state from discovery + translation layers
2. stage outputs
3. atomic replace
4. state file writing
5. dry-run and diff

Files:

- `codex_interop/reconcile.py`
- `codex_interop/state.py`
- `codex_interop/tests/test_reconcile.py`

Exit criteria:

1. reconcile is idempotent
2. failed validation does not corrupt prior outputs
3. state file accurately reflects generated outputs
4. dry-run produces no writes

### Phase 5: macOS scheduling

Status: complete

Scope:

1. add optional LaunchAgent template or helper generator
2. document installation

Files:

- `codex_interop/install_launchagent.py` or similar
- optional plist template under `assets/` or `scripts/`

Exit criteria:

1. user can install a LaunchAgent that runs reconcile at login and on interval
2. LaunchAgent does not require additional project-local hacks

Watcher mode is explicitly deferred beyond this phase.

## File Layout

Recommended implementation layout:

```text
codex_interop/
├── __init__.py
├── cli.py
├── discover.py
├── install_launchagent.py
├── model.py
├── translate_skills.py
├── translate_agents.py
├── render_codex_config.py
├── reconcile.py
├── state.py
└── tests/
    ├── test_discovery.py
    ├── test_skills.py
    ├── test_agents.py
    ├── test_launchagent.py
    ├── test_reconcile.py
    └── fixtures/
```

## Validation Rules

### Discovery validation

1. `AGENTS.md` exists
2. at least one selected plugin is installed
3. installed plugin version parses as semver
4. resolved path exists
5. project root was selected explicitly via `cwd` or `--project`

### Agent translation validation

1. frontmatter parses
2. role names are unique
3. prompt output path is unique
4. translated tools are valid or explicitly degraded

### Skill translation validation

1. `SKILL.md` exists
2. generated install directory is unique
3. copied bundled resources exist after staging

### Reconcile validation

1. all generated outputs are under generator ownership
2. no hand-authored files except `CLAUDE.md` are targeted
3. atomic replace targets are on same filesystem where needed
4. non-shim hand-authored `CLAUDE.md` is never overwritten

## CLI Behavior

### `reconcile`

Behavior:

1. discover
2. translate
3. validate
4. stage
5. replace
6. write state

Project selection:

1. use `cwd` by default
2. use `--project` when provided
3. do not scan the filesystem for other candidate projects

### `validate`

Behavior:

1. discover
2. translate
3. validate
4. report diagnostics
5. perform no writes

### `dry-run`

Behavior:

1. same as `reconcile`
2. no writes
3. summary of what would change

### `diff`

Behavior:

1. compare current generated outputs to desired state
2. show file-level changes
3. no writes

## Rollout Strategy

### Rollout 1: `prompt-engineer`

Goal:

1. prove installed-plugin discovery
2. prove skill generation
3. avoid complex hook/runtime behavior

Success criteria:

1. generated Codex skill installs cleanly
2. generated `CLAUDE.md` and `.codex/config.toml` are stable

### Rollout 2: `dex`

Goal:

1. validate `AGENTS.md`/`CLAUDE.md` compatibility assumptions
2. validate generator behavior with an existing project already aware of `@AGENTS.md`

Success criteria:

1. no Claude behavior regression
2. no ambiguity in `CLAUDE.md` shim handling

### Rollout 3: one `pirategoat-tools` reviewer agent

Goal:

1. validate agent translation
2. validate tool mapping
3. surface bootstrap/runtime gaps

Success criteria:

1. Codex role config is usable
2. degraded mappings are explicit
3. runtime assumptions are documented rather than hidden

## Risks

1. Codex child-agent config may evolve.
Mitigation: keep translation logic isolated in one module and test rendered config shape.

2. Claude cache layout may evolve.
Mitigation: isolate cache discovery in one module and fail clearly.

3. Some Claude agent prompts may rely too heavily on Claude-specific runtime assumptions.
Mitigation: mark degraded mappings explicitly and start with narrow vertical slices.

4. Skill path rewriting may subtly break bundled references.
Mitigation: fixture tests with real copied resources.

## Success Criteria

The implementation is successful when:

1. reconcile is deterministic and idempotent
2. Claude Code behavior is unchanged
3. Codex gets generated skills from actually installed Claude plugins
4. Codex gets generated inline multi-agent config in `.codex/config.toml`
5. local macOS automation can run reconcile safely via `launchd`

## Immediate Next Task

Watcher mode remains deferred.

If work continues, the next concrete step should be:

1. add a generator-owned `clean` command for generated artifacts only
2. document operational usage and rollout steps for `install-launchagent`
3. defer any filesystem watcher until the scheduled reconcile path has proven stable
