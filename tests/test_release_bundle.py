"""Tests for offline release bundle generation."""

from __future__ import annotations

from pathlib import Path
import tarfile

from cc_codex_bridge import __version__
from cc_codex_bridge.release_bundle import (
    build_release_bundle,
    render_installer,
    wheelhouse_archive_name,
)


def test_render_installer_embeds_repository_and_default_tag(tmp_path: Path):
    """The installer template should be parameterized by repo and tag."""
    template_path = tmp_path / "install.sh.in"
    template_path.write_text("repo=@REPOSITORY@\ntag=@DEFAULT_TAG@\n", encoding="utf-8")

    rendered = render_installer(
        repository="example/repo",
        default_tag="v9.9.9",
        template_path=template_path,
    )

    assert rendered == "repo=example/repo\ntag=v9.9.9\n"


def test_build_release_bundle_creates_archive_installer_and_checksums(tmp_path: Path):
    """Release bundling should produce the offline installer assets from wheelhouse inputs."""
    dist_dir = tmp_path / "dist"
    wheelhouse_dir = tmp_path / "wheelhouse"
    output_dir = tmp_path / "output"
    template_path = tmp_path / "install.sh.in"
    dist_dir.mkdir()
    wheelhouse_dir.mkdir()
    template_path.write_text("#!/bin/sh\nrepo=@REPOSITORY@\ntag=@DEFAULT_TAG@\n", encoding="utf-8")

    wheel_path = dist_dir / f"cc_codex_bridge-{__version__}-py3-none-any.whl"
    sdist_path = dist_dir / f"cc_codex_bridge-{__version__}.tar.gz"
    dependency_wheel = wheelhouse_dir / "PyYAML-6.0.2-cp311-cp311-macosx_11_0_arm64.whl"
    wheel_path.write_bytes(b"wheel")
    sdist_path.write_bytes(b"sdist")
    dependency_wheel.write_bytes(b"pyyaml")

    assets = build_release_bundle(
        dist_dir=dist_dir,
        wheelhouse_dir=wheelhouse_dir,
        output_dir=output_dir,
        repository="example/repo",
        template_path=template_path,
    )

    archive_path = output_dir / wheelhouse_archive_name(f"v{__version__}")
    installer_path = output_dir / "install.sh"
    checksums_path = output_dir / "SHA256SUMS"

    assert archive_path in assets
    assert installer_path in assets
    assert checksums_path in assets
    assert installer_path.read_text(encoding="utf-8") == "#!/bin/sh\nrepo=example/repo\ntag=v" + __version__ + "\n"

    with tarfile.open(archive_path, "r:gz") as handle:
        archived_names = sorted(handle.getnames())

    assert archived_names == [
        f"wheelhouse/{dependency_wheel.name}",
        f"wheelhouse/{wheel_path.name}",
    ]

    checksums = checksums_path.read_text(encoding="utf-8")
    assert wheel_path.name in checksums
    assert sdist_path.name in checksums
    assert archive_path.name in checksums
    assert installer_path.name in checksums
