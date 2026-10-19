import httpx
import pytest

from p2psim.common import FileMetadata, atomic_bytes, sha256
from p2psim.peer import Job, LocalFile, PeerNode, create_app


def make_node(tmp_path):
    return PeerNode("peer-b", "http://tracker:8000", "http://127.0.0.1:9002",
                    tmp_path, "tracker-secret", "transfer-secret")


def partial_file(node):
    data = b"abcdef"
    fid = sha256(data)
    meta = FileMetadata(file_id=fid, file_hash=fid, file_name="partial.bin",
                        file_size=len(data), chunk_size=3, chunk_count=2,
                        chunk_hashes=[sha256(b"abc"), sha256(b"def")],
                        tracker_url=node.tracker_url)
    node.files[fid] = LocalFile(meta, {0})
    node.jobs[fid] = Job(fid)
    atomic_bytes(node.chunk_path(fid, 0), b"abc")
    return fid


@pytest.mark.asyncio
async def test_telemetry_requires_admin_token(tmp_path):
    node = make_node(tmp_path)
    app = create_app(node, "admin-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        for endpoint in ("/api/debug/events", "/api/debug/state"):
            assert (await client.get(endpoint)).status_code == 401
            assert (await client.get(endpoint, headers={"X-Admin-Token": "wrong"})).status_code == 401
            assert (await client.get(endpoint, headers={"X-Admin-Token": "admin-secret"})).status_code == 200


@pytest.mark.asyncio
async def test_event_cursor_filter_and_bounded_history(tmp_path):
    node = make_node(tmp_path)
    first_fid, second_fid = sha256(b"first"), sha256(b"second")
    for index in range(2055):
        node.record_event("DOWNLOAD_CHUNK", first_fid if index % 2 else second_fid,
                          source="peer-a", chunk=index, size=3)
    app = create_app(node, "admin-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test",
                                 headers={"X-Admin-Token": "admin-secret"}) as client:
        snapshot = (await client.get("/api/debug/events")).json()
        assert snapshot["last_seq"] == 2055
        assert len(snapshot["events"]) == 2048
        assert [event["seq"] for event in snapshot["events"]] == list(range(8, 2056))
        assert all(event["peer_id"] == "peer-b" and event["time"] > 0
                   for event in snapshot["events"])

        filtered = (await client.get("/api/debug/events", params={
            "after": 2050, "file_id": first_fid})).json()
        assert [event["seq"] for event in filtered["events"]] == [2052, 2054]
        assert all(event["file_id"] == first_fid for event in filtered["events"])
        assert filtered["last_seq"] == 2055
        assert (await client.get("/api/debug/events", params={"after": 2055})).json() == {
            "events": [], "last_seq": 2055}
        assert (await client.get("/api/debug/events", params={"after": -1})).status_code == 422


@pytest.mark.asyncio
async def test_upload_records_incomplete_state_at_transfer_time(tmp_path):
    node = make_node(tmp_path)
    fid = partial_file(node)
    assert await node.serve_chunk(fid, 0) == b"abc"
    first_event = node.event_snapshot()["events"][0]
    assert first_event["kind"] == "UPLOAD"
    assert first_event["file_id"] == fid
    assert first_event["chunk"] == 0 and first_event["size"] == 3
    assert first_event["completed"] is False
    assert node.active_uploads == 0

    # Later completion must not rewrite evidence of the earlier partial upload.
    node.files[fid].completed = True
    assert await node.serve_chunk(fid, 0) == b"abc"
    assert [event["completed"] for event in node.event_snapshot()["events"]] == [False, True]


@pytest.mark.asyncio
async def test_debug_state_and_serving_change(tmp_path):
    node = make_node(tmp_path)
    fid = partial_file(node)
    node.upload_delay = 0.125
    app = create_app(node, "admin-secret")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test",
                                 headers={"X-Admin-Token": "admin-secret"}) as client:
        assert (await client.post("/api/debug/serving/false")).json() == {"serving": False}
        state = (await client.get("/api/debug/state")).json()
        assert state["peer_id"] == "peer-b"
        assert state["serving_enabled"] is False
        assert state["active_uploads"] == 0 and state["upload_delay_ms"] == 125
        assert state["files"] == [{"file_id": fid, "chunks": [0], "completed": False,
                                   "status": node.status(fid)}]
        event = (await client.get("/api/debug/events")).json()["events"][0]
        assert event["kind"] == "SERVING_CHANGED" and event["serving_enabled"] is False
