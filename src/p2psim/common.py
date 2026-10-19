"""Protocol, hashing, validation and safe persistence shared by tracker and peers."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_FILE_SIZE = 1024 * 1024 * 1024  # 1 GiB for the demo
MAX_CHUNK_SIZE = 4 * 1024 * 1024
MAX_CHUNKS = 16384
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,48}$")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(name: str) -> str:
    # Reject path traversal instead of silently interpreting it.
    if not name or name in {".", ".."} or name != Path(name).name or "\\" in name or "/" in name:
        raise ValueError("filename must be a basename without directories")
    if len(name) > 180 or any(ord(ch) < 32 for ch in name):
        raise ValueError("invalid filename")
    return name


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class FileMetadata(BaseModel):
    file_id: str
    file_name: str
    file_size: int = Field(ge=0, le=MAX_FILE_SIZE)
    chunk_size: int = Field(ge=1, le=MAX_CHUNK_SIZE)
    chunk_count: int = Field(ge=0, le=MAX_CHUNKS)
    chunk_hashes: list[str]
    file_hash: str
    tracker_url: str = Field(min_length=1)

    @field_validator("file_name")
    @classmethod
    def filename(cls, v: str) -> str:
        return safe_name(v)

    @field_validator("file_id", "file_hash")
    @classmethod
    def hash_field(cls, v: str) -> str:
        if not HASH_RE.fullmatch(v):
            raise ValueError("invalid SHA-256 digest")
        return v

    @field_validator("chunk_hashes")
    @classmethod
    def hashes(cls, v: list[str]) -> list[str]:
        if any(not HASH_RE.fullmatch(h) for h in v):
            raise ValueError("invalid chunk SHA-256 digest")
        return v

    @model_validator(mode="after")
    def validate_structure(self):
        expected = math.ceil(self.file_size / self.chunk_size)
        if self.chunk_count != expected or len(self.chunk_hashes) != expected:
            raise ValueError("chunk count/hash list inconsistent with file size")
        if self.file_id != self.file_hash:
            raise ValueError("file ID must equal file SHA-256")
        return self


def check_peer_id(peer_id: str) -> str:
    if not ID_RE.fullmatch(peer_id):
        raise ValueError("peer ID must be 1-48 letters, digits, underscore or hyphen")
    return peer_id


def expected_chunk_length(meta: FileMetadata, index: int) -> int:
    if index < 0 or index >= meta.chunk_count:
        raise ValueError("invalid chunk index")
    return min(meta.chunk_size, meta.file_size - index * meta.chunk_size)


def validate_chunk(meta: FileMetadata, index: int, data: bytes) -> bool:
    return len(data) == expected_chunk_length(meta, index) and sha256(data) == meta.chunk_hashes[index]
