# Codex Interop Generator Spec

Date: 2026-03-03
Status: draft
Depends on: `.claude/docs/analysis/2026-03-03-codex-claude-skills-agents-interop-analysis.md`

## Goal

Define one generator/reconcile layer that projects the canonical Claude-oriented source tree into:

- a Claude Code compatibility surface
- a Codex CLI compatibility surface

without creating a second hand-maintained skill/agent ecosystem.

## Constraint

Claude Code's behavior should not be affected by the Codex interop layer.

Implications:

1. The generator must not change how Claude Code discovers or uses the canonical plugin skills/agents already present in this repo.
2. The generator must not introduce a required `.claude/agents/*.md` layer for Claude behavior in v1.
3. The only Claude-facing generated artifact allowed by default is `CLAUDE.md`, and only as a compatibility shim containing `@AGENTS.md`.
4. Any additional Claude-facing artifacts must be explicitly opt-in and justified as non-disruptive.

## Canonical Inputs

These are the only authored sources:

1. `AGENTS.md` for strictly shared project instructions only
2. `plugins/*/skills/*/SKILL.md`
3. `plugins/*/agents/*.md`
4. plugin-local `scripts/`, `references/`, `assets/`, `hooks/` where relevant
5. `.claude-plugin/marketplace.json` as plugin registry metadata

## `AGENTS.md` Scope Boundary

`AGENTS.md` is strictly shared-only.

Allowed content:

1. project conventions
2. coding standards
3. architecture rules
4. workflow expectations that are valid for both Claude Code and Codex

Disallowed content:

1. Codex-only role wiring
2. Codex-only tool/model config
3. Claude-only plugin/subagent wiring
4. runtime-specific bootstrap instructions unless genuinely shared

This is not a new constraint invented by the generator. It preserves the existing role of top-level instruction files as shared project guidance rather than runtime-specific configuration.

Implication:

- the generator must not modify `AGENTS.md`
- the generator must never rely on tool-specific control sections embedded in `AGENTS.md`
- all runtime-specific behavior must be derived from non-`AGENTS.md` canonical sources or generated into runtime-specific targets

## Generated Outputs

### In-repo outputs

1. `CLAUDE.md`
Purpose: Claude Code project instruction discovery
Shape: one-line shim containing only `@AGENTS.md`

2. `.codex/config.toml`
Purpose: Codex project config and multi-agent wiring

3. `.codex/prompts/agents/*.md`
Purpose: generated prompt payloads referenced by Codex multi-agent config

### User-global outputs

1. `~/.codex/skills/<plugin>-<skill>/`
Purpose: Codex skill discovery/install surface

## Non-Goals

1. Do not create hand-authored Codex-specific skills.
2. Do not make `.codex/` the canonical instruction root.
3. Do not rely on project-local Codex skill discovery.
4. Do not attempt a 1:1 Claude slash-command runtime in Codex.
5. Do not translate every Claude hook literally; re-express behavior at the reconcile/runtime layer.

## Generator Responsibilities

The generator owns all of:

1. source discovery
2. path translation
3. format translation
4. generated file ownership
5. atomic replacement
6. state tracking
7. validation

No other script should independently copy/symlink/transform these interoperability artifacts.

## Output Ownership Rules

### Hand-authored

Never overwritten:

- `AGENTS.md`
- `plugins/**`
- existing Claude runtime surfaces unless explicitly designated as generated

### Generated

Owned entirely by the generator:

- `CLAUDE.md`
- `.codex/config.toml`
- `.codex/prompts/agents/*.md`
- `~/.codex/skills/<plugin>-<skill>/`

### Generated file marker

All generated text files should include a short top-level marker where format permits:

```text
GENERATED FILE - DO NOT EDIT
Source: <canonical path or generator id>
```

For `CLAUDE.md`, the content should remain exactly:

```md
@AGENTS.md
```

so no extra marker should be added there.

## Source Discovery

### Installed-plugin mode

This is the only supported source mode for v1.

Inputs:

- current project `AGENTS.md`
- `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/...`

Use case:

- generate Codex-facing outputs from the Claude plugins actually installed on the local machine

Implications:

1. Codex interop is driven by real Claude-installed plugin state, not by an arbitrary repo checkout.
2. If a plugin is not installed in Claude Code, it is out of scope for generation.
3. The generator must resolve the active installed version for each selected plugin before translating skills/agents.
4. If the installed Claude plugin path resolves through a symlink into a working repo, the generator should follow that symlink and use the resolved repo content as the canonical input for that installed plugin.

### Source resolution rule

For each selected installed Claude plugin:

1. start from the installed plugin path under `~/.claude/plugins/cache/...`
2. resolve symlinks with `realpath`
3. if the resolved path points into a working repo checkout, use the resolved repo path as the source for that installed plugin
4. otherwise use the installed plugin artifact path directly

This preserves the rule that generation is driven by what Claude Code has installed, while still supporting local development setups where Claude is configured against a symlinked repo.

## Translation Rules

### Rule A: `AGENTS.md` -> `CLAUDE.md`

`CLAUDE.md` generation must be safe and non-destructive.

Decision tree:

1. If `CLAUDE.md` does not exist:
   generate:

   ```md
   @AGENTS.md
   ```

2. If `CLAUDE.md` is exactly `@AGENTS.md`:
   leave it unchanged or normalize whitespace only.

3. If `CLAUDE.md` is a symlink to `AGENTS.md`:
   leave it unchanged.

4. If `CLAUDE.md` is an existing generated shim owned by the generator:
   the generator may replace it.

5. If `CLAUDE.md` exists and contains anything else:
   fail with a clear diagnostic and do not overwrite it.

Only the shim content is valid for generated creation:

```md
@AGENTS.md
```

No other content may be generated.

Important:

- `CLAUDE.md` only forwards Claude Code into the shared `AGENTS.md` content
- it must not be used to smuggle Claude-specific instructions that are absent from `AGENTS.md`

### Rule B: Claude skill -> Codex skill

Input:

- `plugins/<plugin>/skills/<skill>/`

Output:

- `~/.codex/skills/<plugin>-<skill>/`

Mapping:

1. copy `SKILL.md`
2. copy `scripts/`, `references/`, `assets/` if present
3. copy `agents/` if present
4. skill `name:` in the generated `SKILL.md` must match the generated install directory name, per the Agent Skills spec
5. generate `agents/openai.yaml` only if Codex UI metadata is needed and not already available in a reusable source form
6. rewrite relative paths only when required by the new install location

Notes:

- the official Agent Skills spec treats `scripts/`, `references/`, and `assets/` as optional skill directories; `agents/` is also valid when present
- generated Codex skills should copy only the official skill-layout files plus any explicitly vendored relocation dependencies, not arbitrary development byproducts like `.venv/`

Naming:

- install directory: `<plugin>-<skill>`
- skill `name:` inside `SKILL.md` remains canonical unless a conflict forces prefixing

Conflict rule:

- if two installed skills would collide by directory name or effective skill identity, prefix with plugin name

### Rule C: Claude agent -> Codex child-agent role

Input:

- `plugins/<plugin>/agents/<agent>.md`

Outputs:

- `.codex/prompts/agents/<plugin>-<agent>.md`
- `.codex/config.toml` entries under `[agents.<role>]`

Mapping:

1. frontmatter `name` -> role name
2. frontmatter `description` -> role description
3. body -> prompt file body
4. frontmatter `tools` -> translated Codex tool allowlist
5. frontmatter `model` -> preferred model hint if compatible, otherwise generator default

Role naming:

- default role name: `<plugin>_<agent_name_normalized>`

This avoids collisions across plugins.

### Rule D: Claude commands

Do not translate commands directly into a fake Codex command system.

Allowed outputs:

1. Codex skill instructions
2. AGENTS.md routing documentation
3. helper scripts

### Rule E: Claude hooks

Do not port hooks literally.

Instead map hook behavior into:

1. reconcile-time generation
2. explicit bootstrap scripts
3. MCP wiring
4. documented runtime conventions

Example:

- Claude plugin-root discovery via hook should become an explicit generated bootstrap/runtime contract for Codex-compatible agent flows.

## Tool Translation Table

This is an initial translation layer, not guaranteed lossless.

### Claude -> Codex

- `Read` -> Codex file-read capability
- `Glob` -> Codex file listing / glob capability
- `Grep` -> Codex search capability
- `Write` -> Codex file-write capability
- `Bash` -> Codex shell/exec capability
- `WebSearch` -> Codex web search if enabled; otherwise omit and mark degraded

If a Claude tool has no Codex equivalent:

1. omit from generated allowlist
2. record a warning in generator diagnostics
3. mark the generated role as degraded if behavior materially depends on it

## Model Translation Rules

Claude agent frontmatter may specify models like `sonnet`.

Codex generation should not try to preserve provider-specific names literally.

Instead:

1. keep the original value in generator metadata/diagnostics
2. map to a configured Codex default role model
3. allow per-agent overrides in generator config if needed

Default recommendation:

- use one global Codex role model unless specific agents prove they need differentiated settings

## Path Strategy

### Canonical paths

Always preserve canonical path references in generator metadata for traceability.

### Generated prompt paths

Generated Codex prompt paths should live under:

```text
.codex/prompts/agents/
```

This keeps generated role prompts close to generated Codex config.

### Generated skill paths

Codex skills should be materialized only under:

```text
~/.codex/skills/
```

No project-local Codex skill mirrors.

## Reconcile Algorithm

### Step 1: discover sources

1. resolve project root from `cwd` or explicit `--project`
2. validate that the project root contains `AGENTS.md`
3. enumerate installed Claude plugins from `~/.claude/plugins/cache/`
4. resolve selected plugin/version pairs
5. resolve each selected plugin path through symlinks
6. enumerate skills and agents from the resolved source path for each selected plugin
7. load optional project exclusions from `.codex/bridge.toml`
8. apply CLI exclusions (`--exclude-plugin`, `--exclude-skill`, `--exclude-agent`) if provided
9. filter the discovered plugin/skill/agent set before translation

### Step 2: build desired state

Construct an in-memory desired-state model:

- generated `CLAUDE.md`
- generated `.codex/config.toml`
- generated `.codex/prompts/agents/*`
- generated `~/.codex/skills/*`

### Step 3: validate desired state

Checks:

1. no role-name collisions
2. no skill-name collisions
3. every generated path resolves cleanly
4. referenced scripts/assets exist
5. translated tool names are valid or explicitly degraded

### Step 4: stage outputs

Write to temporary staging locations on the same filesystem as the final targets.

### Step 5: atomic replace

Replace generated outputs via rename where possible.

### Step 6: record state

State file recommendations:

- project-local: `.codex/interop-state.json`
- optional user-global: `~/.codex/interop-state.json`

Suggested contents:

- generator version
- source repo path
- source hash summary
- selected plugins
- generated target paths
- warnings/degraded mappings

State file rule:

- keep the state payload deterministic so rerunning reconcile without source changes stays a no-op
- do not include volatile timestamps in the written state file

## Failure Policy

1. If generation fails, keep last known good outputs.
2. Never partially overwrite a generated tree if validation failed.
3. Surface degraded mappings clearly.
4. Provide `--dry-run` and `--diff`.
5. Provide `--clean` only for generated artifacts owned by the generator.

## macOS Runtime Modes

### Mode 1: manual

Run reconcile explicitly.

Best for:

- initial development
- debugging translation logic

### Mode 2: scheduled

Use a user `launchd` LaunchAgent:

- `RunAtLoad`
- periodic `StartInterval`

Best for:

- low-complexity automation
- eventual consistency

Project roots for scheduled runs should be explicitly configured.

Do not auto-discover projects by crawling the filesystem in v1.

Recommended v1 implementation:

- render a deterministic plist that runs `python3 codex_interop/cli.py reconcile --project <project>`
- support optional `--cache-dir`, `--codex-home`, log-dir, and interval overrides
- install the plist into `~/Library/LaunchAgents/`
- do not auto-run `launchctl` in the generator; print the next-step command instead

### Mode 3: watcher

Use a long-lived watcher process under `launchd`.

Watch roots:

- `~/.claude/plugins/cache/`

Must:

- debounce changes
- trigger full reconcile

## Suggested Generator CLI

Primary command:

```bash
python3 codex_interop/cli.py reconcile
```

Suggested subcommands:

```bash
python3 codex_interop/cli.py reconcile
python3 codex_interop/cli.py dry-run
python3 codex_interop/cli.py diff
python3 codex_interop/cli.py validate
python3 codex_interop/cli.py print-launchagent
python3 codex_interop/cli.py install-launchagent
python3 codex_interop/cli.py clean
```

Project selection:

1. default project root = current working directory
2. optional override:

```bash
python3 codex_interop/cli.py reconcile --project /path/to/project
```

Validation:

1. target project must contain `AGENTS.md`
2. target project should resolve to a recognizable project root
3. no filesystem-wide auto-discovery of candidate projects in v1

## Suggested Internal Modules

If implemented in Python:

1. `discover.py`
2. `model.py`
3. `translate_skills.py`
4. `translate_agents.py`
5. `render_codex_config.py`
6. `reconcile.py`
7. `state.py`
8. `install_launchagent.py`

## Testing Strategy

### Unit tests

1. frontmatter parsing
2. path rewriting
3. tool translation
4. role naming collision handling
5. `CLAUDE.md` shim generation

### Fixture tests

Use fixtures derived from real installed Claude plugin artifacts:

1. `prompt-engineer`
2. `dex`
3. one `pirategoat-tools` reviewer agent

### Golden-output tests

Validate generated:

1. `.codex/config.toml`
2. `.codex/prompts/agents/*`
3. installed skill trees under a temp fake `~/.codex/skills`

### Contract tests

1. every generated artifact can be traced back to a canonical source
2. no generated artifact requires hand-editing
3. rerunning reconcile without source changes is a no-op

## Proposed Vertical Slice

### Slice 1

Plugin: `prompt-engineer`

Why:

1. one skill
2. small surface area
3. strong overlap with Codex skill model
4. low hook/runtime complexity

### Slice 2

Plugin: `dex`

Why:

1. already models `CLAUDE.md -> AGENTS.md` indirection
2. exercises instruction-surface compatibility logic

### Slice 3

Plugin: one `pirategoat-tools` agent

Suggested candidate:

- `architecture-reviewer`

Why:

1. exercises agent translation
2. exercises tool mapping
3. surfaces plugin-root/bootstrap assumptions early

## Decided Policies

1. Generated `.codex/config.toml` is local-only and should not be committed.
2. The first implementation should emit Codex role config directly into `.codex/config.toml`.
3. V1 should only support actually installed Claude Code plugins as generator inputs.
4. If an installed Claude plugin resolves via symlink into a repo checkout, the generator should follow the symlink and use the resolved repo content.
5. If multiple installed versions of the same Claude plugin exist on disk, the generator should always select the latest installed plugin version.

For v1, "latest" should be determined by semantic version ordering from the installed plugin version directory names, not by filesystem modification time.

Version selection algorithm:

1. collect all installed version directory names for the plugin
2. parse them as semantic versions
3. sort by semantic version precedence
4. select the highest version

Failure handling:

1. ignore directories that do not parse as valid semantic versions
2. if no valid semantic versions remain, fail with a clear diagnostic rather than guessing from mtime or lexicographic order

## Default Recommendation

Until decided otherwise:

1. treat generated compatibility artifacts as generated local outputs
2. generate Codex roles into `.codex/config.toml` plus prompt files under `.codex/prompts/agents/`
3. read capabilities from installed Claude plugin artifacts, following symlinks into repo checkouts when present
4. add installed-plugin-cache watching only after the generator logic is stable
5. do not generate `.claude/agents/*.md` in v1
