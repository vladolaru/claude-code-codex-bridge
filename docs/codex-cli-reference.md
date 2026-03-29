# Codex CLI Reference

This document is the bridge project's authoritative reference for how the OpenAI Codex CLI discovers and uses skills, instructions, agent roles, and configuration. It captures behaviors the bridge must target when generating Codex-compatible artifacts.

Sources: [Codex developer docs](https://developers.openai.com/codex/), [Codex CLI source](https://github.com/openai/codex) (`codex-rs/core/`).

---

## 1. Instructions Discovery (`AGENTS.md`)

### 1.1 Global Instructions

`~/.codex/AGENTS.md` is the user-global instructions file, equivalent to Claude Code's `~/.claude/CLAUDE.md`. Codex reads it automatically at session start.

Override: `~/.codex/AGENTS.override.md` takes precedence when present and non-empty. Only one file is used per directory level (first non-empty match wins).

The home directory path is configurable via the `CODEX_HOME` environment variable (defaults to `~/.codex`).

### 1.2 Instruction Chain

Codex builds an instruction chain once per session, concatenating files from root to leaf with `"\n\n"` separators:

1. **Global level**: `$CODEX_HOME/AGENTS.override.md` OR `$CODEX_HOME/AGENTS.md`
2. **Project root** (detected via `project_root_markers`, default `.git`): `AGENTS.override.md` OR `AGENTS.md`
3. **Each directory from root to cwd**: `AGENTS.override.md` OR `AGENTS.md` at each level

At each directory level, the candidate filename search order is:
1. `AGENTS.override.md`
2. `AGENTS.md`
3. Any filenames from `project_doc_fallback_filenames` config (e.g., `TEAM_GUIDE.md`, `.agents.md`)

Only the first match per directory is used. Files closer to the working directory appear later in the chain.

### 1.3 Constraints

- Empty files are skipped
- Total size capped at `project_doc_max_bytes` (default 32 KiB, configurable up to 65536+ bytes)
- At most one file per directory level
- Walking stops at the current directory level

### 1.4 Source Code Confirmation

From `codex-rs/core/src/project_doc.rs`:
- `DEFAULT_PROJECT_DOC_FILENAME = "AGENTS.md"`
- `LOCAL_PROJECT_DOC_FILENAME = "AGENTS.override.md"`

## 2. Skill Discovery

### 2.1 Discovery Hierarchy

Codex discovers skills from four scope levels (from `codex-rs/core/src/skills/loader.rs`):

| Priority | Scope | Path | Description |
|----------|-------|------|-------------|
| 1 (highest) | `Repo` | `<project>/.codex/skills/` | Client-specific project skills |
| 1 | `Repo` | `<cwd-to-root>/.agents/skills/` | Cross-client project skills (walked from cwd up to project root) |
| 2 | `User` | `~/.codex/skills/` | User-level skills (Codex-specific) |
| 2 | `User` | `~/.agents/skills/` | Cross-client user-level skills |
| 3 | `System` | `~/.codex/skills/.system/` | Bundled system skills from OpenAI (cached locally) |
| 4 (lowest) | `Admin` | `/etc/codex/skills/` | System-wide admin-deployed skills |

Sort order: `Repo(0) > User(1) > System(2) > Admin(3)`.

### 2.2 Scope Behavior

- Both `.codex/skills/` and `.agents/skills/` at the same scope level produce equivalent `Repo` scope — they coexist, not replace each other
- Two skills with the same `name` from different scopes are both retained (not merged or deduplicated)
- Same-path dedup: if the same directory appears under multiple scopes, first-encountered scope wins
- Repo skills stay within the git repo boundary
- Non-git repos: `.codex/skills/` at cwd is still scanned, but parent walking does NOT occur

### 2.3 Scanning Constraints

- Max scan depth: 6 levels from a skills root
- Max directories per root: 2,000
- Hidden directories (starting with `.`) are skipped
- Symlinked directories are followed for Repo, User, and Admin scopes (but not System)

### 2.4 Skill Format

Each skill is a directory containing `SKILL.md` with YAML frontmatter for `name`/`description`, plus optional subdirectories: `scripts/`, `references/`, `assets/`, `agents/`.

The format follows the open [Agent Skills standard](agent-skills-standard.md).

### 2.5 Skill Activation

Skills activate through two mechanisms:
1. **Explicit invocation**: Users reference skills via `/skills` command or `$skill-name` mention
2. **Implicit invocation**: Codex automatically selects skills matching task descriptions

### 2.6 Codex-Specific Skill Metadata (`agents/openai.yaml`)

Codex supports an optional `agents/openai.yaml` file inside skill directories for UI configuration:

```yaml
interface:
  display_name: "User-facing name"
  short_description: "Brief description"
  icon_small: "./assets/small-logo.svg"
  icon_large: "./assets/large-logo.png"
  brand_color: "#3B82F6"
  default_prompt: "Surrounding prompt"

policy:
  allow_implicit_invocation: false  # requires explicit $skill invocation

dependencies:
  tools:
    - type: "mcp"
      value: "identifier"
```

This is a Codex extension, not part of the open Agent Skills standard.

### 2.7 Skill Configuration

Skills can be disabled in `~/.codex/config.toml`:

```toml
[[skills.config]]
path = "/path/to/skill/SKILL.md"
enabled = false
```

## 3. Agent Roles

### 3.1 Overview

Codex supports user-defined agent roles that configure how spawned sub-agents behave. Roles are declared in `config.toml` and optionally backed by separate `.toml` config files.

### 3.2 Role Declaration in `config.toml`

Roles are declared under the `[agents]` section using a flattened map. Each role key becomes the role name:

```toml
[agents.researcher]
description = "Research-focused role."
config_file = "./agents/researcher.toml"
nickname_candidates = ["Herodotus", "Ibn Battuta"]
```

#### AgentRoleToml Fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `description` | Yes* | string | Human-facing role documentation used in spawn tool guidance. Required unless supplied by the referenced role file. |
| `config_file` | No | path | Path to a TOML config layer for the role. Relative paths resolve from the `config.toml` that declares the role. |
| `nickname_candidates` | No | string[] | Display nicknames for spawned agents. ASCII alphanumeric, spaces, hyphens, underscores only. No duplicates. |

\* Description is required for the role to load. It can come from either the inline declaration or the referenced `config_file`.

The `[agents]` section also supports threading controls:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_threads` | number | 6 | Maximum concurrent agent threads |
| `max_depth` | number | 1 | Maximum nesting depth (root = 0) |
| `job_max_runtime_seconds` | number | 1800 | Per-worker timeout for `spawn_agents_on_csv` jobs |

### 3.3 Role Config Files (`.toml`)

When `config_file` is specified, it points to a TOML file that can contain:

```toml
name = "role-name"                    # Role name (optional if declared in config.toml)
description = "What this role does"   # Role description
nickname_candidates = ["Name1"]       # Optional display names
developer_instructions = "..."        # Instructions injected as a separate message

# Plus any ConfigToml overrides:
model = "o3"
model_reasoning_effort = "high"
sandbox_mode = "workspace-write"
# ... any other config.toml keys
```

The role file acts as a high-precedence config layer — it can override model, sandbox, approval, and other session settings for agents spawned with that role.

### 3.4 Role File Discovery

In addition to explicit `config_file` declarations, Codex discovers role files from `.codex/agents/` directories:

- All `.toml` files under `.codex/agents/` (recursive) are loaded as agent role definitions
- Each must contain `name` and `developer_instructions`
- Files already referenced via `config_file` are skipped (no double-load)
- Duplicate role names within the same config layer produce warnings

### 3.5 Built-in Roles

Codex ships with built-in roles (`default`, `explorer`, `awaiter`) embedded in the binary. User-defined roles with the same name override built-ins.

### 3.6 Role Application

When a sub-agent is spawned with a role, the role's config file is loaded as a high-precedence config layer on top of the parent session's config. The parent's model provider and profile are preserved unless the role explicitly overrides them.

## 4. Configuration

### 4.1 Config Layer Precedence

Configuration resolves in this order (highest precedence first):

1. CLI flags and `--config` overrides
2. Profile values (via `--profile <name>`)
3. Project config (`.codex/config.toml`, closest to cwd wins; trusted projects only)
4. User config (`~/.codex/config.toml`)
5. System config (`/etc/codex/config.toml` on Unix)
6. Built-in defaults

Untrusted projects skip project-scoped `.codex/` layers entirely.

### 4.2 `~/.codex/` Directory Structure

| File/Directory | Purpose |
|----------------|---------|
| `config.toml` | User-level configuration |
| `AGENTS.md` | Global user instructions |
| `AGENTS.override.md` | Temporary global override |
| `skills/` | User-level skills |
| `skills/.system/` | Bundled system skills |
| `sessions/` | Session transcripts |
| `auth.json` | Credential storage |

### 4.3 Key Config Settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `model` | string | — | Model selection override |
| `model_provider` | string | `"openai"` | Provider ID |
| `model_reasoning_effort` | string | — | `"minimal"` to `"xhigh"` |
| `sandbox_mode` | string | `"read-only"` | `"read-only"`, `"workspace-write"`, `"danger-full-access"` |
| `approval_policy` | string | — | `"untrusted"`, `"on-request"`, `"never"` |
| `project_doc_max_bytes` | number | 32768 | Max bytes from AGENTS.md chain |
| `project_doc_fallback_filenames` | string[] | `[]` | Additional instruction filenames |
| `developer_instructions` | string | — | Extra instructions injected before AGENTS.md |

### 4.4 Tools Configuration

The `[tools]` section in `config.toml` controls optional tool capabilities:

```toml
[tools]
web_search = true        # or a WebSearchToolConfig table
view_image = true
```

Note: core tools (file read, file write, file edit, shell execution, glob, grep) are always available based on sandbox policy. The `[tools]` section only controls optional/experimental tools like `web_search` and `view_image`.

## 5. Comparison: Claude Code vs Codex

| Aspect | Claude Code | Codex CLI |
|--------|-------------|-----------|
| Global instructions file | `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` |
| Override mechanism | None (single file) | `AGENTS.override.md` takes precedence |
| Home directory env var | Not configurable | `CODEX_HOME` |
| Per-directory instructions | `CLAUDE.md` at each level | `AGENTS.md` at each level |
| Fallback filenames | None | Configurable via `project_doc_fallback_filenames` |
| Instructions size limit | Not documented | `project_doc_max_bytes` (32 KiB default) |
| User-level skills | `~/.claude/skills/` | `~/.codex/skills/` and `~/.agents/skills/` |
| Project-level skills | `.claude/skills/` | `.codex/skills/` and `.agents/skills/` |
| Plugin/marketplace skills | `~/.claude/plugins/cache/` | No equivalent (skills are standalone directories) |
| Config file | `~/.claude/settings.json` | `~/.codex/config.toml` |
| Agent definitions | Frontmatter `.md` files under `agents/` | `.toml` role files with config overrides |
| Agent tool grants | Per-agent `tools:` list in frontmatter | Inherited from session config (sandbox policy) |
| Trust model | `.claude/settings.local.json` | `projects.<path>.trust_level` in config |

## 6. Bridge Design Implications

### 6.1 Verified Mappings

| Claude Code Source | Codex Target | Rationale |
|--------------------|--------------|-----------|
| `~/.claude/CLAUDE.md` | `~/.codex/AGENTS.md` | Direct equivalent, both user-global instructions |
| `~/.claude/skills/<name>/` | `~/.codex/skills/<name>/` | User-level skill, global scope (bare name; collision suffixed if needed) |
| `.claude/skills/<name>/` | `.codex/skills/<name>/` | Project-level skill, repo scope |
| Plugin skills | `~/.codex/skills/<name>/` | User-level skill, global scope (bare name; collision suffixed if needed) |

### 6.2 Key Architectural Facts

1. `.codex/skills/` is natively discovered by Codex with `SkillScope::Repo` — the bridge doesn't need to register project skills anywhere
2. `~/.codex/AGENTS.md` is loaded automatically — the bridge just writes the file
3. Project-level `.codex/skills/` provides natural scope isolation — no prefix needed
4. User-level `~/.codex/skills/` is a shared global namespace — collisions resolved with `-alt` / `-alt-N` suffixes
5. Codex agent roles use `.toml` config files with `developer_instructions`, not `.md` prompt files with `model`/`tools` arrays — agent translation now produces native `.toml` files placed in `.codex/agents/` (project-local) or `~/.codex/agents/` (global), matching Codex's auto-discovery format (see section 3.4). This eliminates the need for `config.toml` agent entries; Codex discovers these files automatically.
6. Codex tools are controlled by sandbox policy at the session level, not per-agent tool grants — a role file can override `sandbox_mode` but doesn't list individual tools

## 7. MCP Server Configuration

Codex has first-class MCP (Model Context Protocol) support. MCP servers are configured in `config.toml` and managed via CLI commands.

### 7.1 Config Locations

MCP servers live in `config.toml` at the standard scope levels:

| Scope | Path | Notes |
|-------|------|-------|
| **User (global)** | `~/.codex/config.toml` | Default; overridable via `CODEX_HOME` |
| **Project** | `.codex/config.toml` | Only loaded for trusted projects |
| **System** | `/etc/codex/config.toml` | Unix system-wide |

Standard config layer precedence applies. Untrusted projects skip `.codex/` layers entirely.

MCP servers can also come from **plugins** (plugin-sourced servers are lower priority than `config.toml`) and the built-in **Codex Apps** connector server.

### 7.2 Config Format

Each server is a `[mcp_servers.<name>]` table. Transport type is inferred from whether `command` or `url` is present (mutually exclusive).

#### STDIO Transport

```toml
[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
cwd = "/path/to/working/dir"       # optional
enabled = true                       # default: true
required = false                     # default: false
startup_timeout_sec = 10.0           # default: 10
tool_timeout_sec = 60.0              # default: 120

[mcp_servers.context7.env]
MY_API_KEY = "value"

# Alternative: forward existing env vars by name (not set them)
# env_vars = ["EXISTING_SECRET"]
```

STDIO fields: `command` (required), `args`, `env` (map), `env_vars` (array of names to forward), `cwd`.

**Important**: STDIO servers start with `env_clear()` — they do NOT inherit the parent environment. Only platform defaults (`HOME`, `PATH`, `SHELL`, `USER`, `LANG`, `TMPDIR`, `TZ` on Unix) plus configured `env_vars` and `env` are available.

#### Streamable HTTP Transport

```toml
[mcp_servers.figma]
url = "https://mcp.figma.com/mcp"
bearer_token_env_var = "FIGMA_TOKEN"  # optional
enabled = true
required = true
scopes = ["repo"]                      # OAuth scopes
oauth_resource = "https://example.com/" # RFC 8707

[mcp_servers.figma.http_headers]
"X-Region" = "us-east-1"

[mcp_servers.figma.env_http_headers]
"X-Auth" = "AUTH_ENV_VAR_NAME"         # header value from env var
```

HTTP fields: `url` (required), `bearer_token_env_var`, `http_headers` (map), `env_http_headers` (map of header→env var name), `scopes`, `oauth_resource`.

#### Shared Fields (Both Transports)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | `true` | Set `false` to disable without removing |
| `required` | boolean | `false` | `codex exec` fails if required server can't init |
| `startup_timeout_sec` | number | `10` | Seconds to wait for init |
| `tool_timeout_sec` | number | `120` | Per-tool call timeout |
| `enabled_tools` | string[] | — | Allowlist of tool names |
| `disabled_tools` | string[] | — | Denylist (applied after allowlist) |
| `scopes` | string[] | — | OAuth scopes |
| `oauth_resource` | string | — | RFC 8707 resource parameter |

#### Per-Tool Approval

```toml
[mcp_servers.docs.tools.search]
approval_mode = "approve"   # "auto" | "prompt" | "approve"
```

#### Global OAuth Settings

```toml
mcp_oauth_callback_port = 5555
mcp_oauth_callback_url = "https://devbox.example.internal/callback"
mcp_oauth_credentials_store = "auto"  # "auto" | "file" | "keyring"
```

### 7.3 Tool Namespacing

MCP tools use the same `mcp__<server>__<tool>` double-underscore convention as Claude Code:

```
mcp__context7__search
mcp__github__list_issues
```

- Names sanitized to `[a-zA-Z0-9_-]` (disallowed chars replaced with `_`)
- Max 64 characters (truncated with SHA1 suffix if exceeded)
- Duplicate qualified names: second tool is skipped with warning

### 7.4 Server Lifecycle

1. All enabled servers start **concurrently** at session start (default 10s timeout)
2. Status events: `Starting` → `Ready` | `Failed`
3. `required = true` servers cause `codex exec` to fail on init failure
4. Servers can be refreshed mid-session (e.g., when skill MCP deps are auto-installed)
5. Cancellation token shuts down all connections on session end

### 7.5 MCP in Skills

Skills declare MCP dependencies in `agents/openai.yaml`:

```yaml
dependencies:
  tools:
    - type: "mcp"
      value: "serverName"
      description: "Human description"
      transport: "streamable_http"   # or "stdio"
      url: "https://example.com/mcp" # for streamable_http
      # command: "npx"               # for stdio
```

Codex auto-installs missing MCP deps when a skill is invoked (controlled by `features.skill_mcp_dependency_install`, default `true`). Installs go to global `~/.codex/config.toml`.

Agent role files (`.toml`) do NOT declare MCP dependencies — they inherit the session's MCP config.

### 7.6 MCP Resources and Prompts

| Capability | Supported | Notes |
|------------|-----------|-------|
| **Tools** | Yes | Full support with namespacing, filtering, approval |
| **Resources** | Yes | List, list templates, read — with pagination and server attribution |
| **Prompts** | No | No `prompts/list` or `prompts/get` implementation |
| **Elicitation** | Yes | Server-to-client requests (form-based and URL-based) |

### 7.7 CLI Commands

```bash
codex mcp list [--json]
codex mcp get <name> [--json]
codex mcp add <name> --env KEY=VALUE -- <command> [args...]  # stdio
codex mcp add <name> --url <URL> [--bearer-token-env-var <ENV>]  # http
codex mcp remove <name>
codex mcp login <name> [--scopes scope1,scope2]
codex mcp logout <name>
/mcp                                  # in-session status/management
```

### 7.8 Rust Type Reference

Core types from `codex-rs/core/src/config/types.rs`:

```rust
// Transport — inferred from field presence (command vs url)
enum McpServerTransportConfig {
    Stdio { command, args, env, env_vars, cwd },
    StreamableHttp { url, bearer_token_env_var, http_headers, env_http_headers },
}

// Server config
struct McpServerConfig {
    transport: McpServerTransportConfig,
    enabled: bool,          // default true
    required: bool,         // default false
    startup_timeout_sec: Option<Duration>,
    tool_timeout_sec: Option<Duration>,
    enabled_tools: Option<Vec<String>>,
    disabled_tools: Option<Vec<String>>,
    scopes: Option<Vec<String>>,
    oauth_resource: Option<String>,
    tools: HashMap<String, McpServerToolConfig>,  // per-tool approval
}
```

## Sources

- [Custom instructions with AGENTS.md](https://developers.openai.com/codex/guides/agents-md/)
- [Agent Skills](https://developers.openai.com/codex/skills/)
- [Config reference](https://developers.openai.com/codex/config-reference/)
- [Config (basic)](https://developers.openai.com/codex/config-basic/)
- [Config (advanced)](https://developers.openai.com/codex/config-advanced/)
- [Prompting Codex](https://developers.openai.com/codex/prompting/)
- [Model Context Protocol - Codex](https://developers.openai.com/codex/mcp)
- [Sample Configuration](https://developers.openai.com/codex/config-sample)
- [GitHub: openai/codex](https://github.com/openai/codex) — source: `codex-rs/core/src/skills/loader.rs`, `codex-rs/core/src/project_doc.rs`, `codex-rs/core/src/config/mod.rs`, `codex-rs/core/src/config/agent_roles.rs`, `codex-rs/core/src/agent/role.rs`, `codex-rs/core/src/config/types.rs`, `codex-rs/core/src/mcp/mod.rs`, `codex-rs/core/src/mcp_connection_manager.rs`, `codex-rs/core/src/mcp/skill_dependencies.rs`, `codex-rs/rmcp-client/src/rmcp_client.rs`
