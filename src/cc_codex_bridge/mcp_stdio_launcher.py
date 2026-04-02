"""Runtime launcher for stdio MCP servers that need env-template expansion."""

from __future__ import annotations

import argparse
import json
import os
import sys

from cc_codex_bridge.mcp_env_templates import expand_env_template


def _parse_payload_json(value: str) -> dict[str, object]:
    """Parse the launcher payload JSON from the command line."""
    payload = json.loads(value)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def _build_child_env(
    env_templates: dict[str, str],
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the child environment by expanding template values in order."""
    child_env = dict(os.environ if base_env is None else base_env)
    for key, template in env_templates.items():
        child_env[key] = expand_env_template(template, child_env)
    return child_env


def main(argv: list[str] | None = None) -> int:
    """Parse the launcher payload, expand env templates, and exec the target."""
    parser = argparse.ArgumentParser(prog="cc_codex_bridge.mcp_stdio_launcher")
    parser.add_argument("--payload-json", required=True)
    args = parser.parse_args(argv)

    payload = _parse_payload_json(args.payload_json)
    command = payload.get("command")
    if not isinstance(command, str) or not command:
        raise ValueError("payload.command must be a non-empty string")

    raw_args = payload.get("args", [])
    if (
        not isinstance(raw_args, list)
        or not all(isinstance(arg, str) for arg in raw_args)
    ):
        raise ValueError("payload.args must be a list of strings")

    raw_env_templates = payload.get("env_templates", {})
    if not isinstance(raw_env_templates, dict):
        raise ValueError("payload.env_templates must be an object")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_env_templates.items()
    ):
        raise ValueError("payload.env_templates must be a string-to-string object")

    child_env = _build_child_env(raw_env_templates)
    os.execvpe(command, [command, *raw_args], child_env)
    raise AssertionError("unreachable")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
