"""Custom demo input remains byte-exact, bounded and separate from peer transfers."""
import asyncio
import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from p2psim.visual import MAX_SOURCE_SIZE, VisualDemo, create_app


def test_upload_preserves_filename_bytes_hash_and_reset(tmp_path):
    demo = VisualDemo(tmp_path)
    content = b"%PDF-1.7\n" + bytes(range(256)) * 3
    filename = "Bao cao 100% Việt Nam.pdf"
    with TestClient(create_app(demo), base_url="http://127.0.0.1") as client:
        response = client.post("/api/source", params={"name": filename}, content=content,
                               headers={"Content-Type": "application/octet-stream"})
        assert response.status_code == 201
        expected = dict(name=filename, size=len(content), sha256=hashlib.sha256(content).hexdigest(), custom=True)
        assert response.json() == expected
        assert client.get("/api/state").json()["source"] == expected
        staged = demo.source_path
        assert staged.read_bytes() == content
        assert list(tmp_path.iterdir()) == [tmp_path / "visual-inputs"]
        assert client.delete("/api/source").json() == {"source": None}
        assert not staged.exists()
        assert client.get("/api/state").json()["source"] is None


@pytest.mark.parametrize("filename", ["../x.pdf", "C:\\x.pdf", "file:stream.pdf", "CON.pdf",
                                      "lpt1.docx", "nul", "bad?.pdf", "bad\n.pdf", "file.", "file "])
def test_upload_rejects_unsafe_windows_names(tmp_path, filename):
    demo = VisualDemo(tmp_path)
    with TestClient(create_app(demo), base_url="http://127.0.0.1") as client:
        assert client.post("/api/source", params={"name": filename}, content=b"12345678").status_code == 400
        assert demo.source_path is None
        assert not list(tmp_path.iterdir())


def test_upload_and_reset_reject_foreign_origin_and_invalid_sizes(tmp_path):
    demo = VisualDemo(tmp_path)
    with TestClient(create_app(demo), base_url="http://127.0.0.1") as client:
        foreign = {"Origin": "https://foreign.example"}
        assert client.post("/api/source?name=sample.docx", content=b"12345678", headers=foreign).status_code == 403
        assert client.delete("/api/source", headers=foreign).status_code == 403
        assert client.post("/api/source?name=sample.pdf", content=b"1234567").status_code == 400
        assert client.post("/api/source?name=sample.pdf", content=b"12345678",
                           headers={"Content-Length": str(MAX_SOURCE_SIZE + 1)}).status_code == 413
        assert demo.state["upload_in_progress"] is False
        assert demo.state["source"] is None


@pytest.mark.asyncio
async def test_failed_or_oversized_stream_preserves_previous_source_and_unlocks(tmp_path):
    demo = VisualDemo(tmp_path)

    async def original():
        yield b"original bytes"

    await demo.upload("original.pdf", original())
    selected = demo.source_path
    before = dict(demo.state["source"])

    async def oversized():
        block = b"x" * (1024 * 1024)
        for _ in range(33):
            yield block

    with pytest.raises(HTTPException) as error:
        await demo.upload("oversized.docx", oversized())
    assert error.value.status_code == 413

    async def disconnected():
        yield b"some bytes"
        raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="client disconnected"):
        await demo.upload("interrupted.pdf", disconnected())
    assert demo.source_path == selected
    assert selected.read_bytes() == b"original bytes"
    assert demo.state["source"] == before
    assert demo.state["upload_in_progress"] is False
    assert list(selected.parent.iterdir()) == [selected]
    await demo.close()


@pytest.mark.asyncio
async def test_upload_locks_source_and_demo_until_complete(tmp_path):
    demo = VisualDemo(tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow():
        entered.set()
        await release.wait()
        yield b"12345678"

    task = asyncio.create_task(demo.upload("real.docx", slow()))
    await entered.wait()
    try:
        assert demo.state["upload_in_progress"] is True
        with pytest.raises(HTTPException) as error:
            demo.start(1)
        assert error.value.status_code == 409
        with pytest.raises(HTTPException):
            demo.clear_source()
        with pytest.raises(HTTPException):
            await demo.upload("other.pdf", slow())
    finally:
        release.set()
        await task
        await demo.close()


@pytest.mark.asyncio
async def test_failed_temp_cleanup_still_unlocks_and_preserves_upload_error(tmp_path, monkeypatch):
    demo = VisualDemo(tmp_path)
    original_unlink = Path.unlink

    def locked_partial(path, *args, **kwargs):
        if path.suffix == ".partial":
            raise PermissionError("temporary scanner lock")
        return original_unlink(path, *args, **kwargs)

    async def disconnected():
        yield b"some bytes"
        raise RuntimeError("client disconnected")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked_partial)
        with pytest.raises(RuntimeError, match="client disconnected"):
            await demo.upload("test.pdf", disconnected())
    assert demo.state["upload_in_progress"] is False
    assert demo.source_path is None


def test_artifact_download_requires_verified_peer_output_and_detects_later_damage(tmp_path):
    demo = VisualDemo(tmp_path)
    filename = "Tài liệu demo.docx"
    output = tmp_path / "peer-b" / "downloads" / filename
    output.parent.mkdir(parents=True)
    data = b"PK\x03\x04document test bytes"
    with TestClient(create_app(demo), base_url="http://127.0.0.1") as client:
        assert client.get("/api/artifact/peer-b").status_code == 404
        assert client.get("/api/artifact/peer-a").status_code == 422
        output.write_bytes(data)
        demo.state["metadata"] = dict(file_size=len(data), file_hash=hashlib.sha256(data).hexdigest())
        demo.artifacts["peer-b"] = output
        response = client.get("/api/artifact/peer-b")
        assert response.content == data
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert "filename*=utf-8''" in response.headers["content-disposition"]
        assert client.get("/api/artifact/peer-c").status_code == 404
        output.write_bytes(b"!" + data[1:])
        assert client.get("/api/artifact/peer-b").status_code == 409
