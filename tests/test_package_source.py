"""Source releases must not contain demo inputs, peer data, or local secrets."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import zipfile

import pytest


SPEC = importlib.util.spec_from_file_location("package_source", Path(__file__).parents[1] / "scripts" / "package_source.py")
assert SPEC and SPEC.loader
packager = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = packager
SPEC.loader.exec_module(packager)


def write(root: Path, name: str, data: bytes = b"source\n") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "project"
    for name in packager.REQUIRED_FILES:
        write(root, name)
    for name in (
        "constraints-demo.txt", ".gitattributes", ".env.example", "src/p2psim/peer.py",
        "src/p2psim/visual_assets/index.html", "src/p2psim/visual_assets/style.css",
        "src/p2psim/visual_assets/app.js", "tests/test_peer.py",
        "docs/REPORT.md", "sample_files/.gitkeep",
    ):
        write(root, name)
    return root


def test_archive_allowlist_excludes_secrets_runtime_and_generated_uploads(source_tree, tmp_path):
    forbidden = (
        ".env", "secret.env", "runtime/source.pdf", "peer-data/output.docx",
        "sample_files/personal.pdf", "sample_files/sample.bin", "docs/report.pdf",
        "docs/private-upload.pdf", "docs/uploads/notes.md", ".vscode/settings.json",
        "src/p2psim/secret.env", "src/p2psim/__pycache__/should_not_ship.py",
        "src/p2psim/.pytest_cache/data.py", "src/p2psim/runtime/upload.py",
        "src/p2psim/local.egg-info/generated.py", "tests/.cache/secret.py",
        "scripts/build/output.py", "dist/previous.zip",
    )
    for name in forbidden:
        write(source_tree, name, b"DO NOT DISTRIBUTE")
    result = packager.build_archive(source_tree, tmp_path / "release.zip")
    with zipfile.ZipFile(result.archive) as archive:
        names = archive.namelist()
        prefix = packager.PROJECT_NAME + "/"
        assert all(name.startswith(prefix) for name in names)
        assert not ({prefix + name for name in forbidden} & set(names))
        assert prefix + "scripts/package_source.py" in names
        assert prefix + "docs/input_specification.pdf" in names
        assert prefix + "src/p2psim/visual_assets/app.js" in names
        assert prefix + "constraints-demo.txt" in names
        assert prefix + ".gitattributes" in names
        assert names == sorted(names)
        lines = archive.read(packager.MANIFEST_NAME).decode("utf-8").splitlines()
        assert len(lines) == result.source_files
        for line in lines:
            digest, name = line.split("  ", 1)
            assert digest == hashlib.sha256(archive.read(name)).hexdigest()
    assert result.checksum.read_text().strip() == f"{hashlib.sha256(result.archive.read_bytes()).hexdigest()}  release.zip"


def test_archive_is_reproducible_and_never_recursively_includes_dist(source_tree):
    output = source_tree / "dist" / "source.zip"
    first = packager.build_archive(source_tree, output)
    first_bytes = output.read_bytes()
    write(source_tree, "dist/extra.py", b"not source")
    second = packager.build_archive(source_tree, output)
    assert output.read_bytes() == first_bytes
    assert second.sha256 == first.sha256
    assert second.source_files == first.source_files


def test_missing_specification_fails_before_producing_a_release(source_tree, tmp_path):
    (source_tree / "docs/input_specification.pdf").unlink()
    output = tmp_path / "incomplete.zip"
    with pytest.raises(ValueError, match="input_specification"):
        packager.build_archive(source_tree, output)
    assert not output.exists()
