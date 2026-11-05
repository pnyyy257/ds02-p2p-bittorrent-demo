"""Chunk validation must use a bounded complete HTTP body, not one TCP read."""
import asyncio
from contextlib import asynccontextmanager

import aiohttp
from aiohttp import web
import pytest

from p2psim.common import FileMetadata, sha256
from p2psim.peer import Job, LocalFile, PeerNode


@asynccontextmanager
async def source_stack(tmp_path, data, handler):
    metadata = None
    source_url = None

    async def file_info(request):
        return web.json_response({"metadata": metadata.model_dump(), "peers": [
            {"peer_id": "peer-a", "peer_url": source_url, "chunks": [0]}]})

    async def announce(request):
        await request.json()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/api/files/{fid}", file_info)
    app.router.add_put("/api/announce", announce)
    app.router.add_get("/api/chunks/{fid}/{idx}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        source_url = f"http://127.0.0.1:{runner.addresses[0][1]}"
        fid = sha256(data)
        metadata = FileMetadata(file_id=fid, file_hash=fid, file_name="stream.bin",
                                file_size=len(data), chunk_size=len(data), chunk_count=1,
                                chunk_hashes=[fid], tracker_url=source_url)
        node = PeerNode("peer-b", source_url, "http://127.0.0.1:9002", tmp_path,
                        "tracker-token", "peer-token")
        local = LocalFile(metadata)
        node.files[fid] = local
        job = Job(fid)
        node.jobs[fid] = job
        async with aiohttp.ClientSession() as client:
            node.client = client
            yield node, local, job
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_valid_chunk_split_across_delayed_http_segments_is_accepted(tmp_path):
    data = bytes(range(256)) * 2048  # 512 KiB, as in a maximum-size custom demo.
    sent = []

    async def chunk(request):
        assert request.headers["X-Peer-Token"] == "peer-token"
        response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
        await response.prepare(request)
        # The first short segment reaches the reader before the rest exists.
        # A single StreamReader.read(expected+1) would validate only 17 bytes.
        for block in (data[:17], data[17:131072], data[131072:]):
            await response.write(block)
            sent.append(len(block))
            await asyncio.sleep(0.025)
        await response.write_eof()
        return response

    async with source_stack(tmp_path, data, chunk) as (node, local, job):
        await node._fetch_chunk(job, local, 0, retries=1)
        assert sent == [17, 131055, len(data) - 131072]
        assert node.chunk_path(job.file_id, 0).read_bytes() == data
        assert local.chunks == {0}
        assert job.invalid_chunks == job.failed_requests == 0
        assert job.sources == {"peer-a"}
        assert [event["kind"] for event in node.events] == ["DOWNLOAD_CHUNK"]


@pytest.mark.asyncio
async def test_oversized_chunk_without_content_length_is_rejected_without_storage(tmp_path):
    data = b"correct chunk bytes" * 1024
    response_headers = []

    async def chunk(request):
        response = web.StreamResponse(headers={"Content-Type": "application/octet-stream"})
        await response.prepare(request)
        response_headers.append(dict(response.headers))
        await response.write(data)
        await asyncio.sleep(0.025)
        await response.write(b"unexpected extra bytes")
        await response.write_eof()
        return response

    async with source_stack(tmp_path, data, chunk) as (node, local, job):
        with pytest.raises(RuntimeError, match="exhausted 1 attempts"):
            await node._fetch_chunk(job, local, 0, retries=1)
        assert response_headers and "Content-Length" not in response_headers[0]
        assert not node.chunk_path(job.file_id, 0).exists()
        assert local.chunks == set() and job.sources == set()
        assert job.invalid_chunks == job.failed_requests == 1
        assert [event["kind"] for event in node.events] == ["INVALID_CHUNK", "RETRY"]


@pytest.mark.asyncio
async def test_declared_oversized_chunk_is_rejected_before_validation(tmp_path):
    data = b"valid chunk"

    async def chunk(request):
        return web.Response(body=data + b"x", content_type="application/octet-stream")

    async with source_stack(tmp_path, data, chunk) as (node, local, job):
        with pytest.raises(RuntimeError, match="exhausted 1 attempts"):
            await node._fetch_chunk(job, local, 0, retries=1)
        assert not node.chunk_path(job.file_id, 0).exists()
        assert not local.chunks
        assert job.failed_requests == 1 and job.invalid_chunks == 0
        assert list(node.events)[0]["error"] == "oversized chunk response"


@pytest.mark.asyncio
async def test_retry_exhaustion_preserves_final_source_and_error_in_job_status(tmp_path):
    requests = []

    async def chunk(request):
        status = 503 if not requests else 504
        requests.append(status)
        return web.Response(status=status)

    async with source_stack(tmp_path, b"valid chunk", chunk) as (node, local, job):
        await node._run_download(job, parallelism=1, retries=2)

        status = node.status(job.file_id)
        assert status["status"] == "failed"
        assert status["error"] == (
            "chunk 0: exhausted 2 attempts; last source=peer-a; "
            "last error=source peer-a HTTP 504"
        )
        assert requests == [503, 504]
        assert status["failed_requests"] == 2
        assert not local.chunks and not node.chunk_path(job.file_id, 0).exists()
        assert list(node.events)[-1]["error"] == status["error"]


@pytest.mark.asyncio
async def test_retry_exhaustion_does_not_blame_previous_source_when_none_remain(tmp_path, monkeypatch):
    requests = []

    async def chunk(request):
        requests.append(request.path)
        return web.Response(status=503)

    async with source_stack(tmp_path, b"valid chunk", chunk) as (node, local, job):
        lookup = node.get_tracker_file

        async def lookup_until_source_leaves(fid):
            info = await lookup(fid)
            if requests:
                info["peers"] = []
            return info

        monkeypatch.setattr(node, "get_tracker_file", lookup_until_source_leaves)
        await node._run_download(job, parallelism=1, retries=2)

        assert node.status(job.file_id)["error"] == (
            "chunk 0: exhausted 2 attempts; last source=none; "
            "last error=no online peer has chunk 0"
        )
        assert len(requests) == 1 and job.failed_requests == 2
        assert not local.chunks


@pytest.mark.asyncio
async def test_retry_exhaustion_names_timeout_when_exception_message_is_empty(tmp_path, monkeypatch):
    async def chunk(request):
        pytest.fail("no chunk request should be sent when tracker lookup times out")

    async with source_stack(tmp_path, b"valid chunk", chunk) as (node, local, job):
        async def tracker_timeout(fid):
            raise asyncio.TimeoutError()

        monkeypatch.setattr(node, "get_tracker_file", tracker_timeout)
        await node._run_download(job, parallelism=1, retries=1)

        assert node.status(job.file_id)["error"] == (
            "chunk 0: exhausted 1 attempts; last source=none; last error=TimeoutError"
        )
        assert job.failed_requests == 1 and not local.chunks
