"""Build a reproducible source ZIP without runtime data or uploaded documents.

Run from any directory: python scripts/package_source.py
Only the root files, directory extensions, and specific documents below are
eligible. Generated PDFs, sample binaries, environment secrets, and peer data
are deliberately absent from this allowlist.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
import zipfile


PROJECT_NAME = "DS02-P2P-BitTorrent"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE = ROOT / "dist" / f"{PROJECT_NAME}-source.zip"
ROOT_FILES = frozenset({
    ".dockerignore", ".env.example", ".gitattributes", ".gitignore", "AGENTS.md", "README.md",
    "Dockerfile", "compose.yaml", "pyproject.toml", "requirements.txt",
    "constraints.txt", "constraints-demo.txt", "LICENSE", "LICENSE.md", "LICENSE.txt",
})
DIRECTORY_EXTENSIONS = {
    "src": frozenset({".py", ".html", ".css", ".js"}),
    "scripts": frozenset({".py"}),
    "tests": frozenset({".py"}),
}
EXACT_FILES = frozenset({"docs/input_specification.pdf", "sample_files/.gitkeep"})
REQUIRED_FILES = frozenset({
    "README.md", "AGENTS.md", "pyproject.toml", "requirements.txt", "constraints-demo.txt",
    "docs/input_specification.pdf", "scripts/package_source.py",
})
EXCLUDED_DIRECTORIES = frozenset({
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".git", ".codex", ".agents", ".aws", ".venv", "venv", "env",
    "runtime", "peer-data", "dist", "build", "node_modules", "htmlcov",
    ".tox", ".nox", ".ipynb_checkpoints",
})
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MANIFEST_NAME = f"{PROJECT_NAME}/SOURCE_MANIFEST.sha256"


@dataclass(frozen=True)
class PackageResult:
    archive: Path
    checksum: Path
    source_files: int
    archive_bytes: int
    sha256: str


def _ordinary_file(path: Path, root: Path) -> bool:
    """Reject symlinks and Windows junctions, including paths outside root."""
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        return False
    return path.is_file() and path.resolve().is_relative_to(root)


def _allowed_directory(path: Path, root: Path) -> bool:
    name = path.name.casefold()
    return (
        name not in EXCLUDED_DIRECTORIES
        and not name.endswith(".egg-info")
        and not path.is_symlink()
        and not getattr(path, "is_junction", lambda: False)()
        and path.resolve().is_relative_to(root)
    )


def collect_files(project_root: Path) -> list[Path]:
    """Return an ordered, explicit source allowlist; never scan runtime data."""
    root = project_root.resolve(strict=True)
    selected: set[Path] = set()
    for name in ROOT_FILES | EXACT_FILES:
        path = root / name
        if _ordinary_file(path, root):
            selected.add(path)
    docs = root / "docs"
    if _allowed_directory(docs, root) and docs.is_dir():
        for path in docs.iterdir():
            if path.suffix == ".md" and _ordinary_file(path, root):
                selected.add(path)
    for directory, extensions in DIRECTORY_EXTENSIONS.items():
        start = root / directory
        if not start.is_dir() or not _allowed_directory(start, root):
            continue
        for base, subdirectories, filenames in os.walk(start, followlinks=False):
            parent = Path(base)
            subdirectories[:] = sorted(
                name for name in subdirectories if _allowed_directory(parent / name, root)
            )
            for name in filenames:
                path = parent / name
                if path.suffix in extensions and _ordinary_file(path, root):
                    selected.add(path)
    names = {path.relative_to(root).as_posix() for path in selected}
    missing = REQUIRED_FILES - names
    if missing:
        raise ValueError("Required source files missing: " + ", ".join(sorted(missing)))
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def _zip_entry(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=ZIP_TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info, data


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_archive(project_root: Path, output: Path) -> PackageResult:
    root = project_root.resolve(strict=True)
    output = output.resolve()
    if output.suffix.casefold() != ".zip":
        raise ValueError("--output must name a .zip file")
    files = collect_files(root)
    entries: list[tuple[str, bytes]] = []
    for path in files:
        entries.append((f"{PROJECT_NAME}/{path.relative_to(root).as_posix()}", path.read_bytes()))
    manifest = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in entries
    ).encode("utf-8")
    entries.append((MANIFEST_NAME, manifest))
    entries.sort(key=lambda entry: entry[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(temporary, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in entries:
                info, content = _zip_entry(name, data)
                archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = _digest_file(output)
    checksum = output.with_name(output.name + ".sha256")
    checksum.write_text(f"{digest}  {output.name}\n", encoding="utf-8", newline="\n")
    return PackageResult(output, checksum, len(files), output.stat().st_size, digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_ARCHIVE,
                        help="Destination .zip file (default: project dist/DS02-P2P-BitTorrent-source.zip)")
    args = parser.parse_args(argv)
    try:
        result = build_archive(ROOT, args.output)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Packaging failed: {exc}\n")
    print(f"Archive: {result.archive}")
    print(f"Manifest: {result.source_files} source files + SOURCE_MANIFEST.sha256")
    print(f"Size: {result.archive_bytes:,} bytes")
    print(f"SHA-256: {result.sha256}")
    print(f"Checksum: {result.checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
