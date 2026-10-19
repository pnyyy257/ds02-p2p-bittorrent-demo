"""Run mandatory DS02 scenarios against Docker Compose services.

Usage: python scripts/demo.py   (after docker compose up -d --build)
For the local browser dashboard: python scripts/run_visual.py (no Docker needed).
Note: for a *real kill* demonstration, stop peer-a during a download by hand.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOSTS = {"a": "http://localhost:9001", "b": "http://localhost:9002", "c": "http://localhost:9003"}
TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin-token")
DEMO_RUNTIME_DIR = Path(os.getenv("DEMO_RUNTIME_DIR", "runtime"))


def api(peer, method, endpoint, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(HOSTS[peer] + endpoint, data=data, method=method,
                                 headers={"X-Admin-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def seed(name):
    return api("a", "POST", "/api/seed", {"source_name": name, "chunk_size": 65536})["metadata"]["file_id"]


def download(peer, fid, parallelism=4):
    return api(peer, "POST", "/api/downloads", {"file_id": fid, "parallelism": parallelism})


def status(peer, fid):
    return api(peer, "GET", "/api/downloads/" + fid)


def wait(peer, fid, timeout=70):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = status(peer, fid)
        if s["status"] == "completed":
            return s
        if s["status"] == "failed":
            raise RuntimeError(f"{peer} download failed: {s}")
        time.sleep(0.15)
    raise TimeoutError(f"{peer}: {fid[:12]} did not finish")


def run():
    print("=== DS02 P2P MANDATORY DEMONSTRATION ===")
    for p in HOSTS:
        check(api(p, "GET", "/health")["status"] == "ok", f"peer-{p} online")

    print("\n[1] Metadata, seed, two-source parallel download")
    fid = seed("sample.bin")
    download("c", fid); wait("c", fid)
    download("b", fid)
    s = wait("b", fid)
    check(set(s["sources_used"]) == {"peer-a", "peer-c"}, "peer-b downloaded directly from TWO source peers")
    check((DEMO_RUNTIME_DIR / "peer-b/downloads/sample.bin").read_bytes() == Path("sample_files/sample.bin").read_bytes(), "merged output matches original file byte-for-byte")

    print("\n[2] Upload before the entire download is complete")
    fid_swarm = seed("sample_swarm.bin")
    download("b", fid_swarm, 2)
    t0 = time.monotonic()
    while time.monotonic() - t0 < 15:
        s_b = status("b", fid_swarm)
        if 8 <= s_b["downloaded_chunks"] < s_b["total_chunks"]:
            break
        time.sleep(0.05)
    check(s_b["status"] == "running", "peer-b is still downloading when peer-c joins")
    download("c", fid_swarm, 4)
    s_c = wait("c", fid_swarm)
    wait("b", fid_swarm)
    check("peer-b" in s_c["sources_used"], "peer-c fetched chunks from INCOMPLETE peer-b")

    print("\n[3] Detect damaged chunk and use healthy source")
    fid_bad = seed("sample_corrupt.bin")
    download("c", fid_bad); wait("c", fid_bad)
    api("c", "POST", "/api/debug/corrupt", {"file_id": fid_bad, "chunk_index": 1})
    download("b", fid_bad)
    s = wait("b", fid_bad)
    check(s["invalid_chunks"] >= 1, "SHA-256 rejected corrupted chunk")
    check(set(s["sources_used"]) == {"peer-a", "peer-c"}, "healthy chunks downloaded from multiple peers")

    print("\n[4] Source unavailable while transfer is running")
    fid_failure = seed("sample_failure.bin")
    download("c", fid_failure); wait("c", fid_failure)
    # Make the fault demonstration deterministic even on slower Windows hosts.
    # Start an upload on peer-a, verify it is in flight, then interrupt it.
    api("a", "POST", "/api/debug/upload-delay/350")
    disrupted = False
    try:
        download("b", fid_failure, 2)
        t0 = time.monotonic()
        while time.monotonic() - t0 < 15:
            s_b = status("b", fid_failure)
            if s_b["status"] != "running":
                raise AssertionError("peer-b finished before the planned disruption")
            if api("a", "GET", "/api/debug/active-uploads")["active_uploads"] > 0:
                disrupted = True
                break
            time.sleep(0.025)
        check(disrupted, "peer-a is actively uploading when disruption occurs")
        api("a", "POST", "/api/debug/serving/false")
        s = wait("b", fid_failure)
    finally:
        api("a", "POST", "/api/debug/serving/true")
        api("a", "POST", f"/api/debug/upload-delay/{int(os.getenv('UPLOAD_DELAY_MS', '65'))}")
    check(s["failed_requests"] > 0, "peer-b detected source disruption and retried")
    check("peer-c" in s["sources_used"], "peer-b recovered via alternative peer-c")

    print("\nALL FOUR SCENARIOS PASSED.")
    if os.getenv("DEMO_RUNTIME_DIR"):
        print("See local process logs at:", DEMO_RUNTIME_DIR)
    else:
        print("See logs: docker compose logs tracker peer-a peer-b peer-c")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print("DEMO FAILED:", e, file=sys.stderr)
        sys.exit(1)
