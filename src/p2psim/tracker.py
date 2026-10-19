"""Tracker holds metadata/peer inventories only. It NEVER stores file bytes."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .common import FileMetadata, check_peer_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s TRACKER %(levelname)s %(message)s")
log = logging.getLogger("tracker")


class Announcement(BaseModel):
    peer_id: str
    peer_url: str
    metadata: FileMetadata
    chunks: list[int]


@dataclass
class PeerEntry:
    url: str
    chunks: set[int]
    updated_at: float


class TrackerState:
    def __init__(self, ttl_seconds: float = 12.0):
        self.ttl = ttl_seconds
        self.files: dict[str, FileMetadata] = {}
        self.peers: dict[str, dict[str, PeerEntry]] = {}
        self.lock = asyncio.Lock()

    async def announce(self, a: Announcement) -> None:
        try:
            check_peer_id(a.peer_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not (a.peer_url.startswith("http://") or a.peer_url.startswith("https://")):
            raise HTTPException(400, "peer_url must be http(s)")
        if len(a.peer_url) > 255:
            raise HTTPException(400, "peer_url too long")
        if len(set(a.chunks)) != len(a.chunks) or any(i < 0 or i >= a.metadata.chunk_count for i in a.chunks):
            raise HTTPException(400, "invalid or duplicated chunk index")
        fid = a.metadata.file_id
        async with self.lock:
            existing = self.files.get(fid)
            if existing and existing != a.metadata:
                raise HTTPException(409, "conflicting metadata for existing File ID")
            self.files[fid] = a.metadata
            self.peers.setdefault(fid, {})[a.peer_id] = PeerEntry(a.peer_url, set(a.chunks), time.monotonic())
        log.info("event=ANNOUNCE peer=%s file=%s chunks=%d", a.peer_id, fid[:12], len(a.chunks))

    async def get(self, fid: str) -> dict:
        async with self.lock:
            meta = self.files.get(fid)
            if not meta:
                raise HTTPException(404, "file not found")
            now = time.monotonic()
            peers = [
                {"peer_id": pid, "peer_url": entry.url, "chunks": sorted(entry.chunks)}
                for pid, entry in self.peers.get(fid, {}).items()
                if now - entry.updated_at < self.ttl
            ]
            return {"metadata": meta.model_dump(), "peers": sorted(peers, key=lambda p: p["peer_id"])}


def create_app(state: Optional[TrackerState] = None, secret: Optional[str] = None) -> FastAPI:
    app = FastAPI(title="DS02 Metadata Tracker", version="1.0.0")
    state = state or TrackerState(float(os.getenv("PEER_TTL", "12")))
    secret = secret if secret is not None else os.environ.get("TRACKER_TOKEN", "dev-tracker-token")
    app.state.tracker = state

    def auth(x_tracker_token: str = Header(default="")) -> None:
        import hmac
        if not hmac.compare_digest(x_tracker_token, secret):
            raise HTTPException(401, "invalid tracker token")

    @app.get("/health")
    async def health():
        return {"service": "tracker", "status": "ok"}

    @app.get("/api/files")
    async def files():
        async with state.lock:
            return {"files": [{"file_id": fid, "file_name": meta.file_name, "file_size": meta.file_size}
                              for fid, meta in sorted(state.files.items())]}

    @app.get("/api/files/{file_id}")
    async def file_info(file_id: str):
        return await state.get(file_id)

    @app.put("/api/announce", dependencies=[Depends(auth)])
    async def announce(a: Announcement):
        await state.announce(a)
        return {"ok": True}

    @app.delete("/api/peers/{peer_id}", dependencies=[Depends(auth)])
    async def remove_peer(peer_id: str):
        async with state.lock:
            for entries in state.peers.values():
                entries.pop(peer_id, None)
        log.info("event=LEAVE peer=%s", peer_id)
        return {"ok": True}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
