"""Safe decision tree for project-root CLAUDE.md shim handling."""

from __future__ import annotations

from cc_codex_bridge.model import ClaudeShimDecision, ProjectContext, ReconcileError
from cc_codex_bridge.text import read_utf8_text


SHIM_CONTENT = "@AGENTS.md\n"


def plan_claude_shim(project: ProjectContext) -> ClaudeShimDecision:
    """Plan safe CLAUDE.md behavior for the project root."""
    claude_md_path = project.root / "CLAUDE.md"

    # Bootstrap: CLAUDE.md exists but AGENTS.md does not
    if (
        not project.agents_md_path.exists()
        and not project.agents_md_path.is_symlink()
        and claude_md_path.is_file()
        and not claude_md_path.is_symlink()
    ):
        content = read_utf8_text(
            claude_md_path,
            label="CLAUDE.md bootstrap candidate",
            error_type=ReconcileError,
        )
        if content == SHIM_CONTENT:
            # CLAUDE.md is the generator-owned shim with AGENTS.md missing —
            # bootstrapping would create a self-referencing AGENTS.md.
            return ClaudeShimDecision(
                action="fail",
                path=claude_md_path,
                reason="CLAUDE.md is the @AGENTS.md shim but AGENTS.md is missing; "
                "restore AGENTS.md manually",
            )
        return ClaudeShimDecision(
            action="bootstrap",
            path=claude_md_path,
            content=SHIM_CONTENT,
            agents_md_content=content,
            reason="CLAUDE.md exists without AGENTS.md; will copy to AGENTS.md and replace with shim",
        )

    # AGENTS.md target is a symlink (possibly broken) — refuse to bootstrap through it
    if (
        not project.agents_md_path.exists()
        and project.agents_md_path.is_symlink()
    ):
        return ClaudeShimDecision(
            action="fail",
            path=claude_md_path,
            reason=f"AGENTS.md is a symlink; refusing to write through it: "
            f"{project.agents_md_path}",
        )

    if not claude_md_path.exists() and not claude_md_path.is_symlink():
        return ClaudeShimDecision(
            action="create",
            path=claude_md_path,
            content=SHIM_CONTENT,
            reason="CLAUDE.md missing",
        )

    if claude_md_path.is_symlink():
        target = claude_md_path.resolve()
        if target == project.agents_md_path.resolve():
            return ClaudeShimDecision(
                action="preserve",
                path=claude_md_path,
                reason="CLAUDE.md is a symlink to AGENTS.md",
            )
        return ClaudeShimDecision(
            action="skip",
            path=claude_md_path,
            reason="CLAUDE.md is a symlink but not to AGENTS.md",
        )

    content = read_utf8_text(
        claude_md_path,
        label="CLAUDE.md shim candidate",
        error_type=ReconcileError,
    )
    if content == SHIM_CONTENT:
        return ClaudeShimDecision(
            action="preserve",
            path=claude_md_path,
            content=SHIM_CONTENT,
            reason="CLAUDE.md already matches shim",
        )

    if "AGENTS.md" in content:
        return ClaudeShimDecision(
            action="preserve",
            path=claude_md_path,
            reason="CLAUDE.md references AGENTS.md",
        )

    return ClaudeShimDecision(
        action="skip",
        path=claude_md_path,
        reason="CLAUDE.md exists and is not a generator-owned shim",
    )
