# Codex CLI and Claude Code Skills/Agents Interop Analysis

Date: 2026-03-03
Status: in progress
Scope: Compare OpenAI Codex CLI skills/agent mechanics with Claude Code plugins, skills, and agents; identify viable ways to make Claude Code plugin-provided capabilities usable from Codex CLI.

## Working Thesis

Claude Code plugins and Codex skills overlap most strongly at the "skill" layer, not the "agent" layer. For project instructions, `AGENTS.md` is the better canonical format because it has broader cross-tool standardization and direct Codex support.

Given the current project constraint, the source of truth should remain on the Claude Code side.

The shortest path is likely:

1. Treat Claude plugin skills as the canonical source format and generate Codex skills from them.
2. Treat `AGENTS.md` as the canonical project instruction file.
3. Generate `CLAUDE.md` as a Claude Code compatibility shim.
4. Treat Claude plugin agents as one of:
   - generated Codex child-agent role definitions
   - generated Claude Code `.claude/agents/*.md` compatibility artifacts if needed
   - plain instruction assets invoked by a Codex skill or prompt template where role generation is lossy
   - external tools exposed through MCP where the Claude agent really fronts procedural functionality
5. Treat Claude slash commands as wrappers that need explicit remapping for Codex, not direct reuse.

## Non-Negotiable Constraint

Do not maintain two hand-authored skill/agent ecosystems.

The canonical authored artifacts should be:

- `AGENTS.md`
- Claude plugin `skills/*/SKILL.md`
- Claude plugin `agents/*.md`
- Claude plugin scripts/references/assets/hooks as needed

Codex-facing artifacts should be generated from those sources:

- Codex skills
- Codex child-agent config
- Codex prompt files or bridge wrappers
- optional repo-local `AGENTS.md` fragments beyond the canonical root file
- optional MCP registrations/config templates

Claude-facing compatibility artifacts should also be generated where needed:

- `CLAUDE.md`
- `.claude/agents/*.md`

## Sources

OpenAI:
- https://developers.openai.com/codex/skills
- https://developers.openai.com/codex/guides/agents-md
- https://developers.openai.com/codex/cli
- https://developers.openai.com/codex/cli/config
- https://developers.openai.com/codex/cli/config-reference
- https://developers.openai.com/codex/multi-agent

Anthropic:
- https://code.claude.com/docs/en/plugins-reference
- https://docs.anthropic.com/en/docs/claude-code/memory
- https://docs.anthropic.com/en/docs/claude-code/sub-agents
- https://docs.anthropic.com/en/docs/claude-code/settings

Open standards / ecosystem:
- https://agentskills.io/
- https://agentskills.io/specification
- https://agents.md/
- https://dotagentsprotocol.com/

Local repo and local Codex installation:
- `.claude-plugin/marketplace.json`
- `CLAUDE.md`
- `.claude/settings.local.json`
- `plugins/*`
- `~/.codex/config.toml`
- `~/.codex/skills`
- `~/.codex/vendor_imports/skills`
- `codex-cli 0.106.0` local help output

## What Codex CLI Actually Uses

### 1. Global user configuration

Codex CLI loads defaults from `~/.codex/config.toml`. The local machine currently uses:

```toml
model = "gpt-5.3-codex"
model_reasoning_effort = "high"
personality = "pragmatic"

[projects."/Users/vladolaru/Work/a8c/claude-code-plugins"]
trust_level = "trusted"
```

From the official Codex config docs:
- user config lives at `~/.codex/config.toml`
- project config can also live in `.codex/config.toml` at repo level
- project config is intended for checked-in, repo-specific defaults
- config supports `profiles.<name>` for reusable execution presets

This is the closest Codex analogue to Claude Code's repo-local settings and plugin activation context.

Important nuance:

- `.codex/` is an officially documented Codex project configuration directory
- it is the right place for Codex-specific generated config such as `.codex/config.toml`
- it is not the primary documented home for project instructions
- it is not the primary documented install/discovery location for skills

### 2. Project instructions via `AGENTS.md`

Official Codex docs define `AGENTS.md` as the primary instruction file. Important mechanics:

- Global scope: Codex reads `~/.codex/AGENTS.md` or `~/.codex/AGENTS.override.md`.
- Project scope: Codex walks from project root down to the current directory.
- In each directory it checks `AGENTS.override.md`, then `AGENTS.md`, then configured fallback names.
- The docs explicitly show `project_doc_fallback_filenames = ["TEAM_GUIDE.md", ".agents.md"]`.

This is materially different from Claude plugin manifests. Codex's first-class customization surface is instruction-file discovery, not plugin installation metadata.

Important clarification from the official docs:

- Codex documents `AGENTS.md` and `AGENTS.override.md` as first-class instruction files.
- Codex also supports alternate instruction filenames such as `.agents.md` via config.
- I did not find official Codex documentation for a first-class `.agents/` directory format.
- I also did not find official documentation that makes `.codex/` the home for instruction documents. The documented instruction surface is `AGENTS.md`-based, not `.codex/`-based.

For interoperability planning, this means `AGENTS.md` should be preferred over `CLAUDE.md` as the canonical authored project-instructions file whenever both ecosystems must be served.

### 3. Global skills directory

Local installation and official docs align on this:

- Codex has a first-class global skills directory under `~/.codex/skills/`.
- Bundled system skills live under `~/.codex/skills/.system/`.
- Additional skills can be installed and become available after restart.
- Skills are folder-based and centered on a required `SKILL.md`.

The local vendored OpenAI skills README describes skills as reusable folders of instructions, scripts, and resources. This matches the official skills docs closely.

Important nuance:

- `~/.codex/skills/` is the documented Codex skill surface
- `.codex/` inside a project is not the primary documented skill install/discovery location
- if project-local generated skill mirrors are used, they should be treated as staging or generator outputs unless verified as a native Codex discovery path

### 3b. Skill exposure at runtime

Observed local session artifacts add an important runtime detail:

- Codex session JSONL stores a turn context containing the available skill list, each with name, description, and file path.
- The current turn's injected instructions explicitly describe skill triggering and progressive disclosure behavior.

Implication:
- the install location matters, because Codex appears to precompute and inject skill availability into the turn context
- a bridge should assume "make the skill discoverable to Codex before session start", not "drop files mid-session and expect immediate pickup"

### 4. Skill structure

Observed and documented Codex skill structure:

```text
skill-name/
├── SKILL.md
├── agents/            # optional UI metadata
│   └── openai.yaml
├── scripts/           # optional
├── references/        # optional
└── assets/            # optional
```

The important split is:
- `SKILL.md` frontmatter drives discoverability
- `SKILL.md` body loads only when the skill triggers
- bundled resources are progressive-disclosure material

The `agents/openai.yaml` file in Codex skills is not an execution agent definition. It is UI metadata for display chips/lists.

### 5. Agent support in Codex today

Codex CLI 0.106.0 exposes:
- `codex exec`
- `codex review`
- `codex mcp`
- feature flags including `multi_agent` and `child_agents_md`

Official multi-agent docs describe role-based child agent configuration in config, including:
- `agents.<role>.model`
- `agents.<role>.prompt`
- `agents.<role>.tools`
- `agents.<role>.description`

Current evidence suggests:
- multi-agent support exists, but is still feature-gated/experimental in the installed CLI
- this is a different mechanism from Claude plugin `agents/*.md`
- Codex child-agent configuration currently appears config-driven, not marketplace/plugin-driven
- the official docs show agent roles in TOML under `[agents]` with per-role `config_file` paths like `agents/explorer.toml`

This is the main interop mismatch.

However, if the goal is to stay on the frontier, this mismatch should be treated as a generation target, not a reason to avoid the feature. In other words:

- authored source stays in Claude plugin `agents/*.md`
- generated output includes Codex `agents.<role>` config entries and prompt material
- instability risk is managed in the generator layer, not by introducing a second hand-maintained agent corpus

### 6. MCP in Codex

Codex has first-class MCP management via:

- `codex mcp list`
- `codex mcp get`
- `codex mcp add`

Local CLI help confirms MCP servers can be added either:
- as stdio commands
- or as streamable HTTP endpoints

That makes MCP the most stable tool-integration bridge primitive on the Codex side.

## What Claude Code Plugins Actually Use

### 1. Plugin as packaging primitive

The Anthropic plugin reference describes a plugin as a directory with a `plugin.json` manifest and optional content directories such as:

- `commands/`
- `skills/`
- `agents/`
- `hooks/`
- `mcp/`
- `settings.json`

The repo uses marketplace packaging via `.claude-plugin/marketplace.json`, where each plugin entry points at a plugin source directory and lists relative `skills`, `commands`, and `agents`.

So for this repo specifically, there are two packaging layers:
- Anthropic plugin format at the plugin directory level
- this repo's marketplace registry at `.claude-plugin/marketplace.json`

### 2. Plugin install scope

Anthropic docs state plugins can be installed:
- locally
- globally
- from GitHub

This matters for interop because Claude already has both user-level and project-level plugin distribution models, while Codex skills are currently a global install primitive plus project instructions/config.

Observed local Claude artifacts add two useful implementation details:

- installed plugins are cached under paths like `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/...`
- plugin skill execution references that cached base directory directly

Implication:
- Claude plugins are distributed as versioned install artifacts, not consumed from the source repo in-place during normal use
- a Codex bridge should likely produce an installed or generated artifact set, not rely on reading a Claude plugin source tree ad hoc during every session

### 3. Claude skills

Claude plugin skills are folder-based and centered on `SKILL.md`, which is structurally very close to Codex skills.

Local examples show:
- YAML frontmatter with at least `name` and `description`
- markdown body with workflow instructions
- optional references/scripts/assets under the skill directory

This is the strongest compatibility point between the two ecosystems.

### 3b. Claude project instructions

Anthropic's official Claude Code docs still center project/user memory and instructions on `CLAUDE.md`.

I did not find official Claude Code documentation that treats `AGENTS.md` as a first-class instruction file.

Practical implication:

- Claude Code should currently be treated as requiring `CLAUDE.md` for native project-instruction discovery
- if the project standardizes on `AGENTS.md`, then `CLAUDE.md` should exist as a generated compatibility shim

### 4. Claude agents

Claude plugin agents in this repo are markdown files with YAML frontmatter such as:

```yaml
name: architecture-reviewer
description: Software architecture code review...
model: sonnet
color: blue
tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
  - WebSearch
```

Then the file body contains the agent prompt and operating protocol.

This format is much closer to a complete subagent prompt spec than Codex skill UI metadata, and only partially overlaps with Codex child-agent config.

Anthropic's official subagent docs center on:

- project-local `.claude/agents/`
- user-global `~/.claude/agents/`

So for the agent-role layer, Claude Code still has its own native storage/discovery format that is separate from `AGENTS.md`.

### 5. Claude commands and hooks

Claude plugins also package:
- slash commands in `commands/*.md`
- hooks via `hooks.json` and helper scripts

Codex CLI does not have a directly equivalent plugin command layer in the same packaging style. Codex has top-level CLI commands, skills, AGENTS.md instructions, config, and MCP integrations.

One repo-specific detail matters here:

- `pirategoat-tools` uses a Claude hook to write `CLAUDE_PLUGIN_ROOT` / plugin-root state into `/tmp` so dispatched agents can find plugin assets from inside arbitrary target repositories

Implication:
- some Claude plugin agents assume Claude's hook/runtime machinery
- those agents are not directly portable to Codex without replacing that discovery mechanism

## Direct Comparison

### Strong matches

1. Skill folder concept
- Codex: `SKILL.md` + optional scripts/references/assets
- Claude: `SKILL.md` + optional scripts/references/assets
- Result: mostly portable

2. User-global capability installation
- Codex: `~/.codex/skills/`
- Claude: globally installed plugins
- Result: portable as a distribution workflow, though not via same manifest

3. Repo-specific instruction overlay
- Codex: `AGENTS.md` and `.codex/config.toml`
- Claude: `CLAUDE.md`, local settings, plugin activation context
- Result: conceptually similar, but Claude does not natively adopt the `AGENTS.md` standard yet

### Partial matches

1. Agents
- Codex: config-defined child-agent roles, experimental/still stabilizing
- Claude: markdown-defined agents distributed inside plugins
- Result: translatable in principle, not directly compatible

2. Tool integrations
- Codex: MCP servers configured via `codex mcp` or config
- Claude: plugins may include `mcp/`, hooks, and tool-facing instructions
- Result: common denominator is MCP, not plugin manifests

### Weak or no direct match

1. Plugin manifest/marketplace
- Claude: native plugin manifest + install/update lifecycle
- Codex: no evidence of a comparable plugin marketplace primitive in current CLI docs

2. Slash commands
- Claude: first-class command markdown files
- Codex: no first-class slash-command packaging analogue

3. Hooks
- Claude: explicit hook support inside plugins
- Codex: no equivalent hook packaging surfaced in current CLI docs/help

4. `.agents/` directory protocol
- Emerging ecosystem idea: yes
- Official Codex primitive today: no documented first-class `.agents/` directory support
- Closest Codex equivalent: `AGENTS.md` / `.agents.md` for instructions, `.codex/config.toml` for roles/config, and arbitrary supporting files referenced from config

## Preliminary Interop Options

### Option A. Skill-only bridge

Idea:
- Convert Claude plugin `skills/*` directories into installable Codex skills under `~/.codex/skills/`.

Why it fits:
- same central artifact name: `SKILL.md`
- similar resource folder model
- Codex already supports installed custom skills

Expected work:
- translate or normalize frontmatter if needed
- optionally generate `agents/openai.yaml` for Codex skill UI metadata
- preserve scripts/references/assets with minimal changes

Limitations:
- only covers Claude skills, not commands/agents/hooks

Current assessment:
- lowest-risk path
- should work for many existing plugin skills with modest translation

### Option B. Skill plus prompt-template bridge for agents

Idea:
- treat each Claude agent markdown file as a reusable prompt asset
- wrap it in a Codex skill that tells Codex when to load that agent prompt and how to execute its workflow

Example shape:
- one Codex skill per Claude agent, or
- one Codex "plugin bridge" skill that routes to agent prompt files as references

Why it fits:
- Claude agents are already markdown instructions
- Codex skills are good at progressive disclosure and routing

Limitations:
- loses explicit subagent identity and some Claude-specific tool declarations
- tool lists must be translated into Codex expectations manually
- no native equivalent to Claude slash-command invocation

Current assessment:
- practical for many agent prompts
- good intermediate step before deeper child-agent integration

### Option C. Translate Claude agents into Codex child-agent roles

Idea:
- compile Claude `agents/*.md` into Codex `agents.<role>` config blocks and corresponding prompt files

Why it is attractive:
- preserves explicit specialist-agent semantics
- aligns with Codex multi-agent direction

Why it is risky:
- local CLI marks `multi_agent` as experimental and `child_agents_md` as under development
- Codex child-agent configuration appears config-centric rather than plugin-centric
- unclear whether project-local generated config is ergonomic enough for plugin-style distribution

Current assessment:
- required if the project wants frontier Codex behavior without forking the authored agent corpus
- should be included now, but as generated output with explicit compatibility expectations

### Option D. MCP-centered bridge

Idea:
- expose Claude plugin functionality through MCP servers and consume those servers from Codex via `codex mcp`

Best for:
- tool-like capabilities
- deterministic scripts
- external service integration

Not best for:
- pure instruction assets such as prompt-heavy skills

Current assessment:
- strongest common denominator for tools
- complementary to skill translation, not a replacement

## Concrete Bridge Architecture Candidates

### Candidate 1. One-way installer from Claude plugin skills to Codex skills

Mechanics:
- read Claude plugin registry or plugin source directory
- for each selected `skills/<name>/` folder, copy or sync it into a Codex-visible skill root
- if missing, generate Codex UI metadata file `agents/openai.yaml`
- keep original `SKILL.md`, `scripts/`, `references/`, and `assets/` mostly intact

Best install targets:
- user-global: `~/.codex/skills/<plugin>-<skill>/`

Why this is attractive:
- lowest semantic loss
- easy to reason about updates
- likely enough for a large portion of plugin value

Main drawback:
- does not carry over slash commands or specialist agent dispatch as first-class concepts

### Candidate 2. Claude-agent-as-Codex-skill wrapper

Mechanics:
- keep Claude agent markdown files as reference assets
- generate a Codex skill whose `SKILL.md` tells Codex:
  - when to use that agent role
  - which agent prompt file to read
  - which scripts/resources to load
  - what Codex-native tool assumptions replace Claude `tools:` metadata

Why this is attractive:
- minimal transformation of the actual agent prompt
- good fit for reviewer/expert roles that mostly depend on prompt discipline and repo scripts

Main drawback:
- agent identity becomes advisory rather than native multi-agent configuration

### Candidate 3. Hybrid generated project files

Mechanics:
- install translated skills globally
- generate repo-local `.codex/config.toml` for project-specific defaults
- generate repo-local `AGENTS.md` sections describing where translated capabilities live and how to invoke them

Why this is attractive:
- aligns with Codex's actual control plane: skills + config + `AGENTS.md`
- gives a place to document naming conventions and routing logic for translated Claude capabilities

Main drawback:
- generated files may drift unless the bridge owns the update flow cleanly

### Candidate 4. Tool surface via MCP, instructions via skills

Mechanics:
- procedural capabilities become MCP servers
- knowledge/workflow capabilities remain skills
- translated skills tell Codex when to use which MCP tool

Why this is attractive:
- clean separation of concerns
- mirrors how both systems already distinguish between instructions and tool execution

Main drawback:
- still requires separate handling for pure Claude agent prompts and slash commands

## Early Recommendation

The most viable phased approach is:

### Phase 1

Build a generator/installer that imports Claude plugin `skills/` into Codex skills, with Claude plugin skills remaining canonical.

### Phase 2

Generate Codex child-agent config from Claude `agents/*.md`, plus fallback bridge skills/reference bundles where translation is imperfect.

### Phase 3

Harden the generator around Codex multi-agent changes as the CLI evolves, but keep the authored source unchanged on the Claude side.

### Phase 4

Route tool-heavy plugin capabilities through MCP where the functionality is procedural rather than prompt-only.

## Recommended First Implementation Shape

If the goal is practical interoperability rather than theoretical completeness, the first implementation should probably do exactly three things:

1. Import selected Claude plugin skills into `~/.codex/skills/` as generated artifacts.
2. Generate Codex multi-agent config from selected Claude agents, even if this path is marked experimental.
3. Emit a short project `AGENTS.md` fragment documenting the translated capability names and intended routing.

That would cover the most reusable parts of this repo:

- `prompt-engineer` skill
- `dex` knowledge-capture skill
- selected `pirategoat-tools` skills
- selected `pirategoat-tools` reviewer agents via generated Codex child-agent roles

This keeps Claude plugin content canonical while still betting on Codex's emerging multi-agent model.

## Recommended Architecture Under This Constraint

### Canonical source tree

Keep authoring only here:

```text
AGENTS.md
plugins/<plugin>/
├── skills/
├── agents/
├── commands/
├── scripts/
├── hooks/
└── references/assets as needed
```

### Generated Codex layer

Generate a Codex-specific projection from that source tree, for example:

```text
.codex/
├── generated/
│   ├── skills/
│   │   └── <plugin>-<skill>/
│   ├── prompts/
│   │   └── agents/
│   │       └── <plugin>-<agent>.md
│   └── config.toml
└── AGENTS.md
```

Generate Claude compatibility artifacts alongside that:

```text
CLAUDE.md                  # generated shim pointing to AGENTS.md semantics
.claude/
└── agents/
    └── *.md              # generated if agent compatibility layer is needed
```

Or generate directly into user-global install locations where needed:

- `~/.codex/skills/...`

The important rule is that these are build artifacts, not hand-edited source.

### Generation rules

#### Claude skill -> Codex skill

Map directly:
- `SKILL.md` frontmatter and body remain primary source
- `scripts/`, `references/`, `assets/` are copied with path rewriting only if required
- generate `agents/openai.yaml` if Codex wants UI metadata not present in Claude skill source

#### Claude agent -> Codex child-agent role

Map approximately:
- Claude frontmatter `name` -> Codex role name
- Claude frontmatter `description` -> Codex role description
- Claude body -> Codex role prompt file
- Claude `tools:` -> Codex child-agent `tools` allowlist, via translation table
- Claude model hint -> Codex role model when compatible, otherwise a configured default

Where translation is lossy, preserve the original Claude frontmatter/body in generated prompt comments or metadata so the generator is reversible and debuggable.

#### Claude command -> Codex invocation pattern

Do not try to force a 1:1 slash-command abstraction.

Instead, commands should generate one of:
- documented Codex prompt entrypoints in `AGENTS.md`
- thin helper scripts
- Codex skills whose instructions explain how to invoke the workflow

#### Claude hooks -> Codex bootstrap/runtime support

Claude hook-dependent behaviors should be re-expressed as:
- generated bootstrap scripts
- MCP setup
- repo-local `AGENTS.md` instructions
- Codex config conventions

The key example in this repo is plugin-root discovery for reviewer agents. That should become an explicit generated bootstrap contract rather than an implicit Claude hook assumption.

#### Canonical `AGENTS.md` -> Claude `CLAUDE.md`

Because Claude Code does not currently document native `AGENTS.md` support, generate a minimal `CLAUDE.md` compatibility file.

Preferred shape:

- minimal shim
- no duplicated instructions
- exact content:

```md
@AGENTS.md
```

Operational rule:

- author `AGENTS.md`
- never hand-edit generated `CLAUDE.md`

## Frontier Stance

If the project explicitly wants frontier behavior, the recommendation should be:

1. Generate Codex child-agent config now.
2. Treat breakage from Codex experimental changes as generator maintenance, not authoring maintenance.
3. Keep all authored behavioral logic in Claude plugin skills/agents.
4. Accept that some generated Codex artifacts may be version-sensitive and require compatibility shims.

This is still materially better than maintaining parallel hand-written Codex and Claude ecosystems.

## Interoperability Standards Check

### What seems standard today

There are two distinct open-standard stories here:

1. **Skills**: the Agent Skills standard
- canonical artifact: `SKILL.md`
- supported by both Claude-oriented tooling and Codex
- this is the strongest real interoperability layer for skills

2. **Project instructions**: `AGENTS.md`
- `AGENTS.md` is now presented as an open cross-tool format at `agents.md`
- Codex officially supports `AGENTS.md`
- Codex also supports fallback names such as `.agents.md` when configured

This is the strongest cross-tool direction for project instructions, even though Claude Code has not yet documented first-class native support for it.

### What does not appear to be Codex-standard today

The newer `.agents/` directory protocol is real and explicitly pitches itself as an open standard, but based on the current official Codex docs I found:

- Codex does **not** document `.agents/` as a native discovery/configuration root
- Codex multi-agent config is documented through `.codex/config.toml` plus referenced agent TOML files
- Codex instruction discovery is documented through `AGENTS.md` / `AGENTS.override.md` plus configurable fallback filenames

So the answer is:

- **AGENTS.md**: yes, this is part of the current interoperability story
- **`.agents.md`**: supported indirectly in Codex via fallback filename configuration
- **`.agents/` directory**: not currently documented by Codex as a first-class standard surface

### Design implication

If the project wants maximum present-day interoperability with Codex, the best default is:

1. Keep portable project guidance in `AGENTS.md`.
2. Generate `CLAUDE.md` for Claude Code compatibility.
3. Keep portable skills in Agent Skills format (`SKILL.md`).
4. Generate Codex-specific multi-agent TOML config from Claude agent sources.
5. Generate Claude-native `.claude/agents/` artifacts if Claude subagent compatibility is needed.
6. Treat `.agents/` as optional future-facing packaging, not the current primary target for Codex.

## Updated Recommendation

The canonical authored surfaces should be:

1. `AGENTS.md` for strictly shared project instructions only
2. Claude plugin `skills/*/SKILL.md` for reusable skills
3. Claude plugin `agents/*.md` as the canonical specialist-role source, if this repo wants to keep that authoring model

Generated compatibility surfaces should be:

1. `CLAUDE.md` for Claude Code project instruction discovery, implemented as a one-line shim containing only `@AGENTS.md`
2. `.claude/agents/*.md` for Claude subagent discovery if needed
3. `.codex/config.toml` plus referenced agent config/prompt files for Codex multi-agent
4. `~/.codex/skills/*`

This preserves single-source authoring while aligning the top-level instruction layer with the broader `AGENTS.md` standard.

## `.codex/` Nuance

To keep the roles of each path clean:

- `AGENTS.md` is the canonical project instruction surface
- `.codex/config.toml` is the Codex project configuration surface
- `.codex/` is a reasonable place for Codex-specific generated config and prompt artifacts
- `~/.codex/skills/` is the documented Codex skill installation/discovery surface

So the intended split is:

1. Use `AGENTS.md` for shared project instructions.
2. Use `.codex/` for Codex-specific generated config.
3. Do not treat `.codex/` as a replacement for `AGENTS.md`.
4. Do not use project-local `.codex/` as a Codex skill-discovery target.

## `AGENTS.md` Scope Boundary

`AGENTS.md` should remain strictly shared-only.

That means it should contain:

1. project conventions
2. architecture constraints
3. coding standards
4. workflow rules that are valid regardless of assistant runtime

It should not contain:

1. Codex-only multi-agent wiring
2. Codex-only tool/model configuration
3. Claude-only plugin or subagent wiring
4. runtime-specific bootstrap mechanics unless they are truly shared and harmless to both systems

This is not a new pattern being introduced by the interop work. It matches the intended role of these top-level instruction files already: shared project guidance, not runtime-specific wiring.

The generator should not touch `AGENTS.md`.

Operational split:

1. shared rules live in `AGENTS.md`
2. Claude-specific behavior lives in Claude-native plugin/agent/runtime surfaces
3. Codex-specific behavior lives in `.codex/config.toml`, generated Codex prompts, and `~/.codex/skills/`

## macOS Practical Operating Models

This section assumes a developer Mac with both Claude Code and Codex CLI installed, using the paths observed on this machine:

- Claude user home: `~/.claude/`
- Claude installed plugin cache: `~/.claude/plugins/cache/`
- Claude project agents: `<project>/.claude/agents/`
- Codex user home: `~/.codex/`
- Codex global skills: `~/.codex/skills/`
- Codex global config: `~/.codex/config.toml`
- Codex project config: `<project>/.codex/config.toml`

### Generated artifact targets on macOS

For a canonical project using `AGENTS.md`, the generated outputs should likely be:

```text
<project>/
├── AGENTS.md
├── CLAUDE.md                # generated shim: @AGENTS.md
├── .claude/
│   └── agents/             # generated Claude-compatible agent files if needed
└── .codex/
    ├── config.toml         # generated Codex multi-agent config
    ├── prompts/
    │   └── agents/         # generated Codex prompt files
```

Plus optional user-global targets:

```text
~/.codex/skills/<generated-skill>/
```

### What should be symlinked vs copied vs generated

#### Good symlink candidates

1. `CLAUDE.md -> AGENTS.md`
- If Claude reliably follows symlinks in your environment, this is the cleanest option.
- If not, the safer option is the explicit one-line shim file with `@AGENTS.md`.

2. Large reference or asset directories that are immutable enough
- Example: generated Codex skill references that can point back to canonical plugin resources.
- Use only when the consumer tolerates symlinks and path resolution is predictable.

#### Better as generated copies

1. Codex skills under `~/.codex/skills/`
- Codex appears to enumerate available skills before session start.
- A copied/generated physical skill directory is safer than a fragile chain of symlinks into Claude cache paths.

2. `.codex/config.toml`
- This should be treated as generated config, not a symlink into Claude plugin data.

3. `.claude/agents/*.md`
- Claude expects these in a native location.
- Generate concrete files so Claude discovery stays boring and predictable.

#### Better as generated transforms

1. Claude `agents/*.md` -> Codex role config
- requires translation, not copying

2. Claude plugin commands -> Codex invocation docs/skills/scripts
- requires remapping, not copying

### Operating model A: Explicit sync command

Simplest operational model:

- maintain a generator command such as `scripts/sync-codex-interop.py`
- run it manually after plugin changes or install/update events

Flow:

1. Read canonical sources from repo or Claude plugin cache.
2. Regenerate `CLAUDE.md`, `.claude/agents/`, `.codex/config.toml`, and Codex skills.
3. Replace generated outputs atomically.

Pros:
- easiest to reason about
- lowest background complexity
- simplest debugging

Cons:
- requires discipline or tooling hooks
- drift can occur if developers forget to run it

### Operating model B: Git-hook or task-runner sync

Slightly more automated:

- run generator from `just`, `make`, `npm scripts`, or a repo script
- optionally wire it into pre-commit, post-merge, or a dedicated bootstrap command

Good for:
- repos where the canonical source is the checked-out repository itself

Less good for:
- environments where Claude plugins are updated independently under `~/.claude/plugins/cache/`

### Operating model C: `launchd` login agent with periodic reconcile

macOS-native background option:

- install a user LaunchAgent in `~/Library/LaunchAgents/`
- run a reconcile script at login and then on an interval

Example shape:

```text
~/Library/LaunchAgents/com.example.cc-codex-interop.plist
```

The job runs something like:

```bash
/usr/bin/python3 /path/to/sync-codex-interop.py --reconcile
```

Recommended behavior:

- `RunAtLoad = true`
- periodic fallback using `StartInterval`
- script does full reconciliation and exits quickly

Why this works well on macOS:

- no always-hot daemon required
- leverages native user-session process management
- periodic reconciliation is robust against missed file events

Tradeoff:

- not instant
- shortest safe interval should still avoid wasteful churn

### Operating model D: `launchd` + filesystem watcher process

More responsive macOS-native option:

- LaunchAgent starts a long-lived watcher process
- watcher monitors Claude and project paths
- watcher debounces changes and runs reconciliation

Candidate watch roots:

- `~/.claude/plugins/cache/`
- `~/.claude/plugins/marketplaces/`
- canonical source repo paths if developing plugins from source
- target project roots that need generated `CLAUDE.md`, `.claude/agents/`, or `.codex/`

Implementation choices:

1. Native FSEvents via Python `watchdog`
2. `watchman` if you are willing to install a dependency
3. `fswatch` if you want a lightweight CLI-based watcher

Recommended pattern:

- do not regenerate on every single event
- debounce for a short window
- then run one full reconcile pass

This matters because plugin installs/updates often touch many files rapidly.

### Operating model E: Hybrid watcher + periodic reconcile

Most robust option for a personal macOS workstation:

1. watcher for fast local response
2. periodic reconcile for eventual consistency

This covers:

- missed FSEvents
- partial plugin installs
- stale symlink/copy state
- manual file edits outside the watcher's assumptions

### Why not rely only on `launchd` `WatchPaths`

macOS `launchd` supports path-based triggers, but they are a weak fit for this problem by themselves:

- path watches are coarse
- recursive directory tree changes are awkward
- plugin installs often involve many nested file mutations

They can still be useful for a small number of top-level files, but for plugin trees and generated capability graphs, a dedicated watcher or periodic reconcile is more reliable.

### Where to watch in practice

There are two realistic source-of-truth locations.

#### Source mode: watch the checked-out plugin repo

Best when you are developing the canonical sources in this repository.

Watch:

- `<repo>/AGENTS.md`
- `<repo>/plugins/**/skills/**`
- `<repo>/plugins/**/agents/**`
- `<repo>/.claude-plugin/marketplace.json` if generation depends on registry membership

Best for:

- local plugin development
- deterministic generator behavior

#### Installed mode: watch Claude's installed plugin cache

Best when Claude plugin installation/update is the trigger you care about.

Watch:

- `~/.claude/plugins/cache/`

Important nuance:

- Claude cache paths are versioned
- a plugin update may create a new version directory rather than mutate the old one

That is actually helpful:

- the reconcile script can detect the latest installed version and generate against that
- stale generated outputs can be replaced cleanly when the version changes

### Recommended reconcile algorithm

Whether run manually or from a background process, the reconcile logic should be idempotent:

1. Discover canonical sources.
2. Resolve active plugin/version or source path.
3. Parse canonical `AGENTS.md`, plugin skills, and plugin agents.
4. Generate outputs into a temporary staging directory.
5. Validate generated outputs.
6. Atomically replace target files/directories.
7. Record a small state file with source version/path/hash.

Suggested state file locations:

- project-local: `.codex/interop-state.json`
- user-global: `~/.codex/interop-state.json`

### Atomic update strategy on macOS

For reliability:

- write generated files into a temp directory under the same filesystem
- use rename-based replacement where possible
- avoid editing generated files in place

Why:

- Claude or Codex may read these files while the sync is happening
- APFS rename is atomic within the same volume

### Failure model and safety rules

The sync process should follow these rules:

1. Never overwrite hand-authored canonical files.
2. Only manage files clearly marked as generated.
3. If generation fails, keep the last known good generated output.
4. Log failures to a predictable local file.
5. Make dry-run and diff modes available.

Generated-file marking can be simple:

```md
<!-- GENERATED FILE: do not edit -->
```

or a top-line comment in TOML/Markdown where appropriate.

### Recommended macOS implementation path

For a practical first version on macOS, the best sequence is probably:

1. Build an idempotent sync/reconcile script first.
2. Use manual invocation until the mapping is correct.
3. Add a user LaunchAgent with `RunAtLoad` plus periodic reconcile.
4. Add an optional long-lived watcher mode once the generator is stable.

This order matters because background automation amplifies generator mistakes.

### Candidate tooling stack

Minimal dependency stack:

- Python 3 standard library for generation/reconcile
- `launchd` LaunchAgent for scheduling

Higher-convenience stack:

- Python + `watchdog` for FSEvents-backed watching
- `launchd` to keep the watcher alive in the user session

Power-user stack:

- `watchman` for reliable tree watching
- reconcile script remains the source of truth

### Concrete recommendation

For this project on macOS:

1. Canonical source stays in the repo.
2. `CLAUDE.md` is generated as `@AGENTS.md`.
3. Generated Claude subagents live in `.claude/agents/` only if needed for Claude-native dispatch.
4. Generated Codex config lives in `.codex/config.toml`.
5. Generated Codex skills are materialized under `~/.codex/skills/`.
6. Start with explicit reconcile plus a LaunchAgent periodic sync.
7. Add a watcher for `~/.claude/plugins/cache/` only if plugin-install-driven automation proves valuable.

## Recommended Practical Plan

This is the preferred implementation sequence:

### Phase 1: Canonical source

1. Keep `AGENTS.md` as the canonical project instruction file.
2. Keep Claude plugin `skills/*` and `agents/*.md` as the canonical capability source.
3. Generate `CLAUDE.md` as a one-line shim containing only `@AGENTS.md`.

### Phase 2: Single reconcile layer

Build one idempotent reconcile script that:

1. reads canonical sources
2. generates `CLAUDE.md`
3. generates `.claude/agents/` only when needed
4. generates `.codex/config.toml` and Codex prompt/config artifacts
5. materializes Codex skills into `~/.codex/skills/`
6. replaces generated outputs atomically
7. records a small state file for debugging and incremental checks

The reconcile script should be the only place that knows about copying, symlinking, path rewriting, and format translation.

### Phase 3: macOS automation

Install a user `launchd` LaunchAgent that:

1. runs at login
2. periodically runs the reconcile script
3. exits after each pass

This is the recommended default automation model on macOS.

### Phase 4: Optional reactive sync

Only after the reconcile logic is stable:

1. add a watcher mode
2. watch the source repo and/or `~/.claude/plugins/cache/`
3. debounce file bursts
4. trigger reconcile automatically

### Why this plan

It keeps:

- one authored source of truth
- Codex frontier features enabled
- Claude compatibility explicit
- macOS automation simple at first
- operational complexity concentrated in one reconcile layer

## Open Questions To Verify

1. Whether Codex custom skills can be loaded from a repo-local path without copying into `~/.codex/skills/`, or whether install/copy is required.
2. How much Claude `tools:` metadata can be mapped safely onto Codex child-agent `tools`.
3. Whether Claude plugin `agents/*.md` can be translated into Codex child-agent prompts plus config with acceptable fidelity for real workflows.
4. Whether Claude plugin `commands/*.md` are worth bridging, or whether they should simply become documented Codex prompt entrypoints.
5. Whether a project-level generated `.codex/config.toml` is a sane target for checked-in agent-role definitions.

## Notes From Local Repo Relevant To Design

1. `dex` already understands both `CLAUDE.md` and `AGENTS.md` and explicitly models a migration path from `.claude/` to `.ai/`. That suggests this repo is already thinking in cross-agent-tool terms.
2. `pirategoat-tools/agents/codex-reviewer.md` already treats Codex CLI as an external reviewer orchestrated from Claude Code, which is the reverse of the bridge being explored here.
3. `.claude/settings.local.json` contains explicit permission entries for Claude `Skill(...)` usage, reinforcing that Claude skill dispatch is integrated into its permission/runtime system in a way Codex currently is not.

## Interim Conclusion

There is no clean "install Claude Code plugin into Codex CLI" path in the current documented Codex model.

There is, however, a credible interoperability path if the problem is reframed as:

- generated package translation for skills from canonical Claude sources
- generated Codex multi-agent config and prompt-role translation for agents from canonical Claude sources
- MCP as the shared execution substrate for tools

That reframing keeps the bridge aligned with primitives both systems already expose.
