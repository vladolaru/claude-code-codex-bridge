Last updated: 2026-08-24 15:09

> **Prompt:** "The current system cc-codex-bridge is failing at status. I am trying to add the superpowers plugin to the ignore list but I can't do it"
> **Prompt:** "There is a bug relates to the playground plugin and some issue about version"
> **Prompt:** "The cc-codex-bridge CLI should not fail in such manner."
> **Prompt:** "confirm."

# Exclusion-aware plugin discovery design

## Problem

Claude reports `playground@claude-plugins-official` as enabled with version `unknown` and installs it under a cache directory named `unknown/`. The bridge intentionally requires semantic-version cache directory names for plugins in discovery scope, so `status` aborts before producing a report. Although `claude-plugins-official/playground` is already globally excluded, the current pipeline loads and applies exclusions only after plugin discovery and version validation. The discovery-backed `config exclude add` command uses the same failing pipeline, so an unrelated malformed plugin also blocks adding `superpowers-marketplace/superpowers` to the exclusion list.

## Approved behavior

Configured plugin exclusions define plugin discovery scope. An enabled plugin that is effectively excluded must be filtered before semantic-version validation and cannot block `status` or `reconcile`. A malformed enabled plugin that is not excluded remains a hard discovery failure, preserving the existing protection against silently dropping previously bridged artifacts.

An explicit `config exclude add plugin <id>` operation must remain usable as a recovery mechanism even when any enabled plugin cache has an invalid version directory. It validates the requested plugin identity against the enabled plugin IDs reported by `claude plugins list --json`, rather than requiring successful content discovery for every plugin. Other entity kinds continue to use full discovery because skills, agents, commands, and MCP servers require content-level validation.

## Data flow

For `status` and `reconcile`, the bridge resolves the project root, loads global and project exclusions, applies CLI replacement semantics, queries Claude for enabled plugin IDs, removes effectively excluded plugin IDs from that set, and then performs cache discovery and semantic-version selection only within the remaining set. After discovery, the existing exclusion filter still handles skills, agents, commands, and MCP servers and produces the exclusion report used by output rendering.

For explicit plugin exclusion adds, the command resolves scope and normalizes the requested ID, queries Claude for enabled plugin IDs, validates exact or unique shorthand identity using the same public ID convention, and writes the exclusion without discovering plugin contents. Interactive plugin selection should use the enabled ID list for the plugin choice; entity kinds that depend on plugin contents continue through full discovery after applying already configured plugin exclusions.

## Boundaries and compatibility

Semantic version ordering and the `InstalledPlugin` model remain unchanged. The bridge does not synthesize a version for `unknown`, trust potentially stale Claude `version` or `installPath` metadata, or silently accept arbitrary cache layouts. Existing CLI exclusion replacement semantics remain authoritative: when a pipeline command supplies explicit plugin exclusions, those values replace the merged global and project plugin exclusions before discovery scope is calculated.

Project-local and global plugin exclusions both participate in the pre-discovery filter. Status reporting continues to show configured exclusions through the existing exclusion report contract; the implementation must retain excluded plugin IDs in that report even though their plugin contents were never instantiated as `InstalledPlugin` objects.

## Error handling

If querying Claude's plugin list fails, the command retains the existing discovery error. If an explicit plugin ID is not enabled or is ambiguous in shorthand form, `config exclude add` refuses the mutation with a specific not-found or ambiguity message. Invalid cache versions for non-excluded enabled plugins remain fatal with the existing semantic-version diagnostic.

## Verification

Tests will prove that an excluded enabled plugin with only an `unknown/` cache directory does not block the build pipeline, an equivalent non-excluded plugin still fails, and CLI plugin-exclusion replacement semantics are applied before discovery. Command tests will prove that explicit plugin exclusion add succeeds despite an unrelated malformed enabled plugin, rejects an unknown plugin ID without writing config, and preserves discovery-backed behavior for non-plugin kinds. The full project suite will run through `.venv`, followed by a live `status` check and the requested global `superpowers-marketplace/superpowers` exclusion against the user's current configuration.

## Documentation and release metadata

`DESIGN.md` will describe exclusion-aware discovery ordering and the plugin exclusion recovery path. `CHANGELOG.md` will record the user-visible fix under `Unreleased`. Semver classifies this as a patch-level fix; package version declarations will remain unchanged unless this work is used to prepare a release.
