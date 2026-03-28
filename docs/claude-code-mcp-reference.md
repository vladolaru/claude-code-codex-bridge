# Claude Code MCP Reference

This document is the bridge project's authoritative reference for how Claude Code discovers, configures, and manages MCP (Model Context Protocol) servers. It captures behaviors the bridge must read when translating MCP configurations to Codex.

Sources: [Claude Code MCP docs](https://code.claude.com/docs/en/mcp), [Claude Code Settings docs](https://code.claude.com/docs/en/settings), [Claude Code Permissions docs](https://code.claude.com/docs/en/permissions).

---

## 1. Configuration Locations

MCP server definitions and MCP permission policies live in different files.

### 1.1 Server Definition Files

| Scope | File | Shared? | Notes |
|-------|------|---------|-------|
| **Local** (default) | `~/.claude.json` under `projects.<path>.mcpServers` | No | Private to you, current project only |
| **Project** | `.mcp.json` at repo root | Yes (committed) | Shared with team |
| **User** | `~/.claude.json` top-level `mcpServers` | No | Available across all projects |
| **Managed** | System-level `managed-mcp.json` | Yes (IT-deployed) | See paths below |
| **Plugin** | `.mcp.json` at plugin root or inline in `plugin.json` | Via plugin | Plugin-bundled servers |

Managed MCP paths:

| Platform | Path |
|----------|------|
| macOS | `/Library/Application Support/ClaudeCode/managed-mcp.json` |
| Linux/WSL | `/etc/claude-code/managed-mcp.json` |
| Windows | `C:\Program Files\ClaudeCode\managed-mcp.json` |

**Important**: "Local scope" MCP servers are stored in `~/.claude.json` (under the project path key), NOT in `.claude/settings.local.json`.

### 1.2 Permission/Policy Files

MCP tool permissions and server-level policies live in `settings.json`:

| Scope | File |
|-------|------|
| User | `~/.claude/settings.json` |
| Project (shared) | `.claude/settings.json` |
| Local (private) | `.claude/settings.local.json` |
| Managed | `managed-settings.json` in system directories |

### 1.3 `~/.claude.json` Structure

```json
{
  "mcpServers": {
    "user-scoped-server": { ... }
  },
  "projects": {
    "/path/to/project": {
      "mcpServers": {
        "local-scoped-server": { ... }
      }
    }
  }
}
```

### 1.4 Scope Precedence

When servers with the same name exist at multiple scopes:

1. **Local** (highest) — per-project, per-user
2. **Project** — shared `.mcp.json`
3. **User** (lowest) — global in `~/.claude.json`

Managed MCP (`managed-mcp.json`) takes exclusive control when present.

---

## 2. Server Configuration Format

### 2.1 stdio (Local Process)

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path"],
      "env": {
        "API_KEY": "${API_KEY}",
        "LOG_LEVEL": "info"
      }
    }
  }
}
```

Fields: `command` (required), `args` (optional), `env` (optional). Type is implicit `"stdio"` when `command` is present.

### 2.2 HTTP (Streamable HTTP — Recommended for Remote)

```json
{
  "mcpServers": {
    "server-name": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}"
      }
    }
  }
}
```

Fields: `type` (`"http"`, required), `url` (required), `headers` (optional), `headersHelper` (optional — shell command that outputs JSON headers, 10s timeout), `oauth` (optional).

### 2.3 SSE (Deprecated)

Same as HTTP but `"type": "sse"`. Use HTTP instead.

### 2.4 OAuth

```json
{
  "mcpServers": {
    "server-name": {
      "type": "http",
      "url": "https://mcp.example.com/mcp",
      "oauth": {
        "clientId": "your-client-id",
        "callbackPort": 8080,
        "authServerMetadataUrl": "https://auth.example.com/.well-known/openid-configuration"
      }
    }
  }
}
```

Client secrets stored in system keychain, not config.

### 2.5 Complete Field Reference

| Field | Transport | Description |
|-------|-----------|-------------|
| `type` | all | `"stdio"` (implicit), `"http"`, `"sse"` |
| `command` | stdio | Executable path |
| `args` | stdio | Command arguments |
| `env` | stdio | Environment variables |
| `url` | http/sse | Server endpoint URL |
| `headers` | http/sse | Static HTTP headers |
| `headersHelper` | http/sse | Shell command for dynamic headers |
| `oauth` | http/sse | OAuth configuration object |
| `oauth.clientId` | http/sse | Pre-registered OAuth client ID |
| `oauth.callbackPort` | http/sse | Fixed OAuth redirect port |
| `oauth.authServerMetadataUrl` | http/sse | Override metadata discovery URL |

### 2.6 Environment Variable Expansion

Supported in `.mcp.json` within `command`, `args`, `env`, `url`, `headers`:

| Syntax | Behavior |
|--------|----------|
| `${VAR}` | Expand to env var value |
| `${VAR:-default}` | Expand with fallback |
| `${CLAUDE_PLUGIN_ROOT}` | Plugin root directory (plugin configs only) |
| `${CLAUDE_PLUGIN_DATA}` | Plugin persistent data directory (plugin configs only) |
| `${CLAUDE_PROJECT_DIR}` | Current project directory |

---

## 3. Tool Namespacing

### 3.1 Standard MCP Tools

Format: **`mcp__<server-name>__<tool-name>`** (double underscores)

### 3.2 Plugin-Provided MCP Tools

Format: **`mcp__plugin_<plugin-name>_<server-name>__<tool-name>`**

### 3.3 MCP Prompts as Commands

Format: **`/mcp__<server-name>__<prompt-name>`**

### 3.4 Limits

- Tool name character limit: 64 characters
- Server/prompt names normalized: spaces become underscores

---

## 4. Server Lifecycle

- **stdio**: Spawned as child processes at session start, auto-terminated on exit
- **HTTP/SSE**: Connections established at session start, OAuth refreshed automatically
- **Startup timeout**: Configurable via `MCP_TIMEOUT` env var
- **Dynamic updates**: Supports `list_changed` notifications — tools/prompts/resources update without reconnection
- **Output limits**: Warning at 10,000 tokens; max 25,000 (configurable via `MAX_MCP_OUTPUT_TOKENS`)
- **Tool Search**: Enabled by default — only tool names loaded at startup, full schemas on demand

---

## 5. Permissions

### 5.1 Tool-Level Rules

```json
{
  "permissions": {
    "allow": ["mcp__github__list_prs", "mcp__sentry"],
    "deny": ["mcp__dangerous-server"]
  }
}
```

Patterns: `mcp__server` (all tools), `mcp__server__*` (wildcard), `mcp__server__tool` (specific).

### 5.2 Server-Level Policies (Managed)

```json
{
  "allowedMcpServers": [
    { "serverName": "github" },
    { "serverCommand": ["npx", "-y", "package"] },
    { "serverUrl": "https://mcp.company.com/*" }
  ],
  "deniedMcpServers": [
    { "serverName": "dangerous" }
  ]
}
```

Each entry uses exactly ONE of: `serverName`, `serverCommand` (exact array match), `serverUrl` (wildcard patterns).

### 5.3 Project MCP Approval

- `.mcp.json` servers require user approval before use
- `enabledMcpjsonServers` / `disabledMcpjsonServers` arrays for pre-approval
- `claude mcp reset-project-choices` to reset decisions

Blocked tools are completely removed from the schema sent to the model.

---

## 6. CLI Commands

| Command | Purpose |
|---------|---------|
| `claude mcp add --transport <type> <name> <url-or-args>` | Add server |
| `claude mcp add-json <name> '<json>'` | Add from JSON config |
| `claude mcp add-from-claude-desktop` | Import from Claude Desktop |
| `claude mcp list` | List configured servers |
| `claude mcp get <name>` | Get server details |
| `claude mcp remove <name>` | Remove server |
| `claude mcp enable <name>` | Re-enable disabled server |
| `claude mcp reset-project-choices` | Reset project approvals |
| `claude mcp serve` | Run CC itself as MCP server |
| `/mcp` (in session) | Interactive management |

Scope flag: `--scope local` (default), `--scope project`, `--scope user`.

---

## 7. Capabilities Summary

| MCP Capability | Supported | Notes |
|----------------|-----------|-------|
| **Tools** | Yes | Full support, deferred loading default |
| **Resources** | Yes | Via MCP servers |
| **Prompts** | Yes | Exposed as `/mcp__server__prompt` slash commands |
| **Elicitation** | Yes | Server-to-client input requests |
| **Channels** | Yes | `claude/channel` push messages |

---

## Sources

- [Claude Code MCP Documentation](https://code.claude.com/docs/en/mcp)
- [Claude Code Settings Documentation](https://code.claude.com/docs/en/settings)
- [Claude Code Permissions Documentation](https://code.claude.com/docs/en/permissions)
