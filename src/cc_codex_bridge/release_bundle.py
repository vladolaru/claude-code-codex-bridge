"""Helpers for building self-contained GitHub release installer assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tarfile

from cc_codex_bridge import __version__


DEFAULT_REPOSITORY = "vladolaru/claude-code-codex-bridge"
INSTALLER_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "packaging" / "install.sh.in"
WHEELHOUSE_ARCHIVE_PREFIX = "cc-codex-bridge-wheelhouse-"
SUPPORTED_PYTHON_MINORS = ((3, 11), (3, 12), (3, 13), (3, 14))
ROSETTA_NOTICE_TEMPLATE = """APPLE_SILICON_CAPABLE=\"$(sysctl -in hw.optional.arm64 2>/dev/null || printf '0')\"
  [ \"${APPLE_SILICON_CAPABLE}\" = \"1\" ] || return 0

  PYTHON_MACHINE=\"$(\"${PYTHON_BIN}\" - <<'PY'
import platform

print(platform.machine())
PY
)\"
  [ \"${PYTHON_MACHINE}\" = \"x86_64\" ] || return 0

  PYTHON_PLATFORM=\"$(\"${PYTHON_BIN}\" - <<'PY'
import sysconfig

print(sysconfig.get_platform())
PY
)\"

  printf '%s\\n' \"Warning: Detected Apple Silicon hardware but ${PYTHON_BIN} is an x86_64 Python under Rosetta.\"
  printf '%s\\n' \"         pip will resolve x86_64 wheels for platform tag ${PYTHON_PLATFORM}.\"
  printf '%s\\n' \"         To install with a native arm64 interpreter, rerun with --python /opt/homebrew/bin/python3 from an arm64 shell.\""""


def build_release_bundle(
    *,
    dist_dir: str | Path,
    wheelhouse_dir: str | Path,
    output_dir: str | Path | None = None,
    repository: str = DEFAULT_REPOSITORY,
    template_path: str | Path = INSTALLER_TEMPLATE_PATH,
) -> tuple[Path, ...]:
    """Create installer assets for one already-built release."""
    resolved_dist_dir = Path(dist_dir).expanduser().resolve()
    resolved_wheelhouse_dir = Path(wheelhouse_dir).expanduser().resolve()
    resolved_output_dir = Path(output_dir or dist_dir).expanduser().resolve()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)

    wheel_path = _find_single_distribution(
        resolved_dist_dir,
        pattern=f"cc_codex_bridge-{__version__}-*.whl",
        label="wheel",
    )
    sdist_path = _find_single_distribution(
        resolved_dist_dir,
        pattern=f"cc_codex_bridge-{__version__}.tar.gz",
        label="sdist",
    )

    archived_wheelhouse = _prepare_wheelhouse(resolved_wheelhouse_dir, wheel_path)
    wheelhouse_archive_path = resolved_output_dir / wheelhouse_archive_name(f"v{__version__}")
    _create_wheelhouse_archive(archived_wheelhouse, wheelhouse_archive_path)

    installer_path = resolved_output_dir / "install.sh"
    installer_path.write_text(
        render_installer(
            repository=repository,
            default_tag=f"v{__version__}",
            template_path=template_path,
        ),
        encoding="utf-8",
    )
    installer_path.chmod(0o755)

    checksums_path = resolved_output_dir / "SHA256SUMS"
    assets = (
        wheel_path,
        sdist_path,
        wheelhouse_archive_path,
        installer_path,
    )
    write_sha256sums(assets, output_path=checksums_path)
    return assets + (checksums_path,)


def wheelhouse_archive_name(tag: str) -> str:
    """Return the release archive name for the offline wheelhouse bundle."""
    return f"{WHEELHOUSE_ARCHIVE_PREFIX}{tag}.tar.gz"


def render_installer(
    *,
    repository: str,
    default_tag: str,
    template_path: str | Path = INSTALLER_TEMPLATE_PATH,
) -> str:
    """Render the installer shell script from the tracked template."""
    template = Path(template_path).expanduser().resolve().read_text(encoding="utf-8")
    supported_python_display = ", ".join(
        f"{major}.{minor}" for major, minor in SUPPORTED_PYTHON_MINORS
    )
    supported_python_tuples = ", ".join(
        f"({major}, {minor})" for major, minor in SUPPORTED_PYTHON_MINORS
    )
    return (
        template.replace("@REPOSITORY@", repository)
        .replace("@DEFAULT_TAG@", default_tag)
        .replace("@SUPPORTED_PYTHON_DISPLAY@", supported_python_display)
        .replace("@SUPPORTED_PYTHON_TUPLES@", supported_python_tuples)
        .replace("@ROSETTA_NOTICE@", ROSETTA_NOTICE_TEMPLATE)
    )


def write_sha256sums(paths: tuple[Path, ...], *, output_path: str | Path) -> Path:
    """Write a stable SHA256SUMS file for the supplied assets."""
    resolved_output_path = Path(output_path).expanduser().resolve()
    lines = [
        f"{sha256_file(path)}  {path.name}"
        for path in sorted(paths, key=lambda item: item.name)
    ]
    resolved_output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return resolved_output_path


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest for one file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prepare_wheelhouse(wheelhouse_dir: Path, wheel_path: Path) -> tuple[Path, ...]:
    """Return the sorted wheelhouse contents, ensuring the app wheel is present."""
    resolved = {path.name: path for path in wheelhouse_dir.glob("*.whl")}
    resolved.setdefault(wheel_path.name, wheel_path)
    if not resolved:
        raise ValueError(f"No wheel files found in wheelhouse: {wheelhouse_dir}")
    return tuple(resolved[name] for name in sorted(resolved))


def _create_wheelhouse_archive(wheels: tuple[Path, ...], archive_path: Path) -> None:
    """Create a deterministic tar.gz archive containing the offline wheelhouse."""
    with tarfile.open(archive_path, mode="w:gz", format=tarfile.PAX_FORMAT) as handle:
        for wheel in wheels:
            arcname = Path("wheelhouse") / wheel.name
            tarinfo = handle.gettarinfo(str(wheel), arcname=str(arcname))
            tarinfo.mtime = 0
            tarinfo.uid = 0
            tarinfo.gid = 0
            tarinfo.uname = ""
            tarinfo.gname = ""
            with wheel.open("rb") as source:
                handle.addfile(tarinfo, source)


def _find_single_distribution(dist_dir: Path, *, pattern: str, label: str) -> Path:
    """Find one expected built distribution artifact."""
    matches = sorted(dist_dir.glob(pattern))
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {label} matching {pattern!r} in {dist_dir}, found {len(matches)}"
        )
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    """Build the installer bundle from prebuilt distributions."""
    parser = argparse.ArgumentParser(
        description="Build GitHub release assets for offline wheelhouse installs.",
    )
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--wheelhouse-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--template-path", type=Path, default=INSTALLER_TEMPLATE_PATH)
    args = parser.parse_args(argv)

    build_release_bundle(
        dist_dir=args.dist_dir,
        wheelhouse_dir=args.wheelhouse_dir,
        output_dir=args.output_dir,
        repository=args.repository,
        template_path=args.template_path,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
