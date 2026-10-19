"""Control client for tracker/peers - all transfer still happens directly between peers."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def request(method, url, token="", data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"X-Admin-Token": token, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")
    except urllib.error.URLError as e:
        sys.exit(f"Connection error: {e.reason}")


def main():
    p = argparse.ArgumentParser(description="DS02 P2P simulation CLI")
    p.add_argument("--peer", default="http://localhost:9001", help="Peer control API address")
    p.add_argument("--tracker", default="http://localhost:8000", help="Tracker address")
    p.add_argument("--token", default=os.getenv("ADMIN_TOKEN", "change-me-admin-token"), help="Admin token")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed"); s.add_argument("filename"); s.add_argument("--chunk-size", type=int, default=65536)
    s = sub.add_parser("download"); s.add_argument("file_id"); s.add_argument("--parallelism", type=int, default=4)
    s = sub.add_parser("status"); s.add_argument("file_id")
    sub.add_parser("local-files"); sub.add_parser("tracker-files")
    s = sub.add_parser("corrupt"); s.add_argument("file_id"); s.add_argument("index", type=int)
    s = sub.add_parser("serving"); s.add_argument("mode", choices=["on", "off"])
    a = p.parse_args()
    base = a.peer.rstrip("/")
    if a.command == "seed":
        result = request("POST", base + "/api/seed", a.token, {"source_name": a.filename, "chunk_size": a.chunk_size})
    elif a.command == "download":
        result = request("POST", base + "/api/downloads", a.token, {"file_id": a.file_id, "parallelism": a.parallelism})
    elif a.command == "status":
        result = request("GET", base + "/api/downloads/" + a.file_id, a.token)
    elif a.command == "local-files":
        result = request("GET", base + "/api/files", a.token)
    elif a.command == "tracker-files":
        result = request("GET", a.tracker.rstrip("/") + "/api/files")
    elif a.command == "corrupt":
        result = request("POST", base + "/api/debug/corrupt", a.token, {"file_id": a.file_id, "chunk_index": a.index})
    else:
        result = request("POST", base + "/api/debug/serving/" + str(a.mode == "on").lower(), a.token)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
