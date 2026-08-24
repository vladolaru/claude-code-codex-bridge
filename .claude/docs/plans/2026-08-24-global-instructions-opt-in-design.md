Last updated: 2026-08-24 17:00

> **Prompt:** "So just ignore CLAUDE.md"
> **Prompt:** "Yes"
> **Prompt:** "It does, but it should default to false since that is the current behavior"
> **Prompt:** "Actually, I was wrong. The default should be true"

# Configurable global instructions bridging design

## Problem

The bridge currently treats a discovered user-global `~/.claude/CLAUDE.md` as desired content for `~/.codex/AGENTS.md` on every pipeline run. This conflicts with an independently maintained Codex global instructions file. The existing ownership guard correctly refuses to overwrite an `AGENTS.md` without the bridge sentinel, but the hard error blocks otherwise unrelated reconciliation, including removal of artifacts from an excluded plugin.

Project-level instruction behavior is not part of this problem. Project `CLAUDE.md` discovery, bootstrap, preservation, and `@AGENTS.md` shim behavior must remain unchanged.

## Approved behavior

Global instructions bridging remains enabled by default for backward compatibility. The global bridge configuration can disable it explicitly:

```toml
[sync]
global_instructions = false
```

When the setting is `false`, the bridge ignores `~/.claude/CLAUDE.md` for Codex generation. It does not create, update, validate, adopt, or remove `~/.codex/AGENTS.md`, regardless of whether that file is hand-authored or contains the bridge sentinel. Existing Codex global instructions remain untouched.

When the setting is absent or `true`, the existing behavior remains: discovered Claude global instructions become Codex global instructions with the bridge sentinel, hand-authored destination conflicts remain hard errors, source changes update bridge-owned output, and disappearance of the source removes bridge-owned output.

## Configuration and data flow

`BridgeConfig` gains a boolean `sync_global_instructions` field with default `true`. `load_config()` reads `[sync].global_instructions`; absent, malformed, non-table, or non-boolean values resolve to `true`, with malformed values logged consistently with other recoverable global configuration problems.

The build pipeline carries this machine-wide policy into `DesiredState` separately from the optional instruction content. This distinction is required because `global_instructions=None` currently means the source disappeared and can trigger removal of a bridge-owned destination. A separate `manage_global_instructions` boolean prevents all global-instruction planning when disabled.

Project configuration and CLI exclusion flags do not override this setting. There is one machine-wide Codex global instructions file, so a machine-wide global configuration switch is the canonical control.

## Status and error handling

Human and JSON status output must make the disabled policy visible so users can distinguish “ignored by configuration” from “no Claude global instructions discovered.” When disabled, a hand-authored or bridge-sentinel Codex `AGENTS.md` is not an error and produces no pending global-instruction change.

When enabled, existing ownership errors and safety protections remain unchanged. Invalid `[sync]` configuration is recoverable and defaults to enabled, matching the established behavior rather than silently changing synchronization policy.

## Compatibility

The default remains backward-compatible. Users who want Codex global instructions to remain independent set `[sync] global_instructions = false`. When disabled, existing bridge-owned `~/.codex/AGENTS.md` files are preserved; they are neither stripped of the sentinel nor removed.

No project-level instruction behavior changes. Plugin, skill, agent, command, prompt, resource, and MCP reconciliation remain unchanged.

## Verification

Tests will prove configuration parsing defaults to true, accepts explicit true and false, and safely handles malformed values. Pipeline and reconcile tests will prove that disabled management ignores a discovered global `CLAUDE.md`, preserves both hand-authored and sentinel-bearing Codex `AGENTS.md`, and emits no global-instruction change; enabled management will retain the existing create, update, conflict, and stale-removal behavior. CLI tests will verify visible human and JSON status reporting.

After the implementation passes the full project suite, live verification will preserve the current `~/.codex/AGENTS.md`, add the externally managed `user/using-wpcom-local` skill to the global exclusion list, run reconciliation across all registered projects, and confirm the excluded superpowers plugin artifacts are removed from Codex.

## Documentation and release metadata

`README.md` and `DESIGN.md` will describe configurable global instruction bridging and its default-enabled behavior. `CHANGELOG.md` will record the user-visible opt-out under `Unreleased`. Package versions will remain unchanged until a release is requested.
