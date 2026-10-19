"""Independent P2P peer: shares verified chunks even while downloading.

Control and transfer API use different tokens; chunk bytes go peer->peer, never tracker.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiohttp
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .common import (FileMetadata, atomic_bytes, atomic_json, check_peer_id,
                     digest_file, expected_chunk_length, safe_name, sha256, validate_chunk,
                     MAX_CHUNK_SIZE, MAX_FILE_SIZE, MAX_CHUNKS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s PEER %(levelname)s %(message)s")
log = logging.getLogger("peer")


class SeedRequest(BaseModel):
    source_name: str
    chunk_size: int = Field(default=65536, ge=1, le=MAX_CHUNK_SIZE)


class DownloadRequest(BaseModel):
    file_id: str
    parallelism: int = Field(default=4, ge=1, le=16)
    retries: int = Field(default=12, ge=1, le=30)


class CorruptRequest(BaseModel):
    file_id: str
    chunk_index: int = Field(ge=0)


@dataclass
class LocalFile:
    meta: FileMetadata
    chunks: set[int] = field(default_factory=set)
    completed: bool = False


@dataclass
class Job:
    file_id: str
    status: str = "running"
    error: str = ""
    started_chunks: int = 0
    sources: set[str] = field(default_factory=set)
    invalid_chunks: int = 0
    failed_requests: int = 0
    task: Optional[asyncio.Task] = None

    def summary(self, have: int, total: int) -> dict:
        return {"file_id": self.file_id, "status": self.status, "error": self.error,
                "downloaded_chunks": have, "total_chunks": total,
                "sources_used": sorted(self.sources), "invalid_chunks": self.invalid_chunks,
                "failed_requests": self.failed_requests}


class PeerNode:
    def __init__(self, peer_id: str, tracker_url: str, public_url: str, data_dir: Path,
                 tracker_token: str, peer_token: str, upload_delay: float = 0.0,
                 heartbeat_seconds: float = 3.0):
        self.peer_id = check_peer_id(peer_id)
        self.tracker_url = tracker_url.rstrip("/")
        self.public_url = public_url.rstrip("/")
        self.data_dir = Path(data_dir)
        self.shared_dir = self.data_dir / "shared"
        self.storage_dir = self.data_dir / "storage"
        self.download_dir = self.data_dir / "downloads"
        for folder in (self.shared_dir, self.storage_dir, self.download_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.tracker_token, self.peer_token = tracker_token, peer_token
        self.upload_delay, self.heartbeat_seconds = upload_delay, heartbeat_seconds
        self.files: dict[str, LocalFile] = {}
        self.jobs: dict[str, Job] = {}
        self.state_lock = asyncio.Lock()
        self.client: Optional[aiohttp.ClientSession] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.serving_enabled = True
        self.active_uploads = 0
        self.events: deque[dict] = deque(maxlen=2048)
        self.event_seq = 0
        self._load_states()

    def record_event(self, kind: str, file_id: Optional[str] = None, **details) -> None:
        """Record bounded, in-memory telemetry without delaying transfers."""
        self.event_seq += 1
        event = {"seq": self.event_seq, "time": time.time(),
                 "kind": kind, "peer_id": self.peer_id}
        if file_id is not None:
            event["file_id"] = file_id
        event.update(details)
        self.events.append(event)

    def event_snapshot(self, after: int = 0, file_id: Optional[str] = None) -> dict:
        return {"events": [dict(event) for event in self.events
                           if event["seq"] > after and
                           (file_id is None or event.get("file_id") == file_id)],
                "last_seq": self.event_seq}

    def debug_state(self) -> dict:
        return {"peer_id": self.peer_id, "serving_enabled": self.serving_enabled,
                "active_uploads": self.active_uploads,
                "upload_delay_ms": round(self.upload_delay * 1000),
                "files": [{"file_id": fid, "chunks": sorted(local.chunks),
                           "completed": local.completed, "status": self.status(fid)}
                          for fid, local in sorted(self.files.items())]}

    def chunk_path(self, fid: str, idx: int) -> Path:
        return self.storage_dir / fid / f"{idx:08d}.part"

    def state_path(self, fid: str) -> Path:
        return self.storage_dir / fid / "state.json"

    def _persist(self, fid: str) -> None:
        state = self.files[fid]
        atomic_json(self.state_path(fid), {"metadata": state.meta.model_dump(),
                                           "chunks": sorted(state.chunks), "completed": state.completed})

    def _load_states(self) -> None:
        for path in self.storage_dir.glob("*/state.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                meta = FileMetadata.model_validate(payload["metadata"])
                if path.parent.name != meta.file_id:
                    raise ValueError("mismatched storage directory")
                # Verify on-disk chunks, not just persisted flags: protects restart recovery.
                verified = set()
                for index in payload.get("chunks", []):
                    chunk_path = self.chunk_path(meta.file_id, index)
                    if index >= 0 and index < meta.chunk_count and chunk_path.is_file() and validate_chunk(meta, index, chunk_path.read_bytes()):
                        verified.add(index)
                completed = len(verified) == meta.chunk_count and payload.get("completed", False)
                self.files[meta.file_id] = LocalFile(meta, verified, completed)
                log.info("event=RECOVER peer=%s file=%s chunks=%d/%d", self.peer_id, meta.file_id[:12], len(verified), meta.chunk_count)
            except (ValueError, KeyError, OSError, TypeError) as exc:
                log.warning("event=IGNORE_BAD_STATE path=%s error=%s", path, exc)

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=10, connect=3)
        self.client = aiohttp.ClientSession(timeout=timeout)
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        log.info("event=START peer=%s address=%s", self.peer_id, self.public_url)

    async def shutdown(self) -> None:
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.heartbeat_task
        for job in self.jobs.values():
            if job.task and not job.task.done():
                job.task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job.task
        if self.client:
            with contextlib.suppress(Exception):
                await self.client.delete(f"{self.tracker_url}/api/peers/{self.peer_id}", headers={"X-Tracker-Token": self.tracker_token})
            await self.client.close()
        log.info("event=STOP peer=%s", self.peer_id)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                for state in list(self.files.values()):
                    await self.announce(state.meta.file_id)
            except Exception as exc:
                log.warning("event=TRACKER_UNREACHABLE peer=%s error=%s", self.peer_id, exc)
            await asyncio.sleep(self.heartbeat_seconds)

    async def announce(self, fid: str) -> None:
        state = self.files[fid]
        payload = {"peer_id": self.peer_id, "peer_url": self.public_url,
                   "metadata": state.meta.model_dump(), "chunks": sorted(state.chunks)}
        assert self.client
        async with self.client.put(f"{self.tracker_url}/api/announce", json=payload,
                                   headers={"X-Tracker-Token": self.tracker_token}) as r:
            if r.status != 200:
                raise RuntimeError(f"tracker announce returned {r.status}: {(await r.text())[:200]}")

    async def get_tracker_file(self, fid: str) -> dict:
        assert self.client
        async with self.client.get(f"{self.tracker_url}/api/files/{fid}") as r:
            if r.status != 200:
                raise RuntimeError(f"tracker lookup returned {r.status}")
            return await r.json()

    async def seed(self, name: str, chunk_size: int) -> dict:
        try:
            safe_name(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        source = self.shared_dir / name
        if not source.is_file() or source.is_symlink():
            raise HTTPException(404, "source file not found in this peer's /data/shared folder")
        file_size = source.stat().st_size
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(413, "file exceeds 1 GiB demo limit")
        if (file_size + chunk_size - 1) // chunk_size > MAX_CHUNKS:
            raise HTTPException(413, "file requires too many chunks; increase chunk_size")
        file_hash = digest_file(source)
        chunk_hashes: list[str] = []
        fid = file_hash
        # Re-hashing before storing ensures we detect a file modified mid-seed.
        full_hash = __import__("hashlib").sha256()
        index = 0
        with source.open("rb") as f:
            while data := f.read(chunk_size):
                full_hash.update(data)
                chunk_hashes.append(sha256(data))
                atomic_bytes(self.chunk_path(fid, index), data)
                index += 1
        if full_hash.hexdigest() != file_hash:
            raise HTTPException(409, "source changed during seeding; try again")
        meta = FileMetadata(file_id=fid, file_name=name, file_size=file_size,
                            chunk_size=chunk_size, chunk_count=index,
                            chunk_hashes=chunk_hashes, file_hash=file_hash,
                            tracker_url=self.tracker_url)
        async with self.state_lock:
            self.files[fid] = LocalFile(meta, set(range(index)), True)
            self._persist(fid)
        try:
            await self.announce(fid)
        except Exception as exc:
            raise HTTPException(503, f"cannot register file at tracker: {exc}") from exc
        log.info("event=SEED peer=%s file=%s chunks=%d", self.peer_id, fid[:12], index)
        self.record_event("SEED", fid, size=file_size, chunks=index)
        return {"metadata": meta.model_dump(), "peer_id": self.peer_id}

    async def serve_chunk(self, fid: str, idx: int) -> bytes:
        if not self.serving_enabled:
            raise HTTPException(503, "peer temporarily unavailable")
        local = self.files.get(fid)
        if not local or idx not in local.chunks:
            raise HTTPException(404, "chunk is not available")
        self.active_uploads += 1
        try:
            # Check once more after the artificial delay so a simulated
            # outage also interrupts transfers already in progress.
            if self.upload_delay:
                await asyncio.sleep(self.upload_delay)
            if not self.serving_enabled:
                log.warning("event=UPLOAD_INTERRUPTED peer=%s file=%s chunk=%d",
                            self.peer_id, fid[:12], idx)
                raise HTTPException(503, "peer became unavailable during upload")
            path = self.chunk_path(fid, idx)
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise HTTPException(404, "chunk file missing") from exc
            log.info("event=UPLOAD peer=%s file=%s chunk=%d size=%d", self.peer_id, fid[:12], idx, len(data))
            self.record_event("UPLOAD", fid, chunk=idx, size=len(data), completed=local.completed)
            return data
        finally:
            self.active_uploads -= 1

    async def begin_download(self, fid: str, parallelism: int, retries: int) -> dict:
        # An already-complete local file does not need fetching.
        old_job = self.jobs.get(fid)
        if old_job and old_job.status == "running":
            raise HTTPException(409, "download already running")
        try:
            info = await self.get_tracker_file(fid)
            meta = FileMetadata.model_validate(info["metadata"])
        except Exception as exc:
            raise HTTPException(404, f"file not available from tracker: {exc}") from exc
        async with self.state_lock:
            local = self.files.get(fid)
            if local and local.meta != meta:
                raise HTTPException(409, "local metadata conflicts with tracker")
            if not local:
                local = LocalFile(meta)
                self.files[fid] = local
                self._persist(fid)
        if local.completed and (self.download_dir / meta.file_name).is_file() and digest_file(self.download_dir / meta.file_name) == meta.file_hash:
            return {"file_id": fid, "status": "completed", "detail": "already downloaded"}
        job = Job(file_id=fid)
        self.jobs[fid] = job
        self.record_event("DOWNLOAD_STARTED", fid)
        job.task = asyncio.create_task(self._run_download(job, parallelism, retries))
        return {"file_id": fid, "status": "running"}

    async def _run_download(self, job: Job, parallelism: int, retries: int) -> None:
        fid = job.file_id
        local = self.files[fid]
        pending = asyncio.Queue()
        for i in range(local.meta.chunk_count):
            if i not in local.chunks:
                pending.put_nowait(i)
        errors = []

        async def worker():
            while True:
                try:
                    i = pending.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    await self._fetch_chunk(job, local, i, retries)
                except Exception as exc:
                    errors.append(f"chunk {i}: {exc}")
                    log.error("event=CHUNK_FAILED peer=%s file=%s chunk=%d err=%s", self.peer_id, fid[:12], i, exc)
                finally:
                    pending.task_done()

        try:
            workers = [asyncio.create_task(worker()) for _ in range(min(parallelism, max(1, pending.qsize())))]
            await asyncio.gather(*workers)
            if errors:
                raise RuntimeError("; ".join(errors[:6]))
            # Explicit ordering + whole-file digest verification.
            output = self.download_dir / local.meta.file_name
            temp = self.download_dir / f".{fid}.partial"
            import hashlib
            digest = hashlib.sha256()
            with temp.open("wb") as out:
                for i in range(local.meta.chunk_count):
                    data = self.chunk_path(fid, i).read_bytes()
                    if not validate_chunk(local.meta, i, data):
                        raise RuntimeError(f"chunk {i} damaged before merge")
                    out.write(data)
                    digest.update(data)
            if digest.hexdigest() != local.meta.file_hash or temp.stat().st_size != local.meta.file_size:
                temp.unlink(missing_ok=True)
                raise RuntimeError("FINAL HASH MISMATCH")
            os.replace(temp, output)
            async with self.state_lock:
                local.completed = True
                self._persist(fid)
            job.status = "completed"
            log.info("event=DOWNLOAD_COMPLETE peer=%s file=%s sources=%s hash=OK", self.peer_id, fid[:12], sorted(job.sources))
            self.record_event("DOWNLOAD_COMPLETE", fid, size=local.meta.file_size)
        except asyncio.CancelledError:
            job.status = "interrupted"
            self._persist(fid)
            raise
        except Exception as exc:
            job.status, job.error = "failed", str(exc)
            self._persist(fid)
            log.error("event=DOWNLOAD_FAILED peer=%s file=%s error=%s", self.peer_id, fid[:12], exc)
            self.record_event("DOWNLOAD_FAILED", fid, error=str(exc))

    async def _fetch_chunk(self, job: Job, local: LocalFile, idx: int, retries: int) -> None:
        fid = local.meta.file_id
        tried: set[str] = set()
        for attempt in range(retries):
            source = None
            try:
                info = await self.get_tracker_file(fid)
                eligible = [p for p in info["peers"] if p["peer_id"] != self.peer_id and idx in p["chunks"]]
                # Prefer a source we have not yet tried; distribute chunks across sources.
                eligible.sort(key=lambda p: p["peer_id"])
                if eligible:
                    rotated = eligible[idx % len(eligible):] + eligible[:idx % len(eligible)]
                    source = next((p for p in rotated if p["peer_id"] not in tried), None)
                    if source is None:
                        tried.clear()
                        source = rotated[0]
                    tried.add(source["peer_id"])
                    assert self.client
                    url = f"{source['peer_url'].rstrip('/')}/api/chunks/{fid}/{idx}"
                    async with self.client.get(url, headers={"X-Peer-Token": self.peer_token},
                                               timeout=aiohttp.ClientTimeout(total=6, connect=2)) as resp:
                        if resp.status != 200:
                            raise RuntimeError(f"source {source['peer_id']} HTTP {resp.status}")
                        # A malicious sender cannot force unbounded memory growth.
                        if resp.content_length is not None and resp.content_length > expected_chunk_length(local.meta, idx):
                            raise RuntimeError("oversized chunk response")
                        expected = expected_chunk_length(local.meta, idx)
                        received = bytearray()
                        # StreamReader.read(n) may return a partial transport
                        # segment. Read through EOF, bounded to one extra byte
                        # so oversized/chunked responses remain memory-limited.
                        while len(received) <= expected:
                            block = await resp.content.read(expected + 1 - len(received))
                            if not block:
                                break
                            received.extend(block)
                        data = bytes(received)
                    if not validate_chunk(local.meta, idx, data):
                        job.invalid_chunks += 1
                        log.warning("event=INVALID_CHUNK peer=%s source=%s file=%s chunk=%d", self.peer_id, source["peer_id"], fid[:12], idx)
                        self.record_event("INVALID_CHUNK", fid, source=source["peer_id"], chunk=idx, size=len(data))
                        raise RuntimeError(f"invalid chunk hash from {source['peer_id']}")
                    async with self.state_lock:
                        atomic_bytes(self.chunk_path(fid, idx), data)
                        local.chunks.add(idx)
                        self._persist(fid)
                    job.sources.add(source["peer_id"])
                    job.started_chunks += 1
                    log.info("event=DOWNLOAD_CHUNK peer=%s source=%s file=%s chunk=%d", self.peer_id, source["peer_id"], fid[:12], idx)
                    self.record_event("DOWNLOAD_CHUNK", fid, source=source["peer_id"], chunk=idx, size=len(data))
                    # Critical: chunks immediately become shareable, before download finishes.
                    await self.announce(fid)
                    return
                else:
                    raise RuntimeError(f"no online peer has chunk {idx}")
            except asyncio.CancelledError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, OSError) as exc:
                job.failed_requests += 1
                log.warning("event=RETRY peer=%s file=%s chunk=%d attempt=%d err=%s", self.peer_id, fid[:12], idx, attempt + 1, exc)
                details = {"chunk": idx, "attempt": attempt + 1, "error": str(exc)}
                if source is not None:
                    details["source"] = source["peer_id"]
                self.record_event("RETRY", fid, **details)
                if attempt + 1 < retries:
                    await asyncio.sleep(min(0.2 * (attempt + 1), 1.0))
        raise RuntimeError(f"exhausted {retries} attempts")

    def status(self, fid: str) -> dict:
        local = self.files.get(fid)
        if not local:
            raise HTTPException(404, "unknown local file")
        job = self.jobs.get(fid)
        if job:
            return job.summary(len(local.chunks), local.meta.chunk_count)
        return {"file_id": fid, "status": "completed" if local.completed else "paused",
                "downloaded_chunks": len(local.chunks), "total_chunks": local.meta.chunk_count}

    async def corrupt(self, fid: str, idx: int) -> dict:
        local = self.files.get(fid)
        if not local or idx not in local.chunks:
            raise HTTPException(404, "chunk not available")
        path = self.chunk_path(fid, idx)
        async with self.state_lock:
            data = path.read_bytes()
            if not data:
                raise HTTPException(400, "cannot corrupt empty chunk")
            atomic_bytes(path, bytes([data[0] ^ 0xFF]) + data[1:])
        log.warning("event=SIMULATED_CORRUPTION peer=%s file=%s chunk=%d", self.peer_id, fid[:12], idx)
        return {"corrupted": idx}


def create_app(node: Optional[PeerNode] = None, admin_token: Optional[str] = None):
    from contextlib import asynccontextmanager

    if node is None:
        node = PeerNode(
            peer_id=os.getenv("PEER_ID", "peer-local"),
            tracker_url=os.getenv("TRACKER_URL", "http://localhost:8000"),
            public_url=os.getenv("PUBLIC_URL", "http://localhost:9000"),
            data_dir=Path(os.getenv("PEER_DATA_DIR", "./peer-data")),
            tracker_token=os.getenv("TRACKER_TOKEN", "dev-tracker-token"),
            peer_token=os.getenv("P2P_TOKEN", "dev-peer-token"),
            upload_delay=float(os.getenv("UPLOAD_DELAY_MS", "0")) / 1000,
        )
    admin_token = admin_token if admin_token is not None else os.getenv("ADMIN_TOKEN", "dev-admin-token")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await node.start()
        yield
        await node.shutdown()

    app = FastAPI(title=f"DS02 P2P Peer {node.peer_id}", lifespan=lifespan, version="1.0.0")
    app.state.node = node

    def admin_auth(x_admin_token: str = Header(default="")):
        import hmac
        if not hmac.compare_digest(x_admin_token, admin_token):
            raise HTTPException(401, "invalid admin token")

    def transfer_auth(x_peer_token: str = Header(default="")):
        import hmac
        if not hmac.compare_digest(x_peer_token, node.peer_token):
            raise HTTPException(401, "invalid P2P token")

    @app.get("/health")
    async def health():
        return {"service": "peer", "peer_id": node.peer_id, "status": "ok"}

    @app.get("/api/files", dependencies=[Depends(admin_auth)])
    async def files():
        return {"peer_id": node.peer_id, "files": [node.status(fid) for fid in sorted(node.files)]}

    @app.post("/api/seed", dependencies=[Depends(admin_auth)])
    async def seed(r: SeedRequest):
        return await node.seed(r.source_name, r.chunk_size)

    @app.get("/api/chunks/{fid}/{idx}", dependencies=[Depends(transfer_auth)])
    async def chunk(fid: str, idx: int):
        data = await node.serve_chunk(fid, idx)
        return Response(data, media_type="application/octet-stream")

    @app.post("/api/downloads", status_code=202, dependencies=[Depends(admin_auth)])
    async def download(r: DownloadRequest):
        return await node.begin_download(r.file_id, r.parallelism, r.retries)

    @app.get("/api/downloads/{fid}", dependencies=[Depends(admin_auth)])
    async def download_status(fid: str):
        return node.status(fid)

    @app.post("/api/debug/corrupt", dependencies=[Depends(admin_auth)])
    async def corrupt(r: CorruptRequest):
        return await node.corrupt(r.file_id, r.chunk_index)

    @app.get("/api/debug/active-uploads", dependencies=[Depends(admin_auth)])
    async def active_uploads():
        return {"active_uploads": node.active_uploads}

    @app.get("/api/debug/events", dependencies=[Depends(admin_auth)])
    async def events(after: int = Query(default=0, ge=0), file_id: Optional[str] = None):
        return node.event_snapshot(after, file_id)

    @app.get("/api/debug/state", dependencies=[Depends(admin_auth)])
    async def debug_state():
        return node.debug_state()

    @app.post("/api/debug/upload-delay/{milliseconds}", dependencies=[Depends(admin_auth)])
    async def set_upload_delay(milliseconds: int):
        if not 0 <= milliseconds <= 2000:
            raise HTTPException(400, "milliseconds must be in [0, 2000]")
        node.upload_delay = milliseconds / 1000
        return {"upload_delay_ms": milliseconds}

    @app.post("/api/debug/serving/{enabled}", dependencies=[Depends(admin_auth)])
    async def set_serving(enabled: bool):
        node.serving_enabled = enabled
        log.warning("event=SIMULATED_DISCONNECT peer=%s serving=%s", node.peer_id, enabled)
        node.record_event("SERVING_CHANGED", serving_enabled=enabled)
        return {"serving": enabled}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "9000")))
