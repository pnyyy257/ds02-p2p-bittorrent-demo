import asyncio
import time

import httpx
import pytest
from fastapi import HTTPException

from p2psim.tracker import Announcement, TrackerState, create_app
from p2psim.common import sha256, FileMetadata


def meta():
    data = b"abcdef"
    fid = sha256(data)
    return FileMetadata(file_id=fid, file_hash=fid, file_name="test.bin", file_size=6,
                        chunk_size=3, chunk_count=2, chunk_hashes=[sha256(b"abc"), sha256(b"def")],
                        tracker_url="http://tracker:8000")


def ann(pid="p1", chunks=None):
    return Announcement(peer_id=pid, peer_url="http://127.0.0.1:9000", metadata=meta(),
                        chunks=[0, 1] if chunks is None else chunks)


@pytest.mark.asyncio
async def test_tracker_register_and_discover():
    state = TrackerState()
    await state.announce(ann())
    info = await state.get(meta().file_id)
    assert info["peers"][0]["peer_id"] == "p1"
    assert info["peers"][0]["chunks"] == [0, 1]


@pytest.mark.asyncio
async def test_tracker_rejects_invalid_announcement():
    state = TrackerState()
    with pytest.raises(HTTPException) as exc:
        await state.announce(ann(chunks=[0, 0]))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_tracker_lease_expires():
    state = TrackerState(ttl_seconds=0.01)
    await state.announce(ann())
    await asyncio.sleep(0.03)
    assert (await state.get(meta().file_id))["peers"] == []


@pytest.mark.asyncio
async def test_two_concurrent_peer_announcements():
    state = TrackerState()
    await asyncio.gather(state.announce(ann("peer-a")), state.announce(ann("peer-b")))
    info = await state.get(meta().file_id)
    assert {p["peer_id"] for p in info["peers"]} == {"peer-a", "peer-b"}


@pytest.mark.asyncio
async def test_api_rejects_missing_or_wrong_tracker_token():
    app = create_app(TrackerState(), "secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.put("/api/announce", json=ann().model_dump())
        assert res.status_code == 401
        res = await client.put("/api/announce", json=ann().model_dump(), headers={"X-Tracker-Token": "wrong"})
        assert res.status_code == 401
        res = await client.put("/api/announce", json=ann().model_dump(), headers={"X-Tracker-Token": "secret"})
        assert res.status_code == 200
        assert (await client.get("/api/files")).status_code == 200
