# MCP Bridge Mapping: Claude Code → Codex

This document is the canonical reference for how Claude Code MCP configurations map to Codex MCP configurations, what the bridge translates automatically, what gaps exist, and what the runtime implications are. It informs bridge design decisions for MCP support.

Last verified against [Codex CLI source](https://github.com/openai/codex): 2026-04-02.

---

## 1. Configuration Format Mapping

### 1.1 stdio Servers

| CC Field (JSON) | Codex Field (TOML) | Bridge Status | Notes |
|-----------------|-------------------|---------------|-------|
| `command` | `command` | **Mapped** | Direct 1:1 |
| `args` | `args` | **Mapped** | Direct 1:1. Non-list values ignored. |
| `env` | `env` | **Mapped** (partial) | Static string→string values map directly. `${VAR}` values are handled via `env_vars` or the stdio launcher — see §3. |
| — | `env_vars` | **Synthesized** | No direct CC field. The bridge generates `env_vars` from `${VAR}` patterns in CC `env` values. See §3. |
| — | `cwd` | Not mapped | Codex-only: working directory for server process. No CC equivalent. |

**Bridgeable**: Core fields map directly. `env_vars` is synthesized from `${VAR}` patterns — see §3.

### 1.2 HTTP Servers (Streamable HTTP)

| CC Field (JSON) | Codex Field (TOML) | Bridge Status | Notes |
|-----------------|-------------------|---------------|-------|
| `url` | `url` | **Mapped** (with diagnostics) | Preserved literally. `${VAR}` refs cannot be expanded by Codex, so the bridge warns when they appear. |
| `type: "http"` | (inferred from `url`) | **Stripped** | Codex infers transport from field presence; no `type` field exists |
| `headers` | `http_headers` | **Mapped** (partial) | Renamed. Static literal values map. Whole-value `${VAR}` refs are routed to `env_http_headers` — see §3.4. |
| `headers.Authorization` with `Bearer ${VAR}` | `bearer_token_env_var` | **Mapped** | Env var name extracted; header removed from `http_headers` |
| `headers.Authorization` with literal Bearer | — | **Omitted** | Literal credential dropped; diagnostic warning emitted |
| `headersHelper` | — | **Diagnostic** | CC-only: shell command for dynamic headers. No Codex equivalent. Warning emitted. |
| — | `bearer_token` | Not used | Codex accepts a literal bearer token but we correctly use `bearer_token_env_var` instead |
| — | `env_http_headers` | **Synthesized** | No direct CC field. The bridge generates `env_http_headers` from whole-value `${VAR}` patterns in CC `headers`. See §3.4. |
| `oauth.*` | `scopes` + `oauth_resource` | **Diagnostic** | Different OAuth models. Warning emitted directing user to `codex mcp login`. |

**Bridgeable**: URL and static headers map. URL templates are warned on because Codex cannot expand them. Bearer token extraction works. `${VAR}` header values map to `env_http_headers`. OAuth requires manual `codex mcp login`.

### 1.3 SSE Servers

| CC | Codex | Notes |
|----|-------|-------|
| `type: "sse"` supported | Not supported | Codex only supports stdio and streamable HTTP |

**Not bridgeable.** SSE servers are skipped during discovery. SSE is deprecated in CC too — most servers support HTTP.

---

## 2. Shared Configuration Fields

### 2.1 Fields the Bridge Does NOT Generate

These Codex-only fields have no CC equivalent. The bridge does not generate them, but they are valid in `config.toml` and users can add them manually alongside bridge-owned entries.

| Codex Field | Type | Default | Description |
|-------------|------|---------|-------------|
| `enabled` | `bool` | `true` | Skip server initialization when `false` |
| `required` | `bool` | `false` | `codex exec` exits if required server fails to start |
| `startup_timeout_sec` | `f64` | `30.0`* | Startup + initial tool list timeout (seconds, fractional OK) |
| `startup_timeout_ms` | `u64` | — | Alternative to `_sec` in milliseconds; `_sec` takes precedence if both set |
| `tool_timeout_sec` | `f64` | `120.0`* | Per-tool-call timeout (seconds, fractional OK) |
| `enabled_tools` | `string[]` | — | Allowlist of tool names exposed from this server |
| `disabled_tools` | `string[]` | — | Denylist applied after `enabled_tools` |
| `scopes` | `string[]` | — | OAuth scopes for `codex mcp login` |
| `oauth_resource` | `string` | — | OAuth resource parameter (RFC 8707) |
| `name` | `string` | — | Legacy display-name field; accepted but ignored |
| `tools.<name>.approval_mode` | `string` | `auto` | Per-tool approval (`auto`/`prompt`/`approve`) |

\* Defaults are from Codex CLI source (`mcp_types.rs`). The [Codex public docs](https://developers.openai.com/codex) list `10` and `60` respectively — this may reflect a source update after our last verification or a docs-vs-source divergence. Reverify against current source before relying on specific values.

### 2.2 Transport Validation

Codex validates that stdio and HTTP fields are mutually exclusive:
- stdio fields (`args`, `env`, `env_vars`, `cwd`) rejected when `url` is present
- HTTP fields (`url`, `bearer_token_env_var`, `http_headers`, `env_http_headers`, `oauth_resource`) rejected when `command` is present
- Neither `command` nor `url` → error

The bridge inherits this implicitly by routing to `_translate_stdio()` or `_translate_http()` based on discovered transport type.

---

## 3. Environment Variable Handling

### 3.1 The Codex Env Isolation Model

**Codex does NOT inherit the full parent environment for stdio MCP server child processes.** It builds a minimal environment from a hardcoded allowlist, then layers explicit config on top.

The environment construction order (source: `codex-rs/rmcp-client/src/utils.rs`):

1. **Default allowlist** — only these host vars are forwarded automatically:
   - Unix: `HOME`, `LOGNAME`, `PATH`, `SHELL`, `USER`, `__CF_USER_TEXT_ENCODING`, `LANG`, `LC_ALL`, `TERM`, `TMPDIR`, `TZ`
   - Windows: `PATH`, `PATHEXT`, `COMSPEC`, `SYSTEMROOT`, `SYSTEMDRIVE`, `USERNAME`, `USERDOMAIN`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `PROGRAMFILES`, `PROGRAMFILES(X86)`, `PROGRAMW6432`, `PROGRAMDATA`, `LOCALAPPDATA`, `APPDATA`, `TEMP`, `TMP`, `POWERSHELL`, `PWSH`
2. **`env_vars`** — additional host env var names to forward (resolved from current environment)
3. **`env`** — explicit key=value overrides (applied last, wins over inherited values)

The child process is spawned with `env_clear()` then `envs(...)`, so it receives ONLY the constructed environment.

**Claude Code, by contrast, passes the full parent environment** to MCP server child processes. This means any CC MCP server that depends on env vars like `GITHUB_TOKEN`, `NPM_TOKEN`, etc. works automatically in CC but **silently fails in Codex** unless those vars are explicitly listed in `env_vars`.

### 3.2 `env_vars` — Forwarding Host Env Vars (stdio)

```toml
[mcp_servers.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
env_vars = ["GITHUB_TOKEN"]  # Forward from host env to child process
```

The bridge synthesizes `env_vars` from `${VAR}` patterns detected in CC `env` values. Referenced source vars are forwarded into a bridge-owned stdio launcher when needed so runtime expansion matches Claude Code semantics. See §3.3 for the full classification logic.

Servers that depend on env vars **not referenced in their CC config** (i.e., vars the server reads implicitly without CC listing them in `env`) will still silently fail in Codex. The bridge can only forward vars it can discover from the CC configuration.

**Common env vars that need forwarding** (not in Codex's default allowlist):

| Variable Pattern | Typical Usage |
|-----------------|---------------|
| `GITHUB_TOKEN`, `GITHUB_PERSONAL_ACCESS_TOKEN` | GitHub MCP servers |
| `NPM_TOKEN` | npm/registry MCP servers |
| `ANTHROPIC_API_KEY` | Anthropic MCP servers |
| `OPENAI_API_KEY` | OpenAI MCP servers |
| `SLACK_TOKEN`, `SLACK_BOT_TOKEN` | Slack MCP servers |
| `LINEAR_API_KEY` | Linear MCP servers |
| `DATABASE_URL` | Database MCP servers |
| Any `*_API_KEY`, `*_TOKEN`, `*_SECRET` | API-authenticated servers |

### 3.3 `${VAR}` Expansion Semantic Gap

CC expands `${VAR_NAME}` references in `env` values at runtime. Codex does NOT — it treats the value as a literal string. This means:

```json
{"env": {"API_KEY": "${MY_SECRET}"}}
```

- **CC**: Resolves `${MY_SECRET}` from host env → passes the resolved value
- **Codex**: Passes the literal string `${MY_SECRET}` as the env value

The bridge detects env-template references in CC `env` values and classifies
them into two tiers:

1. **Template value** (`GITHUB_TOKEN = "${GITHUB_TOKEN}"`,
   `API_KEY = "${MY_SECRET}"`, `URL = "https://${HOST}:${PORT}"`, or
   `${VAR:-default}` forms): Removed from `env`, referenced source vars
   added to `env_vars`, and preserved in a bridge-owned stdio launcher
   payload. The launcher expands the templates at runtime, then execs the
   original MCP server command.
2. **Static value** (`NODE_ENV = "production"`): Kept in `env` as-is.

This avoids the semantic mismatch where Codex `env_vars` skips unset vars
but Claude-style `${VAR}` expansion resolves missing refs to the empty
string.

### 3.4 `env_http_headers` — Env-Sourced HTTP Headers

```toml
[mcp_servers.example]
url = "https://api.example.com/mcp"

[mcp_servers.example.env_http_headers]
"X-API-Key" = "MY_API_KEY_ENV_VAR"
```

At runtime, Codex reads `$MY_API_KEY_ENV_VAR` from the host environment and sets it as the `X-API-Key` header value. Empty/missing values are silently skipped.

The bridge maps exact whole-value `${VAR}` and `$VAR` header values to
`env_http_headers`. Header values that mix literal text with env references
cannot be represented natively in Codex, so they are preserved in
`http_headers` with a diagnostic warning instead of being dropped.

---

## 4. OAuth Mapping

CC and Codex handle OAuth differently:

| Aspect | Claude Code | Codex |
|--------|-------------|-------|
| Config location | Per-server `oauth` object | Per-server `scopes` + `oauth_resource` + global settings |
| Client ID | `oauth.clientId` per server | Dynamic client registration or pre-configured |
| Callback port | `oauth.callbackPort` per server | `mcp_oauth_callback_port` global |
| Metadata URL | `oauth.authServerMetadataUrl` | RFC 9728 auto-discovery |
| Credential storage | System keychain | `mcp_oauth_credentials_store` (`auto`/`file`/`keyring`) |
| Login flow | Automatic on connection | Explicit `codex mcp login <name>` |

**Bridge strategy**: Emit a diagnostic warning. Optionally map `oauth.scopes` → `scopes` for convenience. Users must run `codex mcp login` separately.

---

## 5. Tool Namespacing

Both systems use the **same convention**: `mcp__<server>__<tool>` with double underscores.

| Aspect | Claude Code | Codex |
|--------|-------------|-------|
| Format | `mcp__<server>__<tool>` | `mcp__<server>__<tool>` |
| Plugin prefix | `mcp__plugin_<plugin>_<server>__<tool>` | No plugin prefix concept |
| Max length | 64 chars | 64 chars |
| Sanitization | Spaces → underscores | `[^a-zA-Z0-9_-]` → `_` + SHA1 suffix if truncated |

**Bridgeable**: Yes. Preserving server names ensures tool references transfer. Plugin-prefixed tool names need the `plugin_<name>_` prefix stripped.

---

## 6. Scope Mapping

| CC Scope | Codex Scope | Bridge Action |
|----------|-------------|---------------|
| Local (`~/.claude.json` per-project `mcpServers`) | Project (`.codex/config.toml`) | Write to `.codex/config.toml` |
| Project (`.mcp.json`) | Project (`.codex/config.toml`) | Write to `.codex/config.toml` |
| User (`~/.claude.json` global `mcpServers`) | User (`~/.codex/config.toml`) | Write to `~/.codex/config.toml` |
| Managed (`managed-mcp.json`) | System (`/etc/codex/config.toml`) | Out of scope — admin-managed |
| Plugin-bundled | User or project config | Extract and translate as standalone config |

---

## 7. Performance: Server Lifecycle

### 7.1 Session Startup

Codex creates a fresh `McpConnectionManager` for every session (`Codex::spawn`). All enabled MCP servers are started concurrently via `JoinSet`. The startup flow per server:

1. Validate server name
2. Create `RmcpClient` (spawn child process for stdio, open HTTP connection for streamable HTTP)
3. Send MCP `initialize` request with timeout (`startup_timeout_sec`, default 30s)
4. List tools from server (within same timeout window)
5. Report `Ready` or `Failed` status

### 7.2 Subagent MCP Restart (Performance Issue)

**Each subagent spawns fresh MCP server processes.** The `McpManager` (config reader) is shared via `Arc::clone`, but the `McpConnectionManager` (actual connections) is created new per `Codex::spawn`. There is no connection pooling or sharing of running MCP servers between parent and child sessions.

With N subagents and M enabled MCP servers, Codex spawns `(N+1) × M` server processes per session.

### 7.3 Implications for the Bridge

- Every bridge-generated MCP server entry = one process spawn + handshake per session
- Subagents multiply this cost linearly
- Fewer bridged servers = faster startups
- The `enabled` field can be used to temporarily disable servers
- The `startup_timeout_sec` field helps with slow-starting servers

---

## 8. Capability Gaps

### 8.1 CC Features Without Codex Equivalent

| CC Feature | Impact | Bridge Status |
|------------|--------|---------------|
| Full parent env inheritance for MCP servers | **High** | **Mitigated.** Bridge synthesizes `env_vars` from `${VAR}` patterns in CC config. Implicitly-used vars (not in CC config) remain a gap. |
| `${VAR}` expansion in `env` values | **High** | **Handled.** Template values go through the stdio launcher with referenced source vars forwarded via `env_vars`. See §3.3. |
| `${VAR}` in `command`, `args` | **Medium** | **Gap.** CC expands env templates there too; the bridge does not inspect them yet. See §9.2. |
| `${VAR}` in HTTP `url` | **Medium** | **Partially handled.** Codex has no mechanism to expand env vars in the `url` field, so the bridge preserves the literal value and emits a diagnostic. |
| `${VAR}` in non-Bearer header values | **Medium** | **Handled.** Whole-value refs → `env_http_headers`; mixed refs kept as literals with diagnostic. |
| `headersHelper` (dynamic headers via shell) | Medium | **Not bridgeable.** No Codex equivalent. Diagnostic emitted. |
| SSE transport | Low | **Skipped.** Deprecated in CC, unsupported in Codex. |
| MCP Prompts (`/mcp__server__prompt`) | Medium | **Not bridgeable.** Codex doesn't support MCP prompts. |
| `list_changed` dynamic updates | Low | N/A. Codex supports server refresh differently. |
| Tool Search (deferred loading) | Low | N/A. Codex loads all tool schemas upfront. |

### 8.2 Codex Features Without CC Equivalent

| Codex Feature | Impact | Bridge Status |
|---------------|--------|---------------|
| `env_vars` (forward host env vars by name) | **High** | **Synthesized** from CC `${VAR}` patterns. See §3.3. |
| `env_http_headers` (header values from env vars) | **Medium** | **Synthesized** from CC `${VAR}` header values. See §3.4. |
| `enabled` toggle | Low | Not generated. Users can add manually alongside bridge entries. |
| `required` flag | Low | Not generated. `codex exec` CI mode: exit on required server failure. |
| `enabled_tools` / `disabled_tools` | Medium | Not generated. No CC equivalent to bridge from. |
| Per-tool `approval_mode` | Low | Not generated. Fine-grained approval control. |
| `cwd` (working directory) | Low | Not mapped. CC servers inherit CWD from CC process. |
| `startup_timeout_sec` / `tool_timeout_sec` | Low | Not generated. CC uses `MCP_TIMEOUT` env var globally. |
| `scopes` / `oauth_resource` | Low | Not generated. Could pre-populate from `oauth.scopes` for `codex mcp login` convenience. See §9.2. |

---

## 9. Bridge Strategy

### 9.1 Currently Implemented (Phase 1 — Core)

1. **stdio servers**: Map `command`, `args`, and static `env` values directly. Any env-template values use a bridge-owned stdio launcher plus `env_vars` forwarding so runtime expansion matches Claude Code semantics.
2. **HTTP servers**: Map `url`, rename `headers` → `http_headers`. URL template values are preserved literally with a diagnostic because Codex cannot expand them. Exact `Bearer ${VAR}` / `Bearer $VAR` values become `bearer_token_env_var`. Exact whole-value env refs become `env_http_headers`. Mixed template values are preserved with diagnostics. `headersHelper` and `oauth` are flagged as diagnostics.
3. **Server names**: Preserved exactly — ensures tool name references (`mcp__server__tool`) transfer.
4. **Scope mapping**: CC local/project → `.codex/config.toml`; CC user → `~/.codex/config.toml`.
5. **Credential safety**: Literal credentials in Authorization headers omitted with warning. Literal credential-like env values warned but included.
6. **Ownership tracking**: Bridge-owned entries tracked in state/registry. User-authored entries never touched.

### 9.2 Needed Improvements (Phase 2 — Remaining Gaps)

7. **Scan `args` and `command` for CC env-template expansion**: the current bridge rewrites stdio `env` values, warns on HTTP URL templates, and rewrites HTTP headers, but does not inspect command-line arguments for `${VAR}` patterns.
8. **Handle special CC placeholders such as `${CLAUDE_PROJECT_DIR}`** in MCP translation where applicable.
9. **Map `oauth.scopes` → `scopes`**: Pre-populate for `codex mcp login` convenience.

### 9.3 Not Bridged (By Design)

- **Managed MCP** — admin-deployed, out of bridge scope
- **`headersHelper`** — no Codex equivalent; diagnostic warning only
- **SSE transport** — deprecated; skipped in discovery
- **MCP Prompts** — Codex doesn't support them
- **Plugin-bundled MCP servers** — skipped in discovery (plugin MCP is CC-internal)

### 9.4 Ownership and Reconciliation

MCP server configs in Codex's `config.toml` are shared with other Codex features. The bridge:

- Tracks which `[mcp_servers.*]` entries it owns (via bridge state + global registry)
- Never modifies user-authored MCP entries
- Handles same-name collisions by skipping (bridge yields to user)
- Cleans up bridge-owned entries on uninstall or when CC source is removed
- Uses content hashing for change detection (avoids unnecessary writes)

---

## 10. Translation Examples

### 10.1 stdio: CC → Codex

**CC** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"],
      "env": { "API_KEY": "sk-..." }
    }
  }
}
```

**Codex** (`~/.codex/config.toml`):
```toml
[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[mcp_servers.context7.env]
API_KEY = "sk-..."
```

### 10.2 stdio with env var references

**CC**:
```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

**Codex** (correct translation):
```toml
[mcp_servers.github]
command = "/path/to/python"
args = [
  "-m",
  "cc_codex_bridge.mcp_stdio_launcher",
  "--payload-json",
  "{\"command\":\"npx\",\"args\":[\"-y\",\"@modelcontextprotocol/server-github\"],\"env_templates\":{\"GITHUB_TOKEN\":\"${GITHUB_TOKEN}\"}}"
]
env_vars = ["GITHUB_TOKEN"]
```

### 10.3 HTTP with Bearer token extraction

**CC** (`.mcp.json`):
```json
{
  "mcpServers": {
    "github": {
      "type": "http",
      "url": "https://mcp.github.com/sse",
      "headers": {
        "Authorization": "Bearer ${GITHUB_TOKEN}"
      }
    }
  }
}
```

**Codex** (`.codex/config.toml`):
```toml
[mcp_servers.github]
url = "https://mcp.github.com/sse"
bearer_token_env_var = "GITHUB_TOKEN"
```

### 10.4 HTTP with env-sourced headers

**CC**:
```json
{
  "mcpServers": {
    "internal-api": {
      "type": "http",
      "url": "https://api.internal.com/mcp",
      "headers": {
        "X-API-Key": "${INTERNAL_API_KEY}",
        "Accept": "application/json"
      }
    }
  }
}
```

**Codex** (correct translation):
```toml
[mcp_servers.internal-api]
url = "https://api.internal.com/mcp"

[mcp_servers.internal-api.http_headers]
Accept = "application/json"

[mcp_servers.internal-api.env_http_headers]
"X-API-Key" = "INTERNAL_API_KEY"
```

### 10.5 Plugin MCP: CC → Codex

**CC** (plugin `.mcp.json` with tool name `mcp__plugin_context-a8c_context-a8c__context-a8c-execute-tool`):

**Codex** (`~/.codex/config.toml`):
```toml
# Plugin prefix stripped — server name is the plugin's MCP server name
[mcp_servers.context-a8c]
command = "..."
args = [...]
```

Tool name becomes `mcp__context-a8c__context-a8c-execute-tool` (plugin prefix dropped).

---

## Sources

Codex behavior documented here was verified against the [Codex CLI source](https://github.com/openai/codex) (`codex-rs/` tree). Key source files for MCP handling:

| File | What it defines |
|------|----------------|
| `codex-rs/config/src/mcp_types.rs` | `RawMcpServerConfig`, `McpServerConfig`, `McpServerTransportConfig` — the TOML schema and validation |
| `codex-rs/rmcp-client/src/utils.rs` | `create_env_for_mcp_server`, `build_default_headers`, `DEFAULT_ENV_VARS` — env construction for child processes |
| `codex-rs/codex-mcp/src/mcp_connection_manager.rs` | `McpConnectionManager::new`, `start_server_task` — connection lifecycle and startup |
| `codex-rs/core/src/codex.rs` | Session initialization — where `McpConnectionManager` is created per-session |
| `codex-rs/core/src/codex_delegate.rs` | Subagent spawning — shows `McpManager` (config) is shared but connections are not |
| `codex-rs/core/config.schema.json` | Generated JSON Schema for the full `config.toml` format |

Claude Code behavior documented here is based on [docs/claude-code-mcp-reference.md](claude-code-mcp-reference.md). Codex CLI behavior beyond MCP is covered in [docs/codex-cli-reference.md](codex-cli-reference.md).
