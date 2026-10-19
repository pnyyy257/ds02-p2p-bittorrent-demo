import hashlib

import pytest
from pydantic import ValidationError

from p2psim.common import (FileMetadata, atomic_bytes, atomic_json, digest_file,
                           safe_name, sha256, validate_chunk)


def mkmeta(data=b"hello world", size=4):
    parts = [data[i:i+size] for i in range(0, len(data), size)]
    h = sha256(data)
    return FileMetadata(file_id=h, file_hash=h, file_name="demo.txt", file_size=len(data),
                        chunk_size=size, chunk_count=len(parts),
                        chunk_hashes=[sha256(p) for p in parts], tracker_url="http://tracker:8000")


def test_sha256_matches_standard():
    assert sha256(b"hello") == hashlib.sha256(b"hello").hexdigest()


def test_metadata_contains_required_fields():
    meta = mkmeta()
    assert meta.chunk_count == 3
    assert meta.file_id == meta.file_hash
    assert len(meta.chunk_hashes) == 3


def test_invalid_chunk_count_rejected():
    with pytest.raises(ValidationError):
        mkmeta().model_copy(update={"chunk_count": 8}).model_validate(
            mkmeta().model_copy(update={"chunk_count": 8}).model_dump())


def test_corrupted_chunk_rejected():
    meta = mkmeta()
    assert validate_chunk(meta, 0, b"hell")
    assert not validate_chunk(meta, 0, b"HELP")
    assert not validate_chunk(meta, 0, b"hello")


def test_filename_traversal_rejected():
    for bad in ("../secrets", "/tmp/foo", "a/b", "a\\b", ".."):
        with pytest.raises(ValueError):
            safe_name(bad)


def test_file_digest_and_atomic_storage(tmp_path):
    path = tmp_path / "a.bin"
    atomic_bytes(path, b"example")
    assert digest_file(path) == sha256(b"example")
    atomic_bytes(path, b"second")
    assert path.read_bytes() == b"second"


def test_json_atomic_persistence(tmp_path):
    import json
    target = tmp_path / "state.json"
    atomic_json(target, {"chunks": [1, 3]})
    assert json.loads(target.read_text()) == {"chunks": [1, 3]}
    assert list(tmp_path.glob(".tmp-*")) == []


def test_empty_file_metadata():
    meta = mkmeta(b"")
    assert meta.chunk_count == 0 and meta.file_size == 0
