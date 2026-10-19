import hashlib
import time
import urllib.error

import pytest

from conftest import api, seed_file, wait_completed


def payload(length, seed):
    import random
    return random.Random(seed).randbytes(length)


def test_parallel_two_peer_download_with_hash_verification(live_stack):
    st = live_stack
    data = payload(50 * 8192, 11)
    fid = seed_file(st, "multi.bin", data)
    urls = st["urls"]
    api(urls["c"] + "/api/downloads", "POST", {"file_id": fid})
    wait_completed(urls["c"], fid)
    api(urls["b"] + "/api/downloads", "POST", {"file_id": fid, "parallelism": 5})
    status = wait_completed(urls["b"], fid)
    assert set(status["sources_used"]) == {"peer-a", "peer-c"}
    assert (st["root"] / "b" / "downloads" / "multi.bin").read_bytes() == data
    assert hashlib.sha256(data).hexdigest() == fid


def test_upload_before_finish_and_simultaneous_download(live_stack):
    st = live_stack
    data = payload(130 * 8192, 22)
    fid = seed_file(st, "swarm.bin", data)
    urls = st["urls"]
    api(urls["b"] + "/api/downloads", "POST", {"file_id": fid, "parallelism": 2})
    begin = time.monotonic()
    while time.monotonic() - begin < 12:
        status = api(urls["b"] + "/api/downloads/" + fid)
        if 12 <= status["downloaded_chunks"] < status["total_chunks"]:
            break
        time.sleep(0.03)
    assert status["status"] == "running"
    api(urls["c"] + "/api/downloads", "POST", {"file_id": fid, "parallelism": 5})
    final_c = wait_completed(urls["c"], fid)
    wait_completed(urls["b"], fid)
    assert "peer-b" in final_c["sources_used"]
    assert (st["root"] / "c" / "downloads" / "swarm.bin").read_bytes() == data


def test_detect_corruption_and_retry_other_source(live_stack):
    st = live_stack
    data = payload(40 * 8192, 33)
    fid = seed_file(st, "damaged.bin", data)
    urls = st["urls"]
    api(urls["c"] + "/api/downloads", "POST", {"file_id": fid})
    wait_completed(urls["c"], fid)
    api(urls["c"] + "/api/debug/corrupt", "POST", {"file_id": fid, "chunk_index": 1})
    api(urls["b"] + "/api/downloads", "POST", {"file_id": fid})
    final = wait_completed(urls["b"], fid)
    assert final["invalid_chunks"] > 0
    assert (st["root"] / "b" / "downloads" / "damaged.bin").read_bytes() == data


def test_source_failure_recovery_and_concurrency_guard(live_stack):
    st = live_stack
    data = payload(100 * 8192, 44)
    fid = seed_file(st, "failure.bin", data)
    urls = st["urls"]
    api(urls["c"] + "/api/downloads", "POST", {"file_id": fid})
    wait_completed(urls["c"], fid)

    # Deliberately interrupt an IN-FLIGHT upload, rather than hoping to race
    # a request on a fast machine. This catches failures deterministically.
    api(urls["a"] + "/api/debug/upload-delay/350", "POST")
    try:
        api(urls["b"] + "/api/downloads", "POST", {"file_id": fid, "parallelism": 2})
        with pytest.raises(urllib.error.HTTPError) as exc:
            api(urls["b"] + "/api/downloads", "POST", {"file_id": fid})
        assert exc.value.code == 409
        begin = time.monotonic()
        in_flight = False
        while time.monotonic() - begin < 12:
            s = api(urls["b"] + "/api/downloads/" + fid)
            assert s["status"] == "running", "download finished before failure injection"
            if api(urls["a"] + "/api/debug/active-uploads")["active_uploads"] > 0:
                in_flight = True
                break
            time.sleep(0.025)
        assert in_flight, "never observed an active upload on peer-a"
        api(urls["a"] + "/api/debug/serving/false", "POST")
        status = wait_completed(urls["b"], fid)
        assert status["failed_requests"] > 0
        assert "peer-c" in status["sources_used"]
        assert (st["root"] / "b" / "downloads" / "failure.bin").read_bytes() == data
    finally:
        api(urls["a"] + "/api/debug/serving/true", "POST")
        api(urls["a"] + "/api/debug/upload-delay/45", "POST")


def test_security_invalid_filename_and_unauthorized_transfer(live_stack):
    st = live_stack
    url = st["urls"]["a"]
    with pytest.raises(urllib.error.HTTPError) as exc:
        api(url + "/api/seed", "POST", {"source_name": "../secret"})
    assert exc.value.code == 400
    fid = seed_file(st, "secure.bin", b"payload")
    with pytest.raises(urllib.error.HTTPError) as exc:
        api(url + f"/api/chunks/{fid}/0")
    assert exc.value.code == 401
    with pytest.raises(urllib.error.HTTPError) as exc:
        api(url + "/api/files", auth=False)
    assert exc.value.code == 401


def test_restart_recovers_verified_chunk_state(live_stack):
    st = live_stack
    data = payload(10 * 8192, 55)
    fid = seed_file(st, "recover.bin", data)
    urls = st["urls"]
    api(urls["b"] + "/api/downloads", "POST", {"file_id": fid})
    wait_completed(urls["b"], fid)
    # The state exists on disk and is reloadable on a new process.
    from p2psim.peer import PeerNode
    recovered = PeerNode("peer-b", urls["tracker"], urls["b"], st["root"] / "b", "dummy", "dummy")
    assert len(recovered.files[fid].chunks) == 10
    assert recovered.files[fid].completed


def test_performance_throughput_metric(live_stack):
    st = live_stack
    data = payload(20 * 8192, 66)
    fid = seed_file(st, "speed.bin", data)
    start = time.perf_counter()
    api(st["urls"]["c"] + "/api/downloads", "POST", {"file_id": fid, "parallelism": 4})
    final = wait_completed(st["urls"]["c"], fid)
    elapsed = time.perf_counter() - start
    throughput = len(data) / elapsed
    assert final["status"] == "completed" and elapsed > 0 and throughput > 0
    print(f"PERF bytes={len(data)} seconds={elapsed:.3f} throughput={throughput/1048576:.3f} MiB/s")
