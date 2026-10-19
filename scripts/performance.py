"""Performance measurement: compare download times at concurrency 1 and 4.
Requires test stack; uses fresh downloads on different peers.
"""
import json
import os
import time
import urllib.request

TOKEN = os.getenv("ADMIN_TOKEN", "change-me-admin-token")

def api(peer, method, suffix, payload=None):
    req = urllib.request.Request(f"http://localhost:{peer}{suffix}", method=method,
          data=json.dumps(payload).encode() if payload is not None else None,
          headers={"X-Admin-Token": TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

if __name__ == "__main__":
    # Start from clean runtime to benchmark a fresh download.
    meta = api(9001, "POST", "/api/seed", {"source_name": "sample.bin", "chunk_size": 65536})["metadata"]
    fid = meta["file_id"]
    print("Benchmark: file", meta["file_size"], "bytes; upload delay per chunk is configured in Compose")
    for port, workers in [(9002, 1), (9003, 4)]:
        # Keep available source topology stable: peer A alone serves both benchmark runs.
        # Peer B may finish before C starts; disable B uploading so it cannot become a second source.
        if port == 9003:
            api(9002, "POST", "/api/debug/serving/false")
        start = time.perf_counter()
        api(port, "POST", "/api/downloads", {"file_id": fid, "parallelism": workers})
        while True:
            status = api(port, "GET", f"/api/downloads/{fid}")
            if status["status"] == "completed":
                break
            if status["status"] == "failed":
                raise RuntimeError(status)
            time.sleep(0.08)
        elapsed = time.perf_counter() - start
        print(f"workers={workers} time={elapsed:.3f}s throughput={meta['file_size'] / elapsed / 1048576:.3f} MiB/s sources={status['sources_used']}")
    api(9002, "POST", "/api/debug/serving/true")
