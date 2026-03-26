#!/usr/bin/env python3
"""Codex bridge generator CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PACKAGE_PARENT = Path(__file__).resolve().parent.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from cc_codex_bridge.discover import resolve_project_root
from cc_codex_bridge.doctor import (
    doctor_exit_code,
    format_doctor_json,
    format_doctor_report,
    run_doctor,
)
from cc_codex_bridge.exclusions import ExclusionReport
from cc_codex_bridge.install_launchagent import (
    DEFAULT_START_INTERVAL,
    GLOBAL_LAUNCHAGENT_LABEL,
    build_global_launchagent_plist,
    build_launchagent_label,
    build_launchagent_plist,
    find_bridge_launchagents,
    install_launchagent,
    uninstall_launchagent,
)
from cc_codex_bridge.model import (
    AgentTranslationDiagnostic,
    DiscoveryError,
    ReconcileError,
    SkillValidationDiagnostic,
    TranslationError,
)
from cc_codex_bridge.bridge_home import resolve_bridge_home, project_state_dir
from cc_codex_bridge.reconcile import (
    Change,
    ReconcileReport,
    build_project_desired_state,
    compute_project_drift,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from cc_codex_bridge.translate_agents import format_agent_translation_diagnostics
from cc_codex_bridge.translate_skills import format_skill_validation_diagnostics


PIPELINE_COMMANDS = {"reconcile", "status"}
LAUNCHAGENT_COMMANDS = {"autosync"}
UTILITY_COMMANDS = {"doctor"}

_GITHUB_REPO = "vladolaru/claude-code-codex-bridge"
_GITHUB_API_LATEST = f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest"
_INSTALL_SCRIPT_URL = f"https://github.com/{_GITHUB_REPO}/releases/latest/download/install.sh"

_MIN_HELP_POSITION = 24
_HELP_GAP = 4  # spaces between the longest flag+metavar and the help text


class _AutoWidthHelpFormatter(argparse.HelpFormatter):
    """HelpFormatter that sizes max_help_position from actual actions.

    The default HelpFormatter hard-codes max_help_position=24, which
    forces help text onto a new line when the flag+metavar exceeds ~20
    chars.  This subclass lets it grow to fit, capped at half the
    terminal width.
    """

    def __init__(self, prog: str, **kwargs: object) -> None:
        # Start with a generous max_help_position; format_help will
        # clamp it after _action_max_length is known.
        kwargs.setdefault("max_help_position", 52)
        super().__init__(prog, **kwargs)  # type: ignore[arg-type]

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        # Fill each explicit line independently so URLs are never broken.
        import textwrap
        filled = []
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                filled.append("")
            elif " " not in stripped or "http://" in stripped or "https://" in stripped:
                filled.append(indent + stripped)
            else:
                filled.append(textwrap.fill(
                    stripped, width,
                    initial_indent=indent, subsequent_indent=indent,
                    break_long_words=False, break_on_hyphens=False,
                ))
        return "\n".join(filled)

    def format_help(self) -> str:
        # After all actions have been added, _action_max_length holds
        # the widest invocation string.  Clamp max_help_position to
        # that width + a small gap, bounded by [_MIN_HELP_POSITION,
        # width // 2].
        ideal = self._action_max_length + _HELP_GAP
        self._max_help_position = min(
            max(ideal, _MIN_HELP_POSITION),
            self._width // 2,
        )
        return super().format_help()

    def start_section(self, heading: str | None) -> None:
        # Capitalize section headings: "options" -> "Options", etc.
        if heading:
            heading = heading[0].upper() + heading[1:]
        super().start_section(heading)

    def _format_action(self, action: argparse.Action) -> str:
        # Skip the subparser group's own metavar line (e.g. "COMMAND")
        # so that only the individual command entries appear.
        if isinstance(action, argparse._SubParsersAction):
            parts = []
            for choice_action in action._get_subactions():
                parts.append(self._format_action(choice_action))
            return self._join_parts(parts)
        # Capitalize argparse's default help strings.
        if action.help and action.help[0].islower():
            action.help = action.help[0].upper() + action.help[1:]
        return super()._format_action(action)

    def _format_usage(self, usage, actions, groups, prefix):
        if prefix is None:
            prefix = "Usage: "
        return super()._format_usage(usage, actions, groups, prefix)


def _fetch_latest_version(*, timeout: float = 5.0) -> str | None:
    """Return the latest release tag from GitHub, or None on any failure."""
    try:
        import urllib.request
        req = urllib.request.Request(
            _GITHUB_API_LATEST,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "cc-codex-bridge"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "")
        return tag.lstrip("v") if tag else None
    except Exception:
        return None


def _is_editable_install() -> bool:
    """Return True when the package is installed in editable (development) mode."""
    try:
        import importlib.metadata
        dist = importlib.metadata.distribution("cc-codex-bridge")
        direct_url_text = dist.read_text("direct_url.json")
        if direct_url_text:
            data = json.loads(direct_url_text)
            return bool(data.get("dir_info", {}).get("editable", False))
    except Exception:
        pass
    return False


def _handle_upgrade_command(args: argparse.Namespace) -> int:
    """Handle the upgrade command."""
    from cc_codex_bridge import __version__
    from cc_codex_bridge._colors import color_fns

    c = color_fns()
    print()
    print(f"{c['key']('INSTALLED:')} v{__version__}")

    if _is_editable_install():
        print(c["warn"]("Development install detected — upgrade is not supported."))
        print(f"  To update your checkout:  {c['cmd']('git pull && pip install -e .')}")
        print("  To switch to a release:   run the install script in a shell without an active venv:")
        print(f"    {c['cmd']('deactivate')}  # or open a new terminal")
        print(f"    {c['cmd']('curl -fsSL https://github.com/vladolaru/claude-code-codex-bridge/releases/latest/download/install.sh | bash')}")
        return 1

    latest = _fetch_latest_version()
    if latest is None:
        print(f"{c['warn']('WARNING:')} Could not reach GitHub — check your connection.")
        return 1

    print(f"{c['key']('LATEST:')}    v{latest}")

    from cc_codex_bridge.model import SemVer
    try:
        current_sv = SemVer.parse(__version__)
        latest_sv = SemVer.parse(latest)
    except Exception:
        print(f"{c['bad']('Error:')} Could not parse version strings.")
        return 1

    if not (current_sv < latest_sv):
        print(f"{c['good']('Already up to date.')}")
        return 0

    if getattr(args, "check", False):
        print(f"{c['warn'](f'Update available: v{latest}')}")
        print(f"  Run {c['cmd']('cc-codex-bridge upgrade')} to install.")
        return 0

    print(f"{c['warn'](f'Upgrading to v{latest}...')}")
    import subprocess
    import urllib.request
    import tempfile
    import os
    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tmp:
        tmp_path = tmp.name
        try:
            with urllib.request.urlopen(_INSTALL_SCRIPT_URL, timeout=30) as resp:
                tmp.write(resp.read())
        except Exception as exc:
            os.unlink(tmp_path)
            print(f"{c['bad']('Error:')} Failed to download install script: {exc}")
            return 1

    try:
        os.chmod(tmp_path, 0o755)
        result = subprocess.run(["bash", tmp_path], check=False)
        return result.returncode
    finally:
        os.unlink(tmp_path)


def _colored_description(version: str) -> str:
    """Build the parser description with colors when the terminal supports them."""
    try:
        from _colorize import can_colorize, get_theme
        if can_colorize():
            t = get_theme(force_color=True).argparse
            prog = f"{t.prog}cc-codex-bridge{t.reset}"
            ver = f"{t.action}v{version}{t.reset}"
            return (
                f"{prog} {ver} — Bridge your local Claude Code setup into Codex so both tools stay equally effective.\n\n"
                "Detailed documentation: https://github.com/vladolaru/claude-code-codex-bridge/blob/main/README.md"
            )
    except ImportError:
        pass
    return (
        f"cc-codex-bridge v{version} — Bridge your local Claude Code setup into Codex so both tools stay equally effective.\n\n"
        "Detailed documentation: https://github.com/vladolaru/claude-code-codex-bridge/blob/main/README.md"
    )


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser with a blank line before errors and no prog prefix in the message."""

    def error(self, message: str) -> None:
        from cc_codex_bridge._colors import color_fns
        c = color_fns()
        self.print_usage(sys.stderr)
        print(file=sys.stderr)
        print(c["bad"](f"Error: {message}"), file=sys.stderr)
        self.exit(2)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    from cc_codex_bridge._colors import color_fns as _cfns
    _c = _cfns()

    # Shared discovery flags: project root, plugin cache, Claude home.
    common_discovery = argparse.ArgumentParser(add_help=False)
    common_discovery.add_argument(
        "--project",
        type=Path,
        help="Target project directory (default: current working directory).",
    )
    common_discovery.add_argument(
        "--cache-dir",
        type=Path,
        help="Claude plugin cache directory (default: ~/.claude/plugins/cache).",
    )
    common_discovery.add_argument(
        "--claude-home",
        type=Path,
        help="Claude home directory (default: ~/.claude).",
    )

    # Discovery + Codex output flags: used by commands that write to ~/.codex.
    common_with_codex = argparse.ArgumentParser(add_help=False, parents=[common_discovery])
    common_with_codex.add_argument(
        "--codex-home",
        type=Path,
        help="Codex home directory (default: ~/.codex).",
    )

    from cc_codex_bridge import __version__

    parser = _ArgumentParser(
        prog="cc-codex-bridge",
        description=_colored_description(__version__),
        formatter_class=_AutoWidthHelpFormatter,
    )
    parser.add_argument(
        "-v", "--version",
        action="version",
        version=f"%(prog)s v{__version__}",
    )

    _subparsers = parser.add_subparsers(
        dest="command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_add = _subparsers.add_parser

    def _add_parser(*args, **kwargs):
        kwargs.setdefault("formatter_class", _AutoWidthHelpFormatter)
        return _raw_add(*args, **kwargs)

    subparsers = _subparsers
    subparsers.add_parser = _add_parser  # type: ignore[method-assign]

    reconcile_parser = subparsers.add_parser(
        "reconcile",
        parents=[common_with_codex],
        help="Sync Codex artifacts with the current Claude Code setup",
        description=(
            "Sync Codex artifacts with the current Claude Code setup. "
            "Discovers installed plugins, user-level and project-level "
            "skills/agents/commands, translates them into Codex-compatible "
            "files, and writes them to ~/.codex/ and the project. "
            "Safe to run repeatedly — only changed files are updated."
        ),
    )
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing any files.",
    )
    reconcile_parser.add_argument(
        "--diff",
        action="store_true",
        help="Show unified diffs between current and desired file contents (requires --dry-run).",
    )
    status_parser = subparsers.add_parser(
        "status",
        parents=[common_with_codex],
        help="Show sync status and pending changes",
        description=(
            "Compare the current Codex artifacts on disk with what reconcile "
            "would produce. Reports whether the project is in sync and lists "
            "any pending creates, updates, or removals. Does not write or modify any files."
        ),
    )
    for pipeline_parser in (reconcile_parser, status_parser):
        pipeline_parser.add_argument(
            "--all",
            action="store_true",
            default=False,
            help="Operate on all registered projects and scan-config paths.",
        )
    for exclude_parser in (reconcile_parser,):
        exclude_parser.add_argument(
            "--exclude-plugin",
            action="append",
            default=None,
            help="Skip a plugin (format: marketplace/plugin). Repeatable.",
        )
        exclude_parser.add_argument(
            "--exclude-skill",
            action="append",
            default=None,
            help="Skip a skill (format: marketplace/plugin/skill). Repeatable.",
        )
        exclude_parser.add_argument(
            "--exclude-agent",
            action="append",
            default=None,
            help="Skip an agent (format: marketplace/plugin/agent.md). Repeatable.",
        )
        exclude_parser.add_argument(
            "--exclude-command",
            action="append",
            default=None,
            help="Skip a command (format: name, scope/name, or marketplace/plugin/name). Repeatable.",
        )
    reconcile_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text.",
    )

    # -- config subcommand group --
    config_parser = subparsers.add_parser(
        "config",
        help="View and manage bridge configuration",
        description="View, validate, and manage bridge configuration.",
    )
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_config_add = config_subparsers.add_parser
    config_subparsers.add_parser = lambda *a, **kw: (  # type: ignore[method-assign]
        kw.setdefault("formatter_class", _AutoWidthHelpFormatter),
        _raw_config_add(*a, **kw),
    )[1]

    config_show_parser = config_subparsers.add_parser(
        "show",
        help="Display effective configuration with source attribution",
        description=(
            "Display the effective bridge configuration. Shows all values "
            "with attribution indicating whether each comes from the global "
            "config, project config, or defaults. When inside a project, "
            "shows merged view by default."
        ),
    )
    config_show_parser.add_argument(
        "--global",
        dest="force_global",
        action="store_true",
        help="Show only global configuration.",
    )
    config_show_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output.",
    )
    config_show_parser.add_argument(
        "--project",
        type=Path,
        help="Target project directory (default: current working directory).",
    )

    config_check_parser = config_subparsers.add_parser(
        "check",
        help="Validate configuration files",
        description=(
            "Validate bridge configuration files. Checks TOML well-formedness, "
            "unknown keys, scan path expansion, and project-level misplacement "
            "of global-only keys. Reports issues found or confirms all checks pass."
        ),
    )
    config_check_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output.",
    )
    config_check_parser.add_argument(
        "--project",
        type=Path,
        help="Target project directory (default: current working directory).",
    )

    # -- config scan subcommand group --
    config_scan_parser = config_subparsers.add_parser(
        "scan",
        help="Manage scan paths for bulk project discovery",
        description=f"Add, remove, or list scan path patterns used by {_c['cmd']('reconcile --all')}.",
    )
    config_scan_subparsers = config_scan_parser.add_subparsers(
        dest="scan_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_scan_add = config_scan_subparsers.add_parser
    config_scan_subparsers.add_parser = lambda *a, **kw: (  # type: ignore[method-assign]
        kw.setdefault("formatter_class", _AutoWidthHelpFormatter),
        _raw_scan_add(*a, **kw),
    )[1]

    scan_add_parser = config_scan_subparsers.add_parser(
        "add",
        help="Add a scan path pattern",
        description=(
            "Add a scan path glob pattern. The pattern is expanded to verify "
            "at least one directory matches, then stored in config.toml."
        ),
    )
    scan_add_parser.add_argument(
        "pattern",
        nargs="?",
        help="Glob pattern matching project directories (interactive prompt if omitted).",
    )

    scan_remove_parser = config_scan_subparsers.add_parser(
        "remove",
        help="Remove a scan path pattern",
        description="Remove a scan path pattern from config.toml.",
    )
    scan_remove_parser.add_argument(
        "pattern",
        nargs="?",
        help="Pattern to remove (interactive selection if omitted).",
    )

    scan_list_parser = config_scan_subparsers.add_parser(
        "list",
        help="List configured scan paths",
        description="Display current scan_paths and exclude_paths from config.toml.",
    )
    scan_list_parser.add_argument(
        "--json", dest="json", action="store_true", help="Output as JSON",
    )

    # -- config exclude subcommands --
    config_exclude_parser = config_subparsers.add_parser(
        "exclude",
        help="Manage sync exclusions",
        description=(
            "Add, remove, or list exclusions for plugins, skills, agents, "
            "and commands. Excluded entities are skipped during reconcile."
        ),
    )
    config_exclude_subparsers = config_exclude_parser.add_subparsers(
        dest="config_exclude_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_config_exclude_add = config_exclude_subparsers.add_parser
    config_exclude_subparsers.add_parser = lambda *a, **kw: (  # type: ignore[method-assign]
        kw.setdefault("formatter_class", _AutoWidthHelpFormatter),
        _raw_config_exclude_add(*a, **kw),
    )[1]

    exclude_add_parser = config_exclude_subparsers.add_parser(
        "add", help="Add an exclusion",
    )
    exclude_add_parser.add_argument(
        "kind", nargs="?", choices=["plugin", "skill", "agent", "command"],
        help="Entity kind to exclude.",
    )
    exclude_add_parser.add_argument("entity_id", nargs="?", help="Entity ID to exclude.")
    exclude_add_parser.add_argument("--global", dest="force_global", action="store_true")
    exclude_add_parser.add_argument("--project", type=Path)
    exclude_add_parser.add_argument("--cache-dir", type=Path)
    exclude_add_parser.add_argument("--claude-home", type=Path)

    exclude_remove_parser = config_exclude_subparsers.add_parser(
        "remove", help="Remove an exclusion",
    )
    exclude_remove_parser.add_argument(
        "kind", nargs="?", choices=["plugin", "skill", "agent", "command"],
    )
    exclude_remove_parser.add_argument("entity_id", nargs="?")
    exclude_remove_parser.add_argument("--global", dest="force_global", action="store_true")
    exclude_remove_parser.add_argument("--project", type=Path)

    exclude_list_parser = config_exclude_subparsers.add_parser(
        "list", help="Show current exclusions",
    )
    exclude_list_parser.add_argument("--global", dest="force_global", action="store_true")
    exclude_list_parser.add_argument("--project", type=Path)
    exclude_list_parser.add_argument(
        "--json", dest="json", action="store_true", help="Output as JSON",
    )

    # -- config log subcommands --
    config_log_parser = config_subparsers.add_parser(
        "log",
        help="Manage log configuration",
        description="Configure activity log settings.",
    )
    config_log_subparsers = config_log_parser.add_subparsers(
        dest="config_log_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_config_log_add = config_log_subparsers.add_parser
    config_log_subparsers.add_parser = lambda *a, **kw: (  # type: ignore[method-assign]
        kw.setdefault("formatter_class", _AutoWidthHelpFormatter),
        _raw_config_log_add(*a, **kw),
    )[1]

    retention_parser = config_log_subparsers.add_parser(
        "set-retention", help="Set log retention period in days",
    )
    retention_parser.add_argument(
        "days", nargs="?", type=int, help="Number of days to retain logs.",
    )

    log_parser = subparsers.add_parser(
        "log",
        help="View and manage the activity log",
        description="View and manage the bridge activity log.",
    )
    log_subparsers = log_parser.add_subparsers(
        dest="log_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_log_add = log_subparsers.add_parser
    log_subparsers.add_parser = lambda *a, **kw: (kw.setdefault("formatter_class", _AutoWidthHelpFormatter), _raw_log_add(*a, **kw))[1]  # type: ignore[method-assign]

    log_show_parser = log_subparsers.add_parser(
        "show",
        help="Display activity log entries",
        description=(
            "Display activity log entries. Shows reconcile, clean, and "
            "autosync install operations with their file-level changes. "
            "Defaults to the last 7 days."
        ),
    )
    log_show_parser.add_argument("--since", type=str, help="Start date, inclusive (YYYY-MM-DD).")
    log_show_parser.add_argument("--until", type=str, help="End date, inclusive (YYYY-MM-DD).")
    log_show_parser.add_argument("--days", type=int, help="Show last N days (default: 7). Cannot combine with --since/--until.")
    log_show_parser.add_argument("--project", type=Path, help="Filter to entries for this project path.")
    log_show_parser.add_argument("--action", type=str, help="Filter by action (reconcile, clean, autosync-install).")
    log_show_parser.add_argument("--type", type=str, help="Filter by change type (create, update, remove).")
    log_show_parser.add_argument("--json", action="store_true", help="Emit raw JSONL instead of formatted table.")

    log_prune_parser = log_subparsers.add_parser(
        "prune",
        help="Delete old log files past the retention period",
        description=(
            "Delete activity log files older than the retention period. "
            "Also runs automatically after every logged operation."
        ),
    )
    log_prune_parser.add_argument("--retention-days", type=int, help="Days to keep (default: from config.toml, typically 90).")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Run environment health checks",
        description=(
            "Run environment health checks. Verifies that the claude CLI is "
            "installed and accessible, plugins are discoverable, the Codex home "
            "directory exists, and the LaunchAgent (if installed) is correctly "
            "configured. Reports pass/warn/fail for each check."
        ),
    )
    doctor_parser.add_argument(
        "--claude-home",
        type=Path,
        help="Claude home directory (default: ~/.claude).",
    )
    doctor_parser.add_argument(
        "--cache-dir",
        type=Path,
        help="Claude plugin cache directory (default: ~/.claude/plugins/cache).",
    )
    doctor_parser.add_argument(
        "--codex-home",
        type=Path,
        help="Codex home directory (default: ~/.codex).",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text.",
    )
    doctor_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="LaunchAgents directory to check (default: ~/Library/LaunchAgents).",
    )

    clean_parser = subparsers.add_parser(
        "clean",
        help="Remove bridge artifacts for one project",
        description=(
            "Remove all bridge-generated Codex artifacts for one project. "
            "Releases the project's ownership of shared global skills, agents, "
            "and prompts — artifacts still owned by other projects are preserved. "
            "Deletes the project's bridge state file."
        ),
    )
    clean_parser.add_argument(
        "--project",
        type=Path,
        help="Target project directory (default: current working directory).",
    )
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything.",
    )
    clean_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text.",
    )
    autosync_parser = subparsers.add_parser(
        "autosync",
        help="Manage automatic background reconciliation (macOS)",
        description=f"Install or remove the macOS LaunchAgent that runs {_c['cmd']('reconcile --all')} on a schedule.",
    )
    autosync_subparsers = autosync_parser.add_subparsers(
        dest="autosync_command", required=True, title="Commands", metavar="COMMAND",
    )
    _raw_autosync_add = autosync_subparsers.add_parser
    autosync_subparsers.add_parser = lambda *a, **kw: (  # type: ignore[method-assign]
        kw.setdefault("formatter_class", _AutoWidthHelpFormatter),
        _raw_autosync_add(*a, **kw),
    )[1]

    autosync_install_parser = autosync_subparsers.add_parser(
        "install",
        help="Set up automatic background sync",
        description=(
            f"Install a macOS LaunchAgent that runs {_c['cmd']('cc-codex-bridge reconcile --all')} "
            "on a recurring interval. Writes the plist to ~/Library/LaunchAgents and "
            "loads it via launchd. Re-running updates the plist and reloads the agent."
        ),
    )
    autosync_install_parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_START_INTERVAL,
        help=f"Reconcile interval in seconds (default: {DEFAULT_START_INTERVAL}).",
    )
    autosync_install_parser.add_argument(
        "--label",
        help=f"LaunchAgent label (default: {GLOBAL_LAUNCHAGENT_LABEL}).",
    )
    autosync_install_parser.add_argument(
        "--python-executable",
        type=Path,
        help="Python interpreter for the LaunchAgent (default: auto-detected).",
    )
    autosync_install_parser.add_argument(
        "--cli-path",
        type=Path,
        help="Path to cc-codex-bridge script (default: auto-detected).",
    )
    autosync_install_parser.add_argument(
        "--logs-dir",
        type=Path,
        help="Directory for LaunchAgent stdout/stderr logs (default: ~/.cc-codex-bridge/logs).",
    )
    autosync_install_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="LaunchAgents destination directory (default: ~/Library/LaunchAgents).",
    )

    autosync_uninstall_parser = autosync_subparsers.add_parser(
        "uninstall",
        help="Stop and remove automatic background sync",
        description=(
            "Unload and remove the bridge LaunchAgent plist from ~/Library/LaunchAgents. "
            "Stops scheduled reconciliation without affecting any reconciled project artifacts."
        ),
    )
    autosync_uninstall_parser.add_argument(
        "--label",
        help=f"LaunchAgent label to remove (default: {GLOBAL_LAUNCHAGENT_LABEL}).",
    )
    autosync_uninstall_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="LaunchAgents directory to search (default: ~/Library/LaunchAgents).",
    )

    autosync_status_parser = autosync_subparsers.add_parser(
        "status",
        help="Show whether automatic background sync is active",
        description="Report whether the bridge LaunchAgent is installed and loaded.",
    )
    autosync_status_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="LaunchAgents directory to check (default: ~/Library/LaunchAgents).",
    )

    upgrade_parser = subparsers.add_parser(
        "upgrade",
        help="Upgrade to the latest release",
        description=(
            "Check for a newer release on GitHub and upgrade in place by "
            "downloading and running the official install script. "
            f"Use {_c['cmd']('--check')} to report the available version without installing."
        ),
    )
    upgrade_parser.add_argument(
        "--check",
        action="store_true",
        help="Report the latest available version without upgrading.",
    )

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="Remove the entire bridge from this machine",
        description=(
            "Remove the entire bridge from this machine. Cleans all registered "
            "projects, removes all global Codex artifacts (skills, agents, prompts, "
            "AGENTS.md), unloads the LaunchAgent if installed, and deletes the "
            "bridge home directory (~/.cc-codex-bridge)."
        ),
    )
    uninstall_parser.add_argument(
        "--codex-home",
        type=Path,
        help="Codex home directory (default: ~/.codex).",
    )
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting anything.",
    )
    uninstall_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON output instead of human-readable text (requires --dry-run).",
    )
    uninstall_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="LaunchAgents directory to scan (default: ~/Library/LaunchAgents).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "config":
        return _handle_config_command(args)

    if args.command == "log":
        return _handle_log_command(args)

    if args.command == "clean":
        return _handle_clean_command(args)

    if args.command == "uninstall":
        return _handle_uninstall_command(args)

    if args.command == "upgrade":
        return _handle_upgrade_command(args)

    if args.command in LAUNCHAGENT_COMMANDS:
        return _handle_launchagent_command(args)

    if args.command in UTILITY_COMMANDS:
        checks = run_doctor(
            cache_dir=args.cache_dir,
            claude_home=args.claude_home,
            codex_home=getattr(args, "codex_home", None),
            launchagents_dir=args.launchagents_dir,
        )
        if args.json:
            print(format_doctor_json(checks))
        else:
            print(format_doctor_report(checks))
        return doctor_exit_code(checks)

    if args.command not in PIPELINE_COMMANDS:
        print(f"Error: unsupported command `{args.command}`", file=sys.stderr)
        return 1

    if getattr(args, "all", False):
        if args.project:
            from cc_codex_bridge._colors import color_fns as _cfns; _ec = _cfns()
            print(f"Error: {_ec['cmd']('--all')} and {_ec['cmd']('--project')} are mutually exclusive", file=sys.stderr)
            return 1
        return _handle_all_command(args)

    if args.command == "reconcile" and args.diff and not args.dry_run:
        from cc_codex_bridge._colors import color_fns as _cfns; _ec = _cfns()
        print(f"Error: {_ec['cmd']('--diff')} requires {_ec['cmd']('--dry-run')} for reconcile", file=sys.stderr)
        return 1

    try:
        build = build_project_desired_state(
            args.project,
            codex_home=getattr(args, "codex_home", None),
            claude_home=args.claude_home,
            cache_dir=args.cache_dir,
            exclude_plugins=getattr(args, "exclude_plugin", None) or (),
            exclude_skills=getattr(args, "exclude_skill", None) or (),
            exclude_agents=getattr(args, "exclude_agent", None) or (),
            exclude_commands=getattr(args, "exclude_command", None) or (),
        )
    except (DiscoveryError, TranslationError, ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # Split diagnostics: agent diagnostics block reconciliation,
    # skill warnings are informational only.
    agent_diags = tuple(
        d for d in build.diagnostics if isinstance(d, AgentTranslationDiagnostic)
    )
    skill_diags = tuple(
        d for d in build.diagnostics if isinstance(d, SkillValidationDiagnostic)
    )

    try:
        if agent_diags:
            if args.command == "status":
                drifted = compute_project_drift(
                    build.discovery.project.root,
                    bridge_home=build.desired_state.bridge_home if build.desired_state else None,
                )
                if args.json:
                    print(format_status_json(
                        None, build.exclusion_report,
                        agent_count=build.agent_count,
                        skill_count=build.skill_count,
                        prompt_count=build.prompt_count,
                        diagnostics=agent_diags, skill_diagnostics=skill_diags,
                        drifted_files=drifted,
                    ))
                else:
                    print(format_status_report(
                        None, build.exclusion_report,
                        agent_count=build.agent_count,
                        skill_count=build.skill_count,
                        prompt_count=build.prompt_count,
                        diagnostics=agent_diags, skill_diagnostics=skill_diags,
                        drifted_files=drifted,
                        discovery=build.discovery,
                        shim_action=build.shim_decision.action,
                    ))
                return 0
            raise TranslationError(format_agent_translation_diagnostics(agent_diags))

        if args.command == "reconcile":
            if args.dry_run:
                report = diff_desired_state(build.desired_state)
            else:
                report = reconcile_desired_state(build.desired_state)
                if report.changes:
                    _log_and_prune(
                        action="reconcile",
                        project=str(build.discovery.project.root),
                        changes=report.changes,
                    )
            if getattr(args, "json", False):
                print(format_reconcile_json(
                    report,
                    build.discovery,
                    build.shim_decision.action,
                    build.agent_count,
                    build.skill_count,
                    build.prompt_count,
                    build.exclusion_report,
                    skill_diagnostics=skill_diags,
                ))
            else:
                _print_summary(
                    build.discovery,
                    build.shim_decision.action,
                    build.agent_count,
                    build.skill_count,
                    build.prompt_count,
                    build.exclusion_report,
                )
                if args.diff:
                    print(format_diff_report(build.desired_state, report))
                else:
                    print(format_change_report(report))
                if skill_diags:
                    print("\nSkill validation warnings:", file=sys.stderr)
                    print(format_skill_validation_diagnostics(skill_diags), file=sys.stderr)
            return 0

        if args.command == "status":
            report = diff_desired_state(build.desired_state)
            drifted = compute_project_drift(
                build.discovery.project.root,
                bridge_home=build.desired_state.bridge_home if build.desired_state else None,
            )
            if args.json:
                print(format_status_json(
                    report, build.exclusion_report,
                    agent_count=build.agent_count,
                    skill_count=build.skill_count,
                    prompt_count=build.prompt_count,
                    skill_diagnostics=skill_diags,
                    drifted_files=drifted,
                ))
            else:
                print(format_status_report(
                    report, build.exclusion_report,
                    agent_count=build.agent_count,
                    skill_count=build.skill_count,
                    prompt_count=build.prompt_count,
                    skill_diagnostics=skill_diags,
                    drifted_files=drifted,
                    discovery=build.discovery,
                    shim_action=build.shim_decision.action,
                ))
            return 0
    except (TranslationError, ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    raise AssertionError(f"Unhandled command dispatch path: {args.command}")


def _log_and_prune(
    *,
    action: str,
    project: str,
    changes: tuple,
) -> None:
    """Write an activity log entry and auto-prune old logs."""
    from cc_codex_bridge.activity_log import build_log_entry_from_changes, write_log_entry, prune_logs
    from cc_codex_bridge.bridge_home import logs_dir, config_path
    from cc_codex_bridge.config import load_config

    try:
        bridge_home = resolve_bridge_home()
        cfg = load_config(config_path(bridge_home=bridge_home))
        entry = build_log_entry_from_changes(action=action, project=project, changes=changes)
        log_dir = logs_dir(bridge_home=bridge_home)
        write_log_entry(entry, logs_dir=log_dir)
        prune_logs(logs_dir=log_dir, retention_days=cfg.log_retention_days)
    except OSError:
        pass  # Logging is best-effort; never fail a successful operation


def _handle_log_command(args: argparse.Namespace) -> int:
    """Handle the log show/prune subcommands."""
    from datetime import date, timedelta
    from cc_codex_bridge.activity_log import (
        read_log_entries, filter_entries, format_log_entries, prune_logs,
    )
    from cc_codex_bridge.bridge_home import logs_dir, config_path
    from cc_codex_bridge.config import load_config

    bridge_home = resolve_bridge_home()
    log_dir = logs_dir(bridge_home=bridge_home)
    cfg = load_config(config_path(bridge_home=bridge_home))

    if args.log_command == "prune":
        if args.retention_days is not None:
            if args.retention_days < 1:
                print("Error: --retention-days must be at least 1", file=sys.stderr)
                return 1
            retention = args.retention_days
        else:
            retention = cfg.log_retention_days
        removed = prune_logs(logs_dir=log_dir, retention_days=retention)
        if removed:
            print(f"Pruned {len(removed)} log file(s).")
            for path in removed:
                print(f"  {path.name}")
        else:
            print("No log files to prune.")
        return 0

    # log show
    if args.days is not None and (args.since or args.until):
        print("Error: --days conflicts with --since/--until", file=sys.stderr)
        return 1

    today = date.today()
    if args.days is not None:
        if args.days < 1:
            print("Error: --days must be at least 1", file=sys.stderr)
            return 1
        since = today - timedelta(days=args.days - 1)
        until = today
    else:
        try:
            if args.since:
                since = date.fromisoformat(args.since)
            elif args.until:
                since = None  # open-ended start when only --until given
            else:
                since = today - timedelta(days=6)
            until = date.fromisoformat(args.until) if args.until else today
        except ValueError as exc:
            print(f"Error: invalid date: {exc}", file=sys.stderr)
            return 1

    entries = read_log_entries(logs_dir=log_dir, since=since, until=until)

    if args.project:
        raw_project = str(args.project)
        project_filter = raw_project if raw_project == "*" else str(Path(args.project).expanduser().resolve())
    else:
        project_filter = None
    entries = filter_entries(
        entries,
        project=project_filter,
        action=args.action,
        change_type=args.type,
    )

    print(format_log_entries(entries, json_output=args.json))
    return 0


def _handle_config_command(args: argparse.Namespace) -> int:
    """Handle all config subcommands."""
    if args.config_command == "show":
        return _handle_config_show(args)
    if args.config_command == "check":
        return _handle_config_check(args)
    if args.config_command == "scan":
        return _handle_config_scan(args)
    if args.config_command == "log":
        return _handle_config_log(args)
    if args.config_command == "exclude":
        return _handle_config_exclude(args)
    print(f"Error: unknown config command `{args.config_command}`", file=sys.stderr)
    return 1


def _handle_config_show(args: argparse.Namespace) -> int:
    """Handle config show."""
    from cc_codex_bridge.config import load_config
    from cc_codex_bridge.config_scope import resolve_config_scope
    from cc_codex_bridge.config_show import format_config_show, format_config_show_json
    from cc_codex_bridge.exclusions import load_project_exclusions
    from cc_codex_bridge.scan import load_scan_config

    bridge_home = resolve_bridge_home()
    scope = resolve_config_scope(
        force_global=getattr(args, "force_global", False),
        project_dir=getattr(args, "project", None) or Path.cwd(),
        bridge_home=bridge_home,
    )

    global_cfg = load_config(bridge_home / "config.toml")

    try:
        scan_cfg = load_scan_config(bridge_home)
    except Exception as exc:
        print(f"Warning: could not load scan config: {exc}", file=sys.stderr)
        scan_cfg = None

    project_exclusions = None
    if scope.target == "project" and scope.project_root:
        try:
            project_exclusions = load_project_exclusions(scope.project_root)
        except Exception as exc:
            print(f"Warning: could not load project exclusions: {exc}", file=sys.stderr)

    display_scope = "global" if scope.target == "global" else "merged"
    if getattr(args, "force_global", False):
        display_scope = "global"

    scan_paths = scan_cfg.scan_paths if scan_cfg else ()
    exclude_paths = scan_cfg.exclude_paths if scan_cfg else ()

    if getattr(args, "json", False):
        print(format_config_show_json(
            global_config=global_cfg,
            project_exclusions=project_exclusions,
            scan_paths=scan_paths,
            exclude_paths=exclude_paths,
            scope=display_scope,
        ))
    else:
        print(format_config_show(
            global_config=global_cfg,
            project_exclusions=project_exclusions,
            scan_paths=scan_paths,
            exclude_paths=exclude_paths,
            scope=display_scope,
        ))
    return 0


def _handle_config_check(args: argparse.Namespace) -> int:
    """Handle config check."""
    from cc_codex_bridge.config_check import (
        check_global_config,
        check_project_config,
        format_check_report,
        format_check_report_json,
    )
    from cc_codex_bridge.config_scope import resolve_config_scope

    bridge_home = resolve_bridge_home()
    global_config_path = bridge_home / "config.toml"
    global_results = check_global_config(global_config_path, bridge_home=bridge_home)

    scope = resolve_config_scope(
        force_global=False,
        project_dir=getattr(args, "project", None) or Path.cwd(),
        bridge_home=bridge_home,
    )
    project_results = None
    if scope.target == "project" and scope.project_root:
        project_results = check_project_config(scope.config_path)

    if getattr(args, "json", False):
        print(format_check_report_json(global_results, project_results or []))
    else:
        print(format_check_report("global", global_results))
        if project_results is not None:
            print()
            print(format_check_report(f"project ({scope.config_path})", project_results))
        total_issues = sum(1 for r in global_results if not r.passed)
        if project_results:
            total_issues += sum(1 for r in project_results if not r.passed)
        print()
        if total_issues:
            print(f"{total_issues} issue(s) found.")
        else:
            print("All checks passed.")

    has_failures = any(not r.passed for r in global_results)
    if project_results:
        has_failures = has_failures or any(not r.passed for r in project_results)
    return 1 if has_failures else 0


def _handle_config_scan(args: argparse.Namespace) -> int:
    """Handle config scan subcommands."""
    from cc_codex_bridge.config_scan_commands import (
        handle_scan_add,
        handle_scan_list,
        handle_scan_remove,
    )
    from cc_codex_bridge import interactive

    bridge_home = resolve_bridge_home()
    cfg_path = bridge_home / "config.toml"

    scan_command = getattr(args, "scan_command", None)

    if scan_command == "add":
        pattern = getattr(args, "pattern", None)
        if pattern is None:
            if not interactive.is_interactive():
                print("Error: pattern required (not running interactively).", file=sys.stderr)
                return 1
            pattern = interactive.prompt_for_value("Scan path pattern: ")
            if pattern is None:
                return 0

        result = handle_scan_add(pattern=pattern, config_path=cfg_path)
        print(result.message)
        return 0 if result.success else 1

    if scan_command == "remove":
        pattern = getattr(args, "pattern", None)
        if pattern is None:
            # Interactive: let user pick from current scan paths.
            current = handle_scan_list(config_path=cfg_path)
            if not current.paths:
                print("No scan paths configured.")
                return 0
            if not interactive.is_interactive():
                print("Error: pattern required (not running interactively).", file=sys.stderr)
                return 1
            pattern = interactive.select_from_list(
                list(current.paths),
                prompt="Select scan path to remove:",
            )
            if pattern is None:
                return 0

        result = handle_scan_remove(pattern=pattern, config_path=cfg_path)
        print(result.message)
        return 0 if result.success else 1

    if scan_command == "list":
        listing = handle_scan_list(config_path=cfg_path)
        if getattr(args, "json", False):
            data = {
                "scan_paths": list(listing.paths),
                "exclude_paths": list(listing.exclude_paths),
            }
            print(json.dumps(data, indent=2))
            return 0
        if listing.paths:
            print("Scan paths:")
            for p in listing.paths:
                print(f"  {p}")
        else:
            print("No scan paths configured.")
        if listing.exclude_paths:
            print("Exclude paths:")
            for p in listing.exclude_paths:
                print(f"  {p}")
        return 0

    print(f"Error: unknown scan command `{scan_command}`", file=sys.stderr)
    return 1


def _handle_config_log(args: argparse.Namespace) -> int:
    """Handle config log subcommands."""
    from cc_codex_bridge import config_writer, interactive
    from cc_codex_bridge.config import DEFAULT_LOG_RETENTION_DAYS

    if getattr(args, "config_log_command", None) != "set-retention":
        print(
            f"Error: unknown config log command `{getattr(args, 'config_log_command', None)}`",
            file=sys.stderr,
        )
        return 1

    bridge_home = resolve_bridge_home()
    cfg_path = bridge_home / "config.toml"

    days = getattr(args, "days", None)
    if days is None:
        if not interactive.is_interactive():
            print("Error: days value required (not running interactively).", file=sys.stderr)
            return 1
        raw = interactive.prompt_for_value("Log retention (days): ")
        if raw is None:
            print("Cancelled.", file=sys.stderr)
            return 1
        try:
            days = int(raw)
        except ValueError:
            print(f"Error: invalid integer: {raw}", file=sys.stderr)
            return 1

    if days < 1:
        print(f"Error: retention days must be >= 1, got {days}.", file=sys.stderr)
        return 1

    data = config_writer.read_config_data(cfg_path)
    log_section = data.get("log", {})
    old = log_section.get("log_retention_days", DEFAULT_LOG_RETENTION_DAYS)

    config_writer.set_nested_value(data, ["log", "log_retention_days"], days)
    config_writer.write_config_data(cfg_path, data)

    print(f"Set log retention to {days} days (was {old}).")
    return 0


def _handle_config_exclude(args: argparse.Namespace) -> int:
    """Handle config exclude add/remove/list subcommands."""
    from cc_codex_bridge import interactive
    from cc_codex_bridge.config_exclude_commands import (
        KIND_TO_KEY,
        handle_exclude_add,
        handle_exclude_list,
        handle_exclude_remove,
        list_discoverable_entities,
    )
    from cc_codex_bridge.config_scope import resolve_config_scope

    bridge_home = resolve_bridge_home()
    scope = resolve_config_scope(
        force_global=getattr(args, "force_global", False),
        project_dir=getattr(args, "project", None),
        bridge_home=bridge_home,
    )

    subcmd = getattr(args, "config_exclude_command", None)

    # -- list --
    if subcmd == "list":
        result = handle_exclude_list(config_path=scope.config_path)
        if getattr(args, "json", False):
            data = {
                key: list(getattr(result, key))
                for key in KIND_TO_KEY.values()
            }
            print(json.dumps(data, indent=2))
            return 0
        any_found = False
        for kind, key in KIND_TO_KEY.items():
            entries = getattr(result, key)
            if entries:
                any_found = True
                print(f"{kind}s:")
                for entry in entries:
                    print(f"  {entry}")
        if not any_found:
            print("No exclusions configured.")
        return 0

    # -- add --
    if subcmd == "add":
        from cc_codex_bridge.discover import discover

        try:
            discovery = discover(
                project_path=scope.project_root,
                cache_dir=getattr(args, "cache_dir", None),
                claude_home=getattr(args, "claude_home", None),
            )
        except Exception as exc:
            print(f"Error: discovery failed: {exc}", file=sys.stderr)
            return 1

        cli_kind = getattr(args, "kind", None)
        entity_id = getattr(args, "entity_id", None)

        # Build candidate lists, filtered by what's already excluded.
        current_excl = handle_exclude_list(config_path=scope.config_path)
        discoverable = list_discoverable_entities(discovery)
        available: dict[str, list[str]] = {}
        for k, key in KIND_TO_KEY.items():
            already = set(getattr(current_excl, key))
            available[k] = [e for e in discoverable.get(k, []) if e not in already]

        # Interactive loop: ESC at entity level goes back to kind selection.
        kind = cli_kind
        while True:
            if kind is None:
                if not interactive.is_interactive():
                    print("Error: kind required (not running interactively).", file=sys.stderr)
                    return 1
                # Only show kinds that have remaining candidates.
                selectable_kinds = [k for k in sorted(KIND_TO_KEY.keys()) if available.get(k)]
                if not selectable_kinds:
                    print("All discoverable entities are already excluded.")
                    return 0
                kind = interactive.select_from_list(
                    selectable_kinds,
                    prompt="Select entity kind:",
                    clear_on_select=True,
                )
                if kind is None:
                    # ESC at kind level → exit
                    return 0

            if entity_id is None:
                candidates = available.get(kind, [])
                if not candidates:
                    print(f"No {kind}s available to exclude (all already excluded).")
                    if cli_kind is not None:
                        return 0
                    kind = None
                    continue
                if not interactive.is_interactive():
                    print("Error: entity_id required (not running interactively).", file=sys.stderr)
                    return 1
                entity_id = interactive.select_from_list(
                    candidates,
                    prompt=f"Select {kind} to exclude:",
                )
                if entity_id is None:
                    if cli_kind is not None:
                        # Kind was provided on CLI, ESC → exit
                        return 0
                    # Kind was interactive, ESC → go back to kind selection
                    kind = None
                    continue

            break

        result = handle_exclude_add(
            kind=kind,
            entity_id=entity_id,
            config_path=scope.config_path,
            discovery=discovery,
        )
        print(result.message)
        return 0 if result.success else 1

    # -- remove --
    if subcmd == "remove":
        kind = getattr(args, "kind", None)
        entity_id = getattr(args, "entity_id", None)

        if kind is None and entity_id is None:
            # Interactive: pick from flattened current exclusions
            current = handle_exclude_list(config_path=scope.config_path)
            flat: list[str] = []
            flat_map: list[tuple[str, str]] = []  # (kind, entity_id)
            for k, key in KIND_TO_KEY.items():
                for entry in getattr(current, key):
                    label = f"{k}: {entry}"
                    flat.append(label)
                    flat_map.append((k, entry))
            if not flat:
                print("No exclusions to remove.")
                return 0
            if not interactive.is_interactive():
                print("Error: kind and entity_id required (not running interactively).", file=sys.stderr)
                return 1
            chosen = interactive.select_from_list(flat, prompt="Select exclusion to remove:")
            if chosen is None:
                return 0
            idx = flat.index(chosen)
            kind, entity_id = flat_map[idx]

        elif kind is not None and entity_id is None:
            # Kind given, pick entity from that kind's exclusions
            current = handle_exclude_list(config_path=scope.config_path)
            key = KIND_TO_KEY.get(kind, kind + "s")
            entries = list(getattr(current, key, ()))
            if not entries:
                print(f"No {kind} exclusions to remove.")
                return 0
            if not interactive.is_interactive():
                print("Error: entity_id required (not running interactively).", file=sys.stderr)
                return 1
            entity_id = interactive.select_from_list(
                entries,
                prompt=f"Select {kind} exclusion to remove:",
            )
            if entity_id is None:
                return 0

        result = handle_exclude_remove(
            kind=kind,
            entity_id=entity_id,
            config_path=scope.config_path,
        )
        print(result.message)
        return 0 if result.success else 1

    print(f"Error: unknown exclude command `{subcmd}`", file=sys.stderr)
    return 1


def _handle_clean_command(args: argparse.Namespace) -> int:
    """Handle the clean command."""
    bridge_home_path = resolve_bridge_home()
    try:
        project_root = resolve_project_root(args.project or Path.cwd()).root
    except (DiscoveryError, OSError, UnicodeError):
        # Discovery failed — try to resolve from bridge state instead
        candidate = Path(args.project or Path.cwd()).resolve()
        state_dir = project_state_dir(candidate, bridge_home=bridge_home_path)
        state_path = state_dir / "state.json"
        if state_path.exists() and not state_path.is_symlink():
            project_root = candidate
        else:
            print(
                f"Error: could not find AGENTS.md or bridge state in: {candidate}",
                file=sys.stderr,
            )
            return 1

    try:
        from cc_codex_bridge.reconcile import clean_project
        report = clean_project(
            project_root,
            bridge_home=bridge_home_path,
            dry_run=args.dry_run,
        )
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    use_json = getattr(args, "json", False)

    if not report.changes:
        if use_json:
            print(format_clean_json(report, project_root=project_root, dry_run=args.dry_run))
        elif report.ownership_released:
            print("Ownership released (no files removed — other projects still reference the artifacts).")
        else:
            print("Nothing to clean.")
        return 0

    if not args.dry_run:
        _log_and_prune(
            action="clean",
            project=str(project_root),
            changes=report.changes,
        )

    if use_json:
        print(format_clean_json(report, project_root=project_root, dry_run=args.dry_run))
    else:
        if args.dry_run:
            print("Dry run — the following would be removed:")
        else:
            print("Cleaned:")
        print(format_change_report(report))
    return 0


def _handle_uninstall_command(args: argparse.Namespace) -> int:
    """Handle the uninstall command."""
    if args.json and not args.dry_run:
        from cc_codex_bridge._colors import color_fns as _cfns; _ec = _cfns()
        print(f"Error: {_ec['cmd']('--json')} requires {_ec['cmd']('--dry-run')} for uninstall", file=sys.stderr)
        return 1

    try:
        from cc_codex_bridge.reconcile import uninstall_all
        report = uninstall_all(
            codex_home=getattr(args, "codex_home", None),
            launchagents_dir=args.launchagents_dir,
            dry_run=args.dry_run,
        )
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(_format_uninstall_json(report))
    else:
        print(_format_uninstall_report(report, dry_run=args.dry_run))

    return 1 if report.has_errors else 0


def _handle_all_command(args: argparse.Namespace) -> int:
    """Handle --all mode for reconcile, validate, and status commands."""
    # status always runs in dry-run mode
    dry_run = True if args.command == "status" else getattr(args, "dry_run", False)
    use_json = getattr(args, "json", False)

    try:
        from cc_codex_bridge.reconcile import reconcile_all
        report = reconcile_all(
            codex_home=getattr(args, "codex_home", None),
            claude_home=getattr(args, "claude_home", None),
            cache_dir=getattr(args, "cache_dir", None),
            exclude_plugins=getattr(args, "exclude_plugin", None) or (),
            exclude_skills=getattr(args, "exclude_skill", None) or (),
            exclude_agents=getattr(args, "exclude_agent", None) or (),
            exclude_commands=getattr(args, "exclude_command", None) or (),
            dry_run=dry_run,
        )
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not dry_run:
        for r in report.results:
            if r.report.changes:
                _log_and_prune(
                    action="reconcile",
                    project=str(r.project_root),
                    changes=r.report.changes,
                )

    if use_json:
        print(_format_all_json(report))
    else:
        print(_format_all_report(report, dry_run=dry_run))

    has_errors = len(report.errors) > 0
    return 1 if has_errors else 0


def _format_all_json(report) -> str:
    """Render --all report as JSON, including scan info when available."""
    payload: dict[str, object] = {}

    if report.scan_result is not None:
        payload["scan"] = {
            "bridgeable": [str(p) for p in report.scan_result.bridgeable],
            "not_bridgeable": [
                {"path": str(c.path), "reason": c.filter_reason or c.status}
                for c in report.scan_result.not_bridgeable
            ],
            "filtered": [
                {"path": str(c.path), "reason": c.filter_reason or c.status}
                for c in report.scan_result.filtered
            ],
        }

    payload["projects"] = [
        {
            "root": str(r.project_root),
            "changes": len(r.report.changes),
            "applied": r.report.applied,
        }
        for r in report.results
    ]
    payload["errors"] = [
        {
            "root": str(e.project_root),
            "error": e.error,
        }
        for e in report.errors
    ]
    return json.dumps(payload, indent=2, sort_keys=True)


def _format_all_report(report, *, dry_run: bool = False) -> str:
    """Render --all report as human-readable text, including scan summary."""
    lines: list[str] = []

    if dry_run:
        lines.append("Dry run — no changes applied.")
        lines.append("")

    # Scan summary (only when scan config exists and produced results)
    scan = report.scan_result
    if scan is not None and (scan.bridgeable or scan.not_bridgeable or scan.filtered):
        total = len(scan.bridgeable) + len(scan.not_bridgeable) + len(scan.filtered)
        lines.append(
            f"Scan: {total} candidates, "
            f"{len(scan.bridgeable)} bridgeable, "
            f"{len(scan.not_bridgeable)} not bridgeable, "
            f"{len(scan.filtered)} filtered"
        )
        for c in scan.filtered:
            lines.append(f"  SKIP: {c.path} ({c.filter_reason})")
        for c in scan.not_bridgeable:
            lines.append(f"  NOTE: {c.path} ({c.filter_reason})")
        lines.append("")

    for r in report.results:
        change_count = len(r.report.changes)
        if change_count:
            lines.append(f"OK: {r.project_root} ({change_count} change{'s' if change_count != 1 else ''})")
        else:
            lines.append(f"OK: {r.project_root} (no changes)")

    for e in report.errors:
        lines.append(f"ERROR: {e.project_root} — {e.error}")

    has_scan_output = (
        scan is not None
        and (scan.bridgeable or scan.not_bridgeable or scan.filtered)
    )
    if not report.results and not report.errors and not has_scan_output:
        lines.append("No registered projects.")

    return "\n".join(lines)


def _format_uninstall_json(report) -> str:
    """Render uninstall report as JSON."""
    payload = {
        "projects": [
            {
                "root": str(result.root),
                "status": "will_clean" if result.status == "cleaned" else result.status,
                "removals": [str(c.path) for c in result.changes],
                **({"skip_reason": result.skip_reason} if result.skip_reason else {}),
            }
            for result in report.projects
        ],
        "global": {
            "skills": [
                str(c.path) for c in report.global_removals
                if c.resource_kind == "skill"
            ],
            "agents": [
                str(c.path) for c in report.global_removals
                if c.resource_kind == "agent"
            ],
            "agents_md": next(
                (str(c.path) for c in report.global_removals
                 if c.resource_kind == "global_instructions"),
                None,
            ),
            "registry": next(
                (str(c.path) for c in report.global_removals
                 if not c.resource_kind),
                None,
            ),
        },
        "launchagents": [
            {
                "path": str(removal.path),
                "bootout_command": removal.bootout_command,
            }
            for removal in report.launchagent_removals
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _format_uninstall_report(report, *, dry_run: bool = False) -> str:
    """Render uninstall report as human-readable text."""
    lines: list[str] = []

    if dry_run:
        lines.append("Dry run — the following would be removed:")
        lines.append("")

    for result in report.projects:
        lines.append(f"--- Project: {result.root} ---")
        if result.status == "skipped":
            lines.append(f"SKIPPED: {result.skip_reason}")
        elif result.status == "no_state":
            lines.append("NO_STATE: no bridge state file found")
        else:
            for change in result.changes:
                suffix = f" ({change.resource_kind})" if change.resource_kind else ""
                lines.append(f"REMOVE: {change.path}{suffix}")
            if not result.changes:
                lines.append("Ownership released (no files removed).")
        lines.append("")

    if report.global_removals or report.launchagent_removals:
        lines.append("--- Global ---")
        for change in report.global_removals:
            suffix = f" ({change.resource_kind})" if change.resource_kind else ""
            lines.append(f"REMOVE: {change.path}{suffix}")
        lines.append("")

    if report.launchagent_removals:
        lines.append("--- LaunchAgents ---")
        for removal in report.launchagent_removals:
            lines.append(f"REMOVE: {removal.path}")
            lines.append(f"BOOTOUT: {removal.bootout_command}")
        lines.append("")

    if not report.projects and not report.global_removals and not report.launchagent_removals:
        lines.append("Nothing to uninstall.")

    # Add trailing summary when there are projects to report on
    if report.projects:
        cleaned = sum(1 for r in report.projects if r.status == "cleaned")
        skipped = sum(1 for r in report.projects if r.status == "skipped")
        no_state = sum(1 for r in report.projects if r.status == "no_state")
        cleaned_label = "will_clean" if dry_run else "cleaned"
        parts = [f"{cleaned} {cleaned_label}"]
        if skipped:
            parts.append(f"{skipped} skipped")
        if no_state:
            parts.append(f"{no_state} no state")
        lines.append(f"Summary: {', '.join(parts)}.")

    return "\n".join(lines).rstrip()


def _handle_launchagent_command(args: argparse.Namespace) -> int:
    """Handle autosync install / uninstall / status subcommands."""
    from cc_codex_bridge._colors import color_fns
    c = color_fns()

    sub = args.autosync_command

    if sub == "uninstall":
        label = args.label or GLOBAL_LAUNCHAGENT_LABEL
        removed = uninstall_launchagent(label, launchagents_dir=args.launchagents_dir)
        if removed is None:
            print(c["warn"]("Autosync is not installed."))
            return 1
        print(f"{c['good']('Removed:')} {removed}")
        return 0

    if sub == "status":
        from cc_codex_bridge.install_launchagent import DEFAULT_LAUNCHAGENTS_DIR as _LA_DIR
        import subprocess
        la_dir = Path(args.launchagents_dir or _LA_DIR).expanduser().resolve()
        plist_path = la_dir / f"{GLOBAL_LAUNCHAGENT_LABEL}.plist"
        print()
        if not plist_path.exists():
            print(f"{c['warn']('AUTOSYNC:')} not installed")
            return 0
        result = subprocess.run(
            ["launchctl", "list", GLOBAL_LAUNCHAGENT_LABEL],
            capture_output=True, text=True,
        )
        loaded = result.returncode == 0
        status_s = c["good"]("installed and loaded") if loaded else c["warn"]("installed, not loaded")
        print(f"{c['key']('AUTOSYNC:')} {status_s}")
        print(f"  {plist_path}")
        return 0

    # sub == "install"
    try:
        label = args.label or GLOBAL_LAUNCHAGENT_LABEL
        plist_bytes = build_global_launchagent_plist(
            interval_seconds=args.interval,
            python_executable=args.python_executable,
            cli_path=args.cli_path,
            label=label,
            logs_dir=args.logs_dir,
        )
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    from cc_codex_bridge.reconcile import Change
    from cc_codex_bridge.install_launchagent import DEFAULT_LAUNCHAGENTS_DIR as _LA_DIR
    la_root = Path(args.launchagents_dir or _LA_DIR).expanduser().resolve()
    la_existed = (la_root / f"{label}.plist").exists()
    destination = install_launchagent(
        plist_bytes,
        label=label,
        launchagents_dir=args.launchagents_dir,
    )
    _log_and_prune(
        action="autosync-install",
        project="*",
        changes=(Change(kind="update" if la_existed else "create", path=destination, resource_kind="launchagent"),),
    )
    print()
    print(f"{c['good']('Installed and loaded:')} {destination}")
    print(f"  Runs {c['cmd']('reconcile --all')} every {args.interval}s")

    # Warn about stale per-project plists
    la_dir = args.launchagents_dir if hasattr(args, "launchagents_dir") and args.launchagents_dir else None
    existing_plists = find_bridge_launchagents(launchagents_dir=la_dir)
    per_project_plists = [p for p in existing_plists if p != destination]
    if per_project_plists:
        print("")
        print(c["warn"]("WARNING: Found existing per-project LaunchAgent plists."))
        print(f"These are no longer needed with the global {c['cmd']('reconcile --all')} plist.")
        print("Remove them with:")
        for plist_path in per_project_plists:
            print(f"  {c['cmd'](f'launchctl bootout gui/$(id -u) {plist_path} && rm {plist_path}')}")

    return 0


def _print_summary(
    result,
    shim_action: str,
    agent_count: int,
    skill_count: int,
    prompt_count: int,
    exclusion_report: ExclusionReport,
) -> None:
    """Print a human-readable discovery summary."""
    from cc_codex_bridge import __version__

    c = _status_color_fns()
    # Project metadata
    print()
    print(f"{c['key']('VERSION:')} v{__version__}")
    print(f"{c['key']('PROJECT_ROOT:')} {result.project.root}")
    print(f"{c['key']('AGENTS_MD:')} {result.project.agents_md_path}")
    print(f"{c['key']('CLAUDE_MD_ACTION:')} {shim_action}")
    # Plugin discovery
    print()
    print(f"{c['key']('PLUGINS_FOUND:')} {len(result.plugins)}")
    for plugin in result.plugins:
        print(f"  {plugin.marketplace}/{plugin.plugin_name}@{plugin.version_text}")
        print(f"    source  = {plugin.source_path}")
        print(f"    skills  = {len(plugin.skills)}")
        print(f"    agents  = {len(plugin.agents)}")
        print(f"    prompts = {len(plugin.commands)}")
    # Translation output
    print()
    print(f"{c['key']('GENERATED_AGENTS:')} {agent_count}")
    print(f"{c['key']('GENERATED_SKILLS:')} {skill_count}")
    print(f"{c['key']('TRANSLATED_PROMPTS:')} {prompt_count}")
    # Exclusions
    print()
    print(f"{c['key']('EXCLUDED_PLUGINS:')} {len(exclusion_report.plugins)}")
    for plugin_id in exclusion_report.plugins:
        print(f"  {c['dim'](plugin_id)}")
    print(f"{c['key']('EXCLUDED_SKILLS:')} {len(exclusion_report.skills)}")
    for skill_id in exclusion_report.skills:
        print(f"  {c['dim'](skill_id)}")
    print(f"{c['key']('EXCLUDED_AGENTS:')} {len(exclusion_report.agents)}")
    for agent_id in exclusion_report.agents:
        print(f"  {c['dim'](agent_id)}")
    print(f"{c['key']('EXCLUDED_COMMANDS:')} {len(exclusion_report.commands)}")
    for command_id in exclusion_report.commands:
        print(f"  {c['dim'](command_id)}")


def _build_status_payload(
    report,
    exclusion_report: ExclusionReport,
    *,
    agent_count: int = 0,
    skill_count: int = 0,
    prompt_count: int = 0,
    diagnostics=None,
    skill_diagnostics=None,
    drifted_files: list[str] | None = None,
) -> dict[str, object]:
    """Build a stable status payload from reconcile diff output."""
    drifted = sorted(drifted_files or [])
    categorized_changes: dict[str, dict[str, list[str]]] = {
        "project_files": {"create": [], "update": [], "remove": []},
        "skills": {"create": [], "update": [], "remove": []},
        "agents": {"create": [], "update": [], "remove": []},
        "prompts": {"create": [], "update": [], "remove": []},
        "global": {"create": [], "update": [], "remove": []},
    }
    pending_change_count = 0
    status = "invalid" if diagnostics else "in_sync"

    if diagnostics:
        rendered_diagnostics = [
            {
                "kind": "unsupported_agent_tools",
                "source_path": str(diagnostic.source_path),
                "agent_name": diagnostic.agent_name,
                "unsupported_tools": list(diagnostic.unsupported_tools),
                "message": format_agent_translation_diagnostics((diagnostic,)),
            }
            for diagnostic in diagnostics
        ]
    else:
        rendered_diagnostics = []
        for change in report.changes:
            if change.resource_kind in ("skill", "project_skill"):
                category = "skills"
            elif change.resource_kind == "agent":
                category = "agents"
            elif change.resource_kind == "prompt":
                category = "prompts"
            elif change.resource_kind in ("global_instructions", "state", "plugin_resource"):
                category = "global"
            else:
                category = "project_files"
            kind_list = categorized_changes[category].get(change.kind)
            if kind_list is not None:
                kind_list.append(str(change.path))
        pending_change_count = len(report.changes) + len(drifted)
        status = "in_sync" if pending_change_count == 0 else "pending_changes"

    rendered_skill_warnings = [
        {
            "kind": "skill_validation",
            "source_path": str(d.source_path),
            "skill_name": d.skill_name,
            "warnings": list(d.warnings),
            "message": format_skill_validation_diagnostics((d,)),
        }
        for d in (skill_diagnostics or ())
    ]

    from cc_codex_bridge import __version__

    return {
        "agent_count": agent_count,
        "skill_count": skill_count,
        "prompt_count": prompt_count,
        "version": __version__,
        "status": status,
        "pending_change_count": pending_change_count,
        "categorized_changes": categorized_changes,
        "diagnostics": rendered_diagnostics,
        "skill_warnings": rendered_skill_warnings,
        "excluded": {
            "plugins": list(exclusion_report.plugins),
            "skills": list(exclusion_report.skills),
            "agents": list(exclusion_report.agents),
            "commands": list(exclusion_report.commands),
        },
        "drifted_files": drifted,
    }


def format_status_json(
    report, exclusion_report: ExclusionReport,
    *, agent_count: int = 0, skill_count: int = 0, prompt_count: int = 0,
    diagnostics=None, skill_diagnostics=None,
    drifted_files: list[str] | None = None,
) -> str:
    """Render status output as deterministic JSON."""
    return json.dumps(
        _build_status_payload(
            report, exclusion_report,
            agent_count=agent_count, skill_count=skill_count,
            prompt_count=prompt_count,
            diagnostics=diagnostics, skill_diagnostics=skill_diagnostics,
            drifted_files=drifted_files,
        ),
        indent=2,
        sort_keys=True,
    )


from cc_codex_bridge._colors import color_fns as _status_color_fns


def format_status_report(
    report, exclusion_report: ExclusionReport,
    *, agent_count: int = 0, skill_count: int = 0, prompt_count: int = 0,
    diagnostics=None, skill_diagnostics=None,
    drifted_files: list[str] | None = None,
    discovery=None,
    shim_action: str | None = None,
) -> str:
    """Render status output as human-readable text."""
    payload = _build_status_payload(
        report, exclusion_report,
        agent_count=agent_count, skill_count=skill_count,
        prompt_count=prompt_count,
        diagnostics=diagnostics, skill_diagnostics=skill_diagnostics,
        drifted_files=drifted_files,
    )
    c = _status_color_fns()
    categorized = payload["categorized_changes"]
    project_files = categorized["project_files"]
    skills = categorized["skills"]
    agents = categorized["agents"]
    prompts = categorized["prompts"]
    global_changes = categorized["global"]

    # Pad key+colon before coloring so ANSI codes don't affect ljust width.
    _W = len("TRANSLATED_PROMPTS:")  # 19 — longest summary key
    def _k(key: str) -> str:
        return c["key"](f"{key}:".ljust(_W))

    status_val = payload['status']
    if status_val == "in_sync":
        colored_status = c["good"](status_val)
    elif status_val == "pending_changes":
        colored_status = c["warn"](status_val)
    else:
        colored_status = c["bad"](status_val)

    pending = payload['pending_change_count']
    colored_pending = c["warn"](str(pending)) if pending > 0 else str(pending)

    def _counts(cat):
        n_create = len(cat["create"])
        n_update = len(cat["update"])
        n_remove = len(cat["remove"])
        create_s = c["create"](f"create = {n_create}") if n_create else f"create = {n_create}"
        update_s = c["update"](f"update = {n_update}") if n_update else f"update = {n_update}"
        remove_s = c["remove"](f"remove = {n_remove}") if n_remove else f"remove = {n_remove}"
        return f"{create_s} {update_s} {remove_s}"

    # Group 1: project metadata
    lines = [""]
    lines.append(f"{_k('VERSION')} v{payload['version']}")
    if discovery is not None:
        lines.append(f"{_k('PROJECT_ROOT')} {discovery.project.root}")
        lines.append(f"{_k('AGENTS_MD')} {discovery.project.agents_md_path}")
        if shim_action is not None:
            lines.append(f"{_k('CLAUDE_MD_ACTION')} {shim_action}")
    lines.append(f"{_k('STATUS')} {colored_status}")

    # Group 2: plugin discovery
    if discovery is not None:
        lines.append("")
        lines.append(f"{_k('PLUGINS_FOUND')} {len(discovery.plugins)}")
        for plugin in discovery.plugins:
            lines.append(f"  {plugin.marketplace}/{plugin.plugin_name}@{plugin.version_text}")
            lines.append(f"    source  = {plugin.source_path}")
            lines.append(f"    skills  = {len(plugin.skills)}")
            lines.append(f"    agents  = {len(plugin.agents)}")
            lines.append(f"    prompts = {len(plugin.commands)}")

    # Group 3: translation output
    lines.append("")
    lines.append(f"{_k('GENERATED_AGENTS')} {payload['agent_count']}")
    lines.append(f"{_k('GENERATED_SKILLS')} {payload['skill_count']}")
    lines.append(f"{_k('TRANSLATED_PROMPTS')} {payload['prompt_count']}")

    # Group 4: pending changes
    lines.append("")
    lines.append(f"{_k('PENDING_CHANGES')} {colored_pending}")
    lines.append(f"{_k('PROJECT_FILES')} {_counts(project_files)}")
    lines.append(f"{_k('SKILLS')} {_counts(skills)}")
    lines.append(f"{_k('AGENTS')} {_counts(agents)}")
    lines.append(f"{_k('PROMPTS')} {_counts(prompts)}")
    lines.append(f"{_k('GLOBAL')} {_counts(global_changes)}")
    for diagnostic in payload["diagnostics"]:
        lines.append(f"{c['bad']('DIAGNOSTIC:')} {diagnostic['message']}")
    for warning in payload["skill_warnings"]:
        lines.append(f"{c['key']('SKILL_WARNING:')} {c['warn'](warning['message'])}")
    for path in project_files["create"]:
        lines.append(f"{c['key']('PROJECT_FILE_CREATE:')} {c['create'](path)}")
    for path in project_files["update"]:
        lines.append(f"{c['key']('PROJECT_FILE_UPDATE:')} {c['update'](path)}")
    for path in project_files["remove"]:
        lines.append(f"{c['key']('PROJECT_FILE_REMOVE:')} {c['remove'](path)}")
    for path in skills["create"]:
        lines.append(f"{c['key']('SKILL_CREATE:')} {c['create'](path)}")
    for path in skills["update"]:
        lines.append(f"{c['key']('SKILL_UPDATE:')} {c['update'](path)}")
    for path in skills["remove"]:
        lines.append(f"{c['key']('SKILL_REMOVE:')} {c['remove'](path)}")
    for path in agents["create"]:
        lines.append(f"{c['key']('AGENT_CREATE:')} {c['create'](path)}")
    for path in agents["update"]:
        lines.append(f"{c['key']('AGENT_UPDATE:')} {c['update'](path)}")
    for path in agents["remove"]:
        lines.append(f"{c['key']('AGENT_REMOVE:')} {c['remove'](path)}")
    for path in prompts["create"]:
        lines.append(f"{c['key']('PROMPT_CREATE:')} {c['create'](path)}")
    for path in prompts["update"]:
        lines.append(f"{c['key']('PROMPT_UPDATE:')} {c['update'](path)}")
    for path in prompts["remove"]:
        lines.append(f"{c['key']('PROMPT_REMOVE:')} {c['remove'](path)}")
    for path in global_changes["create"]:
        lines.append(f"{c['key']('GLOBAL_CREATE:')} {c['create'](path)}")
    for path in global_changes["update"]:
        lines.append(f"{c['key']('GLOBAL_UPDATE:')} {c['update'](path)}")
    for path in global_changes["remove"]:
        lines.append(f"{c['key']('GLOBAL_REMOVE:')} {c['remove'](path)}")
    drifted = payload.get("drifted_files", [])
    if drifted:
        lines.append(f"{_k('DRIFTED_FILES')} {c['warn'](str(len(drifted)))}")
        for path in drifted:
            lines.append(f"  {c['warn'](path)}")

    # Group 5: exclusions
    lines.append("")
    lines.append(f"{_k('EXCLUDED_PLUGINS')} {len(payload['excluded']['plugins'])}")
    for plugin_id in payload["excluded"]["plugins"]:
        lines.append(f"  {c['dim'](plugin_id)}")
    lines.append(f"{_k('EXCLUDED_SKILLS')} {len(payload['excluded']['skills'])}")
    for skill_id in payload["excluded"]["skills"]:
        lines.append(f"  {c['dim'](skill_id)}")
    lines.append(f"{_k('EXCLUDED_AGENTS')} {len(payload['excluded']['agents'])}")
    for agent_id in payload["excluded"]["agents"]:
        lines.append(f"  {c['dim'](agent_id)}")
    lines.append(f"{_k('EXCLUDED_COMMANDS')} {len(payload['excluded']['commands'])}")
    for command_id in payload["excluded"]["commands"]:
        lines.append(f"  {c['dim'](command_id)}")

    return "\n".join(lines)


def format_reconcile_json(
    report,
    discovery_result,
    shim_action: str,
    agent_count: int,
    skill_count: int,
    prompt_count: int,
    exclusion_report: ExclusionReport,
    *,
    skill_diagnostics=None,
) -> str:
    """Render single-project reconcile output as deterministic JSON."""
    from cc_codex_bridge import __version__

    changes = [
        {
            "kind": c.kind,
            "path": str(c.path),
            "resource_kind": c.resource_kind,
        }
        for c in report.changes
    ]

    payload: dict[str, object] = {
        "version": __version__,
        "project_root": str(discovery_result.project.root),
        "applied": report.applied,
        "change_count": len(report.changes),
        "changes": changes,
        "plugin_count": len(discovery_result.plugins),
        "agent_count": agent_count,
        "skill_count": skill_count,
        "prompt_count": prompt_count,
        "excluded": {
            "plugins": list(exclusion_report.plugins),
            "skills": list(exclusion_report.skills),
            "agents": list(exclusion_report.agents),
            "commands": list(exclusion_report.commands),
        },
        "skill_warnings": [
            {
                "kind": "skill_validation",
                "source_path": str(d.source_path),
                "skill_name": d.skill_name,
                "warnings": list(d.warnings),
                "message": format_skill_validation_diagnostics((d,)),
            }
            for d in (skill_diagnostics or ())
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def format_clean_json(
    report,
    *,
    project_root: Path,
    dry_run: bool = False,
) -> str:
    """Render clean output as deterministic JSON."""
    from cc_codex_bridge import __version__

    changes = [
        {
            "kind": c.kind,
            "path": str(c.path),
            "resource_kind": c.resource_kind,
        }
        for c in report.changes
    ]

    payload: dict[str, object] = {
        "version": __version__,
        "project_root": str(project_root),
        "dry_run": dry_run,
        "applied": report.applied,
        "change_count": len(report.changes),
        "changes": changes,
        "ownership_released": report.ownership_released,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


if __name__ == "__main__":
    sys.exit(main())
