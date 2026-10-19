"""One-command 4-process demonstration without Docker.

Install: python -m pip install -e '.[dev]'
Run:     python scripts/run_local.py
Requires available localhost ports 8000, 9001, 9002, 9003.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / ("local-" + datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")


def ready(port):
    for _ in range(200):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.4) as r:
                if r.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"port {port} service did not start; check logs under {RUNTIME}")


def main():
    subprocess.run([sys.executable, "scripts/make_sample.py"], cwd=ROOT, check=True)
    RUNTIME.mkdir(parents=True)
    shared = RUNTIME / "peer-a" / "shared"
    shared.mkdir(parents=True)
    for sample in (ROOT / "sample_files").glob("*.bin"):
        shutil.copyfile(sample, shared / sample.name)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
               TRACKER_TOKEN=os.getenv("TRACKER_TOKEN", "change-me-tracker-token"),
               P2P_TOKEN=os.getenv("P2P_TOKEN", "change-me-peer-token"),
               ADMIN_TOKEN=os.getenv("ADMIN_TOKEN", "change-me-admin-token"),
               UPLOAD_DELAY_MS=os.getenv("UPLOAD_DELAY_MS", "65"),
               DEMO_RUNTIME_DIR=str(RUNTIME))
    services = []
    outputs = []
    try:
        def launch(name, module, port, extra=None):
            stream = (RUNTIME / f"{name}.log").open("w", encoding="utf-8")
            outputs.append(stream)
            process = subprocess.Popen([sys.executable, "-m", module], cwd=ROOT,
                                       env=dict(env, PORT=str(port), **(extra or {})),
                                       stdout=stream, stderr=subprocess.STDOUT)
            services.append(process)
            ready(port)

        launch("tracker", "p2psim.tracker", 8000)
        for letter, port in zip("abc", (9001, 9002, 9003)):
            launch(f"peer-{letter}", "p2psim.peer", port,
                   {"PEER_ID": f"peer-{letter}",
                    "PUBLIC_URL": f"http://127.0.0.1:{port}",
                    "TRACKER_URL": "http://127.0.0.1:8000",
                    "PEER_DATA_DIR": str(RUNTIME / f"peer-{letter}")})
        print("\nFour independent processes are running. Starting network demo...\n", flush=True)
        result = subprocess.run([sys.executable, "scripts/demo.py"], cwd=ROOT, env=env)
        print(f"\nLogs and downloaded files remain at: {RUNTIME}")
        return result.returncode
    finally:
        for p in reversed(services):
            if p.poll() is None:
                p.terminate()
        for p in reversed(services):
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        for stream in outputs:
            stream.close()


if __name__ == "__main__":
    sys.exit(main())
