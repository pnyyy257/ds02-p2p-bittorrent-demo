import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
TOKEN = "unit-admin-token"
TRACKER_TOKEN = "unit-tracker-token"
P2P_TOKEN = "unit-p2p-token"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def api(url, method="GET", data=None, token=TOKEN, auth=True):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["X-Admin-Token"] = token
    request = urllib.request.Request(url, method=method, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def wait_completed(url, fid, timeout=45):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        status = api(f"{url}/api/downloads/{fid}")
        if status["status"] == "completed":
            return status
        if status["status"] == "failed":
            raise AssertionError(f"download failed: {status}")
        time.sleep(0.05)
    raise AssertionError(f"download timeout: {fid}")


def wait_ready(url, timeout=12):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if api(url + "/health", auth=False)["status"] == "ok":
                return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"service did not start: {url}")


@pytest.fixture(scope="module")
def live_stack(tmp_path_factory):
    root = tmp_path_factory.mktemp("network")
    tracker_port, a_port, b_port, c_port = [free_port() for _ in range(4)]
    urls = {"tracker": f"http://127.0.0.1:{tracker_port}",
            "a": f"http://127.0.0.1:{a_port}", "b": f"http://127.0.0.1:{b_port}",
            "c": f"http://127.0.0.1:{c_port}"}
    (root / "a" / "shared").mkdir(parents=True)
    procs = []
    logs = []
    common = dict(os.environ, PYTHONPATH=str(ROOT / "src"), TRACKER_TOKEN=TRACKER_TOKEN,
                  P2P_TOKEN=P2P_TOKEN, ADMIN_TOKEN=TOKEN, UPLOAD_DELAY_MS="45")
    try:
        tlog = (root / "tracker.log").open("w")
        logs.append(tlog)
        procs.append(subprocess.Popen([sys.executable, "-m", "p2psim.tracker"],
                                      cwd=root, stdout=tlog, stderr=subprocess.STDOUT,
                                      env=dict(common, PORT=str(tracker_port), PEER_TTL="5")))
        wait_ready(urls["tracker"])
        for letter, port in zip("abc", [a_port, b_port, c_port]):
            stream = (root / f"{letter}.log").open("w")
            logs.append(stream)
            procs.append(subprocess.Popen([sys.executable, "-m", "p2psim.peer"],
                                          cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                          env=dict(common, PORT=str(port), PEER_ID=f"peer-{letter}",
                                                   TRACKER_URL=urls["tracker"], PUBLIC_URL=urls[letter],
                                                   PEER_DATA_DIR=str(root / letter))))
            wait_ready(urls[letter])
        yield {"urls": urls, "root": root, "processes": procs}
    finally:
        for p in reversed(procs):
            if p.poll() is None:
                p.terminate()
        for p in reversed(procs):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for stream in logs:
            stream.close()


def seed_file(stack, name, data, chunk_size=8192):
    (stack["root"] / "a" / "shared" / name).write_bytes(data)
    payload = api(stack["urls"]["a"] + "/api/seed", "POST", {"source_name": name, "chunk_size": chunk_size})
    return payload["metadata"]["file_id"]
