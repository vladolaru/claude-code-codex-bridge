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

from cc_codex_bridge.claude_shim import plan_claude_shim
from cc_codex_bridge.discover import discover, resolve_project_root
from cc_codex_bridge.doctor import (
    doctor_exit_code,
    format_doctor_json,
    format_doctor_report,
    run_doctor,
)
from cc_codex_bridge.exclusions import (
    ExclusionReport,
    apply_sync_exclusions,
    load_project_exclusions,
    resolve_effective_exclusions,
)
from cc_codex_bridge.install_launchagent import (
    DEFAULT_START_INTERVAL,
    build_launchagent_label,
    build_launchagent_plist,
    install_launchagent,
)
from cc_codex_bridge.model import DiscoveryError, ReconcileError, TranslationError
from cc_codex_bridge.reconcile import (
    build_desired_state,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from cc_codex_bridge.render_codex_config import render_inline_codex_config, render_prompt_files
from cc_codex_bridge.translate_agents import (
    format_agent_translation_diagnostics,
    translate_installed_agents_with_diagnostics,
    translate_standalone_agents,
)
from cc_codex_bridge.translate_skills import translate_installed_skills, translate_standalone_skills


PIPELINE_COMMANDS = {"reconcile", "validate", "status"}
LAUNCHAGENT_COMMANDS = {"print-launchagent", "install-launchagent"}
UTILITY_COMMANDS = {"doctor"}


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project",
        type=Path,
        help="Project path to resolve instead of the current working directory.",
    )
    common.add_argument(
        "--cache-dir",
        type=Path,
        help="Override the Claude plugin cache path (mainly for testing).",
    )
    common.add_argument(
        "--claude-home",
        type=Path,
        help="Override the Claude home path (~/.claude) for discovery.",
    )
    common.add_argument(
        "--codex-home",
        type=Path,
        help="Override the Codex home path (mainly for testing).",
    )

    parser = argparse.ArgumentParser(
        description="Generate Codex bridge artifacts from installed Claude Code plugins.",
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    reconcile_parser = subparsers.add_parser("reconcile", parents=[common])
    reconcile_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute reconcile changes without writing.",
    )
    reconcile_parser.add_argument(
        "--diff",
        action="store_true",
        help="Include unified text diffs for managed .md/.toml/.json files (requires --dry-run).",
    )
    validate_parser = subparsers.add_parser("validate", parents=[common])
    status_parser = subparsers.add_parser("status", parents=[common])
    clean_parser = subparsers.add_parser("clean", parents=[common])
    clean_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be removed without deleting anything.",
    )
    uninstall_parser = subparsers.add_parser("uninstall", parents=[common])
    uninstall_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be removed without deleting anything.",
    )
    uninstall_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON output (dry-run only).",
    )
    uninstall_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="Override the LaunchAgents directory to scan.",
    )
    doctor_parser = subparsers.add_parser("doctor", parents=[common])
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit doctor results as JSON instead of human-readable text.",
    )
    doctor_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="Override the LaunchAgents directory checked by doctor.",
    )
    for pipeline_parser in (reconcile_parser, validate_parser, status_parser):
        pipeline_parser.add_argument(
            "--exclude-plugin",
            action="append",
            default=None,
            help="Exclude one plugin (`marketplace/plugin`) from sync. Repeatable.",
        )
        pipeline_parser.add_argument(
            "--exclude-skill",
            action="append",
            default=None,
            help="Exclude one skill (`marketplace/plugin/skill`) from sync. Repeatable.",
        )
        pipeline_parser.add_argument(
            "--exclude-agent",
            action="append",
            default=None,
            help="Exclude one agent (`marketplace/plugin/agent.md`) from sync. Repeatable.",
        )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit status as JSON instead of human-readable text.",
    )

    launchagent_common = argparse.ArgumentParser(add_help=False)
    launchagent_common.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_START_INTERVAL,
        help="LaunchAgent StartInterval in seconds.",
    )
    launchagent_common.add_argument(
        "--label",
        help="Override the generated LaunchAgent label.",
    )
    launchagent_common.add_argument(
        "--python-executable",
        type=Path,
        help="Override the Python executable used by the LaunchAgent.",
    )
    launchagent_common.add_argument(
        "--cli-path",
        type=Path,
        help="Override the CLI script path used by the LaunchAgent.",
    )
    launchagent_common.add_argument(
        "--logs-dir",
        type=Path,
        help="Override the LaunchAgent log directory.",
    )

    subparsers.add_parser("print-launchagent", parents=[common, launchagent_common])
    install_parser = subparsers.add_parser("install-launchagent", parents=[common, launchagent_common])
    install_parser.add_argument(
        "--launchagents-dir",
        type=Path,
        help="Override the LaunchAgents destination directory.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "clean":
        return _handle_clean_command(args)

    if args.command == "uninstall":
        return _handle_uninstall_command(args)

    if args.command in LAUNCHAGENT_COMMANDS:
        return _handle_launchagent_command(args)

    if args.command in UTILITY_COMMANDS:
        checks = run_doctor(
            cache_dir=args.cache_dir,
            claude_home=args.claude_home,
            codex_home=args.codex_home,
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
    if args.command == "reconcile" and args.diff and not args.dry_run:
        print("Error: --diff requires --dry-run for reconcile", file=sys.stderr)
        return 1

    try:
        result = discover(
            project_path=args.project,
            cache_dir=args.cache_dir,
            claude_home=args.claude_home,
        )
        config_exclusions = load_project_exclusions(result.project.root)
        exclusions = resolve_effective_exclusions(
            config_exclusions,
            cli_exclude_plugins=args.exclude_plugin,
            cli_exclude_skills=args.exclude_skill,
            cli_exclude_agents=args.exclude_agent,
        )
        result, exclusion_report = apply_sync_exclusions(result, exclusions)
        shim_decision = plan_claude_shim(result.project)
    except (DiscoveryError, TranslationError, ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        # Plugin agents
        agent_result = translate_installed_agents_with_diagnostics(result.plugins)

        # Standalone agents
        user_agent_result = translate_standalone_agents(result.user_agents, scope="user")
        project_agent_result = translate_standalone_agents(result.project_agents, scope="project")

        # Merge diagnostics from all sources
        all_diagnostics = (
            *agent_result.diagnostics,
            *user_agent_result.diagnostics,
            *project_agent_result.diagnostics,
        )
        if all_diagnostics:
            if args.command == "status":
                if args.json:
                    print(format_status_json(None, exclusion_report, diagnostics=all_diagnostics))
                else:
                    print(format_status_report(None, exclusion_report, diagnostics=all_diagnostics))
                return 0
            raise TranslationError(format_agent_translation_diagnostics(all_diagnostics))

        # Merge roles from all sources
        all_roles = (*agent_result.roles, *user_agent_result.roles, *project_agent_result.roles)

        # Plugin skills + user skills → global registry
        plugin_skills = translate_installed_skills(result.plugins)
        user_skills = translate_standalone_skills(result.user_skills, scope="user")
        all_global_skills = (*plugin_skills, *user_skills)

        # Project skills → project file entries
        project_skills = translate_standalone_skills(result.project_skills, scope="project")
        extra_project_files: list[tuple[Path, bytes]] = []
        for gen_skill in project_skills:
            for f in gen_skill.files:
                rel = Path(".codex") / "skills" / gen_skill.install_dir_name / f.relative_path
                extra_project_files.append((rel, f.content))

        prompt_files = render_prompt_files(all_roles)
        rendered_config = render_inline_codex_config(all_roles)
        total_skill_count = len(all_global_skills) + len(project_skills)
        desired_state = build_desired_state(
            result,
            shim_decision,
            prompt_files,
            rendered_config,
            all_global_skills,
            codex_home=args.codex_home,
            extra_project_files=extra_project_files,
        )
    except (TranslationError, ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "validate":
        _print_summary(
            result,
            shim_decision.action,
            len(all_roles),
            len(prompt_files),
            total_skill_count,
            rendered_config,
            exclusion_report,
        )
        return 0

    try:
        if args.command == "reconcile":
            if args.dry_run:
                report = diff_desired_state(desired_state)
            else:
                report = reconcile_desired_state(desired_state)
            _print_summary(
                result,
                shim_decision.action,
                len(all_roles),
                len(prompt_files),
                total_skill_count,
                rendered_config,
                exclusion_report,
            )
            if args.diff:
                print(format_diff_report(desired_state, report))
            else:
                print(format_change_report(report))
            return 0

        if args.command == "status":
            report = diff_desired_state(desired_state)
            if args.json:
                print(format_status_json(report, exclusion_report))
            else:
                print(format_status_report(report, exclusion_report))
            return 0
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    raise AssertionError(f"Unhandled command dispatch path: {args.command}")


def _handle_clean_command(args: argparse.Namespace) -> int:
    """Handle the clean command."""
    try:
        project_root = resolve_project_root(args.project or Path.cwd()).root
    except (DiscoveryError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        from cc_codex_bridge.reconcile import clean_project
        report = clean_project(
            project_root,
            codex_home=args.codex_home,
            dry_run=args.dry_run,
        )
    except (ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if not report.changes:
        print("Nothing to clean.")
        return 0

    if args.dry_run:
        print("Dry run — the following would be removed:")
    else:
        print("Cleaned:")

    print(format_change_report(report))
    return 0


def _handle_uninstall_command(args: argparse.Namespace) -> int:
    """Handle the uninstall command."""
    if args.json and not args.dry_run:
        print("Error: --json requires --dry-run for uninstall", file=sys.stderr)
        return 1

    try:
        from cc_codex_bridge.reconcile import uninstall_all
        report = uninstall_all(
            codex_home=args.codex_home,
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

    return 0


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
                lines.append("Nothing to clean.")
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

    return "\n".join(lines).rstrip()


def _handle_launchagent_command(args: argparse.Namespace) -> int:
    """Handle LaunchAgent rendering or installation commands."""
    try:
        resolved_project = resolve_project_root(args.project or Path.cwd()).root
        label = args.label or build_launchagent_label(resolved_project)
        plist_bytes = build_launchagent_plist(
            project_root=resolved_project,
            interval_seconds=args.interval,
            cache_dir=args.cache_dir,
            claude_home=args.claude_home,
            codex_home=args.codex_home,
            python_executable=args.python_executable,
            cli_path=args.cli_path,
            label=label,
            logs_dir=args.logs_dir,
        )
    except (DiscoveryError, ReconcileError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.command == "print-launchagent":
        sys.stdout.buffer.write(plist_bytes)
        return 0

    destination = install_launchagent(
        plist_bytes,
        label=label,
        launchagents_dir=args.launchagents_dir,
    )
    print(f"LAUNCHAGENT_LABEL: {label}")
    print(f"LAUNCHAGENT_PATH: {destination}")
    print(f"NEXT_STEP: launchctl bootstrap gui/$(id -u) {destination}")
    return 0


def _print_summary(
    result,
    shim_action: str,
    role_count: int,
    prompt_count: int,
    skill_count: int,
    rendered_config: str,
    exclusion_report: ExclusionReport,
) -> None:
    """Print a human-readable discovery summary."""
    print(f"PROJECT_ROOT: {result.project.root}")
    print(f"AGENTS_MD: {result.project.agents_md_path}")
    print(f"CLAUDE_MD_ACTION: {shim_action}")
    print(f"PLUGINS_FOUND: {len(result.plugins)}")
    print(f"GENERATED_ROLES: {role_count}")
    print(f"GENERATED_PROMPTS: {prompt_count}")
    print(f"GENERATED_SKILLS: {skill_count}")
    print(f"CONFIG_LINES: {len(rendered_config.splitlines())}")
    for plugin in result.plugins:
        print(
            "PLUGIN: "
            f"{plugin.marketplace}/{plugin.plugin_name}@{plugin.version_text} "
            f"source={plugin.source_path} "
            f"skills={len(plugin.skills)} "
            f"agents={len(plugin.agents)}"
        )
    print(f"EXCLUDED_PLUGINS: {len(exclusion_report.plugins)}")
    print(f"EXCLUDED_SKILLS: {len(exclusion_report.skills)}")
    print(f"EXCLUDED_AGENTS: {len(exclusion_report.agents)}")
    for plugin_id in exclusion_report.plugins:
        print(f"EXCLUDED_PLUGIN: {plugin_id}")
    for skill_id in exclusion_report.skills:
        print(f"EXCLUDED_SKILL: {skill_id}")
    for agent_id in exclusion_report.agents:
        print(f"EXCLUDED_AGENT: {agent_id}")


def _build_status_payload(
    report,
    exclusion_report: ExclusionReport,
    *,
    diagnostics=None,
) -> dict[str, object]:
    """Build a stable status payload from reconcile diff output."""
    categorized_changes: dict[str, dict[str, list[str]]] = {
        "project_files": {"create": [], "update": [], "remove": []},
        "skills": {"create": [], "update": [], "remove": []},
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
            category = "skills" if change.resource_kind == "skill" else "project_files"
            categorized_changes[category][change.kind].append(str(change.path))
        pending_change_count = len(report.changes)
        status = "in_sync" if not report.changes else "pending_changes"

    return {
        "status": status,
        "pending_change_count": pending_change_count,
        "categorized_changes": categorized_changes,
        "diagnostics": rendered_diagnostics,
        "excluded": {
            "plugins": list(exclusion_report.plugins),
            "skills": list(exclusion_report.skills),
            "agents": list(exclusion_report.agents),
        },
    }


def format_status_json(report, exclusion_report: ExclusionReport, *, diagnostics=None) -> str:
    """Render status output as deterministic JSON."""
    return json.dumps(
        _build_status_payload(report, exclusion_report, diagnostics=diagnostics),
        indent=2,
        sort_keys=True,
    )


def format_status_report(report, exclusion_report: ExclusionReport, *, diagnostics=None) -> str:
    """Render status output as human-readable text."""
    payload = _build_status_payload(report, exclusion_report, diagnostics=diagnostics)
    categorized = payload["categorized_changes"]
    project_files = categorized["project_files"]
    skills = categorized["skills"]
    lines = [
        f"STATUS: {payload['status']}",
        f"PENDING_CHANGES: {payload['pending_change_count']}",
        (
            "PROJECT_FILES: "
            f"create={len(project_files['create'])} "
            f"update={len(project_files['update'])} "
            f"remove={len(project_files['remove'])}"
        ),
        (
            "SKILLS: "
            f"create={len(skills['create'])} "
            f"update={len(skills['update'])} "
            f"remove={len(skills['remove'])}"
        ),
        (
            "EXCLUDED: "
            f"plugins={len(payload['excluded']['plugins'])} "
            f"skills={len(payload['excluded']['skills'])} "
            f"agents={len(payload['excluded']['agents'])}"
        ),
    ]
    for diagnostic in payload["diagnostics"]:
        lines.append(f"DIAGNOSTIC: {diagnostic['message']}")
    for path in project_files["create"]:
        lines.append(f"PROJECT_FILE_CREATE: {path}")
    for path in project_files["update"]:
        lines.append(f"PROJECT_FILE_UPDATE: {path}")
    for path in project_files["remove"]:
        lines.append(f"PROJECT_FILE_REMOVE: {path}")
    for path in skills["create"]:
        lines.append(f"SKILL_CREATE: {path}")
    for path in skills["update"]:
        lines.append(f"SKILL_UPDATE: {path}")
    for path in skills["remove"]:
        lines.append(f"SKILL_REMOVE: {path}")
    for plugin_id in payload["excluded"]["plugins"]:
        lines.append(f"EXCLUDED_PLUGIN: {plugin_id}")
    for skill_id in payload["excluded"]["skills"]:
        lines.append(f"EXCLUDED_SKILL: {skill_id}")
    for agent_id in payload["excluded"]["agents"]:
        lines.append(f"EXCLUDED_AGENT: {agent_id}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
