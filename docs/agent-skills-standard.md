# Agent Skills Standard Reference

This document is the bridge project's authoritative reference for the open Agent Skills standard. It captures the specification, client implementation contract, and script conventions that the bridge must comply with when generating Codex-compatible skill artifacts.

Source: [agentskills.io](https://agentskills.io/) — the canonical upstream specification.

---

## 1. Skill Directory Structure

A skill is a directory containing, at minimum, a `SKILL.md` file:

```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code
├── references/       # Optional: documentation
├── assets/           # Optional: templates, resources
└── ...               # Any additional files or directories
```

## 2. SKILL.md Format

The `SKILL.md` file must contain YAML frontmatter followed by Markdown content.

### 2.1 Frontmatter Fields

| Field           | Required | Constraints |
|-----------------|----------|-------------|
| `name`          | Yes      | Max 64 chars. Lowercase letters, numbers, hyphens only. Must not start/end with hyphen. No consecutive hyphens. **Must match the parent directory name.** |
| `description`   | Yes      | Max 1024 chars. Non-empty. Describes what the skill does and when to use it. |
| `license`       | No       | License name or reference to a bundled license file. |
| `compatibility` | No       | Max 500 chars. Environment requirements (intended product, system packages, network access). |
| `metadata`      | No       | Arbitrary key-value mapping (string keys to string values). |
| `allowed-tools` | No       | Space-delimited list of pre-approved tools. Experimental. |

### 2.2 Name Field Rules

- 1–64 characters
- Unicode lowercase alphanumeric (`a-z`, `0-9`) and hyphens (`-`) only
- Must not start or end with a hyphen
- Must not contain consecutive hyphens (`--`)
- Must match the parent directory name

Valid: `pdf-processing`, `data-analysis`, `code-review`
Invalid: `PDF-Processing` (uppercase), `-pdf` (leading hyphen), `pdf--processing` (consecutive hyphens)

### 2.3 Description Field

- 1–1024 characters
- Should describe both what the skill does and when to use it
- Should include keywords that help agents identify relevant tasks

### 2.4 Body Content

The Markdown body after the frontmatter contains skill instructions. No format restrictions. Write whatever helps agents perform the task effectively.

Recommended sections: step-by-step instructions, input/output examples, common edge cases.

The agent loads the entire file once the skill is activated. Keep the main `SKILL.md` under 500 lines. Move detailed reference material to separate files.

### 2.5 Minimal Example

```markdown
---
name: skill-name
description: A description of what this skill does and when to use it.
---
```

### 2.6 Full Example

```markdown
---
name: pdf-processing
description: Extract PDF text, fill forms, merge files. Use when handling PDFs.
license: Apache-2.0
metadata:
  author: example-org
  version: "1.0"
---

# PDF Processing

## When to use this skill
Use this skill when the user needs to work with PDF files...
```

## 3. Optional Directories

### 3.1 `scripts/`

Executable code that agents can run. Scripts should be self-contained, include helpful error messages, and handle edge cases. Supported languages depend on the agent implementation.

### 3.2 `references/`

Additional documentation loaded on demand. Keep individual reference files focused — agents load them individually, so smaller files mean less context use.

### 3.3 `assets/`

Static resources: templates, images, data files, schemas.

## 4. Progressive Disclosure

Skills are structured for efficient context use across three tiers:

| Tier | What | When Loaded | Token Cost |
|------|------|-------------|------------|
| 1. Catalog | `name` + `description` | Session start | ~50–100 tokens per skill |
| 2. Instructions | Full `SKILL.md` body | Skill activated | <5000 tokens recommended |
| 3. Resources | Scripts, references, assets | When referenced | Varies |

## 5. File References

Use relative paths from the skill root:

```markdown
See [the reference guide](references/REFERENCE.md) for details.

Run the extraction script:
scripts/extract.py
```

Keep file references one level deep from `SKILL.md`. Avoid deeply nested reference chains.

## 6. Client Implementation Contract

### 6.1 Discovery

Clients scan skill directories at multiple scopes:

| Scope   | Path Convention | Purpose |
|---------|-----------------|---------|
| Project | `<project>/.<client>/skills/` | Client-specific project skills |
| Project | `<project>/.agents/skills/` | Cross-client interoperability |
| User    | `~/.<client>/skills/` | Client-specific user skills |
| User    | `~/.agents/skills/` | Cross-client interoperability |

Within each directory, look for subdirectories containing a file named exactly `SKILL.md`.

Practical scanning rules:
- Skip `.git/`, `node_modules/`, etc.
- Max depth: 4–6 levels
- Max directories per root: 2,000
- Hidden directories (starting with `.`) are skipped

### 6.2 Name Collision Handling

When two skills share the same `name`, project-level skills override user-level skills. Within the same scope, pick a deterministic rule (first-found or last-found) and be consistent.

### 6.3 Trust

Project-level skills come from potentially untrusted repositories. Gate project-level skill loading on a trust check.

### 6.4 Parsing

1. Find `---` delimiters to extract YAML frontmatter
2. Parse `name` and `description` (required) plus optional fields
3. Everything after the closing `---` is the body content

Lenient validation: warn on issues but still load the skill when possible. Skip the skill only when the description is missing/empty or YAML is completely unparseable.

### 6.5 Activation

Two mechanisms:
- **File-read activation**: Model reads `SKILL.md` via its standard file-read tool
- **Dedicated tool activation**: Registered tool (e.g., `activate_skill`) returns content

### 6.6 Context Management

- Exempt skill content from context compaction/pruning
- Deduplicate activations within a session
- Optionally support subagent delegation for complex skill workflows

## 7. Script Design for Agents

Scripts must work in non-interactive shells. Key requirements:

- **No interactive prompts** — accept input via CLI flags, env vars, or stdin
- **`--help` output** — primary way agents learn the script's interface
- **Helpful error messages** — say what went wrong, what was expected, what to try
- **Structured output** — prefer JSON/CSV over free-form text; data on stdout, diagnostics on stderr
- **Idempotency** — agents may retry; "create if not exists" is safer than "create and fail on duplicate"
- **Meaningful exit codes** — distinct codes for different failure types
- **Predictable output size** — default to summaries; support `--offset`/`--output` for large output

## Sources

- [Specification](https://agentskills.io/specification)
- [What are skills?](https://agentskills.io/what-are-skills)
- [Client implementation guide](https://agentskills.io/client-implementation/adding-skills-support)
- [Using scripts in skills](https://agentskills.io/skill-creation/using-scripts)
- [Reference library (validation)](https://github.com/agentskills/agentskills/tree/main/skills-ref)
- [Example skills](https://github.com/anthropics/skills)
