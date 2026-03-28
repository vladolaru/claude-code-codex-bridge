"""Data models for Codex bridge discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


class DiscoveryError(RuntimeError):
    """Raised when project or installed-plugin discovery fails."""


class TranslationError(RuntimeError):
    """Raised when translation from Claude artifacts to Codex artifacts fails."""


class ReconcileError(RuntimeError):
    """Raised when reconcile planning or writes cannot proceed safely."""


@dataclass(frozen=True)
class SemVer:
    """Semantic version with precedence comparison."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> "SemVer":
        """Parse a semantic version string or raise ValueError."""
        match = SEMVER_RE.match(value)
        if not match:
            raise ValueError(f"Invalid semantic version: {value}")

        prerelease = tuple(
            part for part in (match.group("prerelease") or "").split(".") if part
        )

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
        )

    def __lt__(self, other: object) -> bool:
        """Compare semantic versions by precedence."""
        if not isinstance(other, SemVer):
            return NotImplemented

        core_self = (self.major, self.minor, self.patch)
        core_other = (other.major, other.minor, other.patch)
        if core_self != core_other:
            return core_self < core_other

        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and not other.prerelease:
            return False

        return _compare_prerelease(self.prerelease, other.prerelease) < 0


def _compare_prerelease(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    """Return -1, 0, or 1 for prerelease precedence comparison."""
    for left_part, right_part in zip(left, right):
        if left_part == right_part:
            continue

        left_is_num = left_part.isdigit()
        right_is_num = right_part.isdigit()

        if left_is_num and right_is_num:
            return -1 if int(left_part) < int(right_part) else 1
        if left_is_num and not right_is_num:
            return -1
        if not left_is_num and right_is_num:
            return 1
        return -1 if left_part < right_part else 1

    if len(left) == len(right):
        return 0
    return -1 if len(left) < len(right) else 1


@dataclass(frozen=True)
class ProjectContext:
    """Resolved project root and instruction file."""

    root: Path
    agents_md_path: Path


@dataclass(frozen=True)
class InstalledPluginVersion:
    """One installed Claude plugin version."""

    marketplace: str
    plugin_name: str
    version_text: str
    version: SemVer
    installed_path: Path
    resolved_path: Path


@dataclass(frozen=True)
class InstalledPlugin:
    """Latest installed plugin source selected for generation."""

    marketplace: str
    plugin_name: str
    version_text: str
    version: SemVer
    installed_path: Path
    source_path: Path
    skills: tuple[Path, ...]
    agents: tuple[Path, ...]
    commands: tuple[Path, ...]


@dataclass(frozen=True)
class DiscoveryResult:
    """Combined project and installed-plugin discovery result."""

    project: ProjectContext
    plugins: tuple[InstalledPlugin, ...]
    user_skills: tuple[Path, ...] = ()
    user_agents: tuple[Path, ...] = ()
    user_commands: tuple[Path, ...] = ()
    project_skills: tuple[Path, ...] = ()
    project_agents: tuple[Path, ...] = ()
    project_commands: tuple[Path, ...] = ()
    user_claude_md: str | None = None


@dataclass(frozen=True)
class ClaudeShimDecision:
    """Decision for handling the project root CLAUDE.md shim."""

    action: str
    path: Path
    content: str | None = None
    agents_md_content: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class AgentTranslationDiagnostic:
    """One agent translation diagnostic that invalidates generation."""

    source_path: Path
    agent_name: str
    unsupported_tools: tuple[str, ...]


@dataclass(frozen=True)
class AgentTranslationResult:
    """Agent translation result plus any hard diagnostics."""

    agents: tuple[GeneratedAgentFile, ...]
    diagnostics: tuple[AgentTranslationDiagnostic, ...]
    plugin_resources: tuple[VendoredPluginResource, ...] = ()


@dataclass(frozen=True)
class SkillValidationDiagnostic:
    """One skill validation warning from the Agent Skills Standard."""

    source_path: Path
    skill_name: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SkillTranslationResult:
    """Skill translation result plus any validation diagnostics."""

    skills: tuple[GeneratedSkill, ...]
    diagnostics: tuple[SkillValidationDiagnostic, ...]
    plugin_resources: tuple[VendoredPluginResource, ...] = ()


@dataclass(frozen=True)
class GeneratedAgentFile:
    """Generated Codex agent .toml file derived from a Claude agent."""

    marketplace: str
    plugin_name: str
    source_path: Path
    scope: str  # "global" or "project"
    agent_name: str  # Codex name field (role identifier)
    install_filename: str  # filename for the .toml file
    description: str
    developer_instructions: str
    sandbox_mode: str | None  # "read-only", "workspace-write", or None
    original_model_hint: str | None


@dataclass(frozen=True)
class GeneratedSkillFile:
    """One file that will be materialized inside a generated Codex skill."""

    relative_path: Path
    content: bytes
    mode: int


@dataclass(frozen=True)
class GeneratedSkill:
    """Generated Codex skill tree derived from a Claude skill."""

    marketplace: str
    plugin_name: str
    source_path: Path
    install_dir_name: str
    original_skill_name: str
    codex_skill_name: str
    files: tuple[GeneratedSkillFile, ...]


@dataclass(frozen=True)
class GeneratedPrompt:
    """Generated Codex prompt file derived from a Claude command."""

    filename: str            # e.g., "review.md", "review--my-app.md"
    content: bytes
    source_path: Path
    marketplace: str
    plugin_name: str


@dataclass(frozen=True)
class PromptTranslationResult:
    """Prompt translation result plus any diagnostics."""

    prompts: tuple[GeneratedPrompt, ...]
    diagnostics: tuple[SkillValidationDiagnostic, ...]
    plugin_resources: tuple[VendoredPluginResource, ...] = ()


@dataclass(frozen=True)
class VendoredPluginResource:
    """One plugin resource directory to write under bridge home."""

    marketplace: str
    plugin_name: str
    source_dir: Path         # e.g., /path/to/plugin/scripts
    target_dir_name: str     # e.g., "scripts"
    files: tuple[GeneratedSkillFile, ...]  # reuse GeneratedSkillFile for file content


@dataclass(frozen=True)
class DiscoveredMcpServer:
    """An MCP server definition discovered from Claude Code configuration."""

    name: str           # server name (key in mcpServers)
    scope: str          # "global" or "project"
    transport: str      # "stdio" or "http"
    source: str         # "user-global", "project-local", or "project-shared"
    config: dict        # raw CC config dict


@dataclass(frozen=True)
class GeneratedMcpServer:
    """A translated MCP server ready to write into Codex config.toml."""

    name: str           # server name (preserved from CC)
    scope: str          # "global" or "project"
    toml_table: dict    # Codex-side TOML key-value pairs
    source_description: str  # provenance for logging/diagnostics


@dataclass(frozen=True)
class McpTranslationDiagnostic:
    """A warning produced during MCP server translation."""

    server_name: str
    message: str


@dataclass(frozen=True)
class McpTranslationResult:
    """Result of translating discovered MCP servers."""

    servers: tuple[GeneratedMcpServer, ...]
    diagnostics: tuple[McpTranslationDiagnostic, ...]
