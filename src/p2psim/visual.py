"""Local presentation controller. File bytes still travel only between peers."""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterable, Literal

import aiohttp
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .common import digest_file, safe_name

ROOT = Path(__file__).resolve().parents[2]
ASSETS = Path(__file__).with_name("visual_assets")
PEERS = ("peer-a", "peer-b", "peer-c")
CHUNK_SIZE = 65536
MIN_SOURCE_SIZE = 8
MAX_SOURCE_SIZE = 32 * 1024 * 1024


def source_name(name: str) -> str:
    """Preserve the original basename while avoiding Windows path/device aliases."""
    try:
        safe_name(name)
        if any(ch in '<>:"|?*' or ord(ch) == 127 for ch in name) or name.endswith((".", " ")):
            raise ValueError("Tên file có ký tự không hợp lệ hoặc kết thúc bằng dấu chấm/khoảng trắng.")
        stem = name.split(".", 1)[0].rstrip(" .")
        if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", stem, re.IGNORECASE):
            raise ValueError("Tên file trùng tên thiết bị dành riêng của Windows.")
    except ValueError as exc:
        raise HTTPException(400, f"Tên file không hợp lệ: {exc}") from exc
    return name


def source_size(size: int):
    if size > MAX_SOURCE_SIZE:
        raise HTTPException(413, "File demo tối đa 32 MiB.")
    if size < MIN_SOURCE_SIZE:
        raise HTTPException(400, "File cần ít nhất 8 byte để minh họa đủ bốn tình huống chia sẻ chunk.")


class DemoStack:
    """Own only the subprocesses and fresh directories created for this run."""

    def __init__(self, runtime: Path):
        self.runtime = runtime
        self.processes: list[subprocess.Popen] = []
        self.logs = []
        self.urls: dict[str, str] = {}
        self.token = secrets.token_urlsafe(24)
        self.client: aiohttp.ClientSession | None = None
        self.reservations: list[socket.socket] = []

    async def start(self):
        self.runtime.mkdir(parents=True, exist_ok=False)
        self.client = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15, connect=2))
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"),
                   TRACKER_TOKEN=secrets.token_urlsafe(24), P2P_TOKEN=secrets.token_urlsafe(24),
                   ADMIN_TOKEN=self.token, UPLOAD_DELAY_MS="60", PYTHONUNBUFFERED="1")
        ports = {}
        for name in ("tracker", *PEERS):
            reserved = socket.socket()
            reserved.bind(("127.0.0.1", 0))
            self.reservations.append(reserved)
            ports[name] = reserved.getsockname()[1]
            self.urls[name] = f"http://127.0.0.1:{ports[name]}"
        for name in ("tracker", *PEERS):
            stream = (self.runtime / f"{name}.log").open("w", encoding="utf-8")
            self.logs.append(stream)
            extra = {} if name == "tracker" else dict(
                PEER_ID=name, PEER_DATA_DIR=str(self.runtime / name),
                PUBLIC_URL=self.urls[name], TRACKER_URL=self.urls["tracker"])
            self.reservations.pop(0).close()
            module = "p2psim.tracker:app" if name == "tracker" else "p2psim.peer:app"
            process = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1",
                 "--port", str(ports[name]), "--no-access-log"],
                cwd=ROOT, env=dict(env, **extra), stdout=stream, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.processes.append(process)
            # Tracker must be ready before peers start their announcements.
            if name == "tracker":
                await self._ready(name, process)
        await asyncio.gather(*(self._ready(name, process)
                               for name, process in zip(PEERS, self.processes[1:])))

    async def _ready(self, name, process):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} không khởi động được; xem {self.runtime / (name + '.log')}")
            try:
                result = await self.api(name, "GET", "/health")
                if result.get("status") == "ok" and (
                    result.get("peer_id") == name if name != "tracker" else result.get("service") == "tracker"
                ):
                    return
            except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError):
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError(f"{name} khởi động quá hạn; xem {self.runtime}")

    async def api(self, peer, method, endpoint, payload=None):
        assert self.client
        async with self.client.request(method, self.urls[peer] + endpoint, json=payload,
                                       headers={"X-Admin-Token": self.token}) as response:
            if response.status >= 400:
                raise RuntimeError(f"{peer} {endpoint}: HTTP {response.status}: {(await response.text())[:250]}")
            return await response.json()

    async def close(self):
        for reserved in self.reservations:
            reserved.close()
        self.reservations.clear()
        for process in reversed(self.processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(self.processes):
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait)
        self.processes.clear()
        for stream in self.logs:
            stream.close()
        self.logs.clear()
        if self.client:
            await self.client.close()
            self.client = None


class VisualDemo:
    def __init__(self, runtime_root: Path | None = None):
        self.runtime_root = runtime_root or ROOT / "runtime"
        self.stack: DemoStack | None = None
        self.task: asyncio.Task | None = None
        self.monitor: asyncio.Task | None = None
        self.monitor_error: Exception | None = None
        self.event_id = 0
        self.refresh_lock = asyncio.Lock()
        self.cursors = dict.fromkeys(PEERS, 0)
        self.source_path: Path | None = None
        self.active_source: tuple[Path, dict] | None = None
        self.artifacts: dict[str, Path] = {}
        self.state = dict(status="idle", scenario=None, phase="Sẵn sàng",
                          message="Chọn một tình huống rồi bấm Chạy demo.", run_id="",
                          metadata=None, peers={}, events=[], checks=[], results=[], artifact=None, error=None,
                          source=None, upload_in_progress=False)

    def ensure_available(self):
        if self.task and not self.task.done():
            raise HTTPException(409, "Đang chạy demo; chờ tình huống hiện tại kết thúc.")
        if self.state["upload_in_progress"]:
            raise HTTPException(409, "Đang đưa file lên Peer A; chờ tải lên hoàn tất.")

    async def upload(self, name: str, chunks: AsyncIterable[bytes], declared_size: int | None = None):
        self.ensure_available()
        name = source_name(name)
        if declared_size is not None:
            source_size(declared_size)
        # Set before the first await: a second tab cannot change input mid-run/upload.
        self.state["upload_in_progress"] = True
        partial: Path | None = None
        try:
            folder = self.runtime_root / "visual-inputs"
            folder.mkdir(parents=True, exist_ok=True)
            key = uuid.uuid4().hex
            partial = folder / f"{key}.partial"
            destination = folder / f"{key}.input"
            size, digest = 0, hashlib.sha256()
            with partial.open("xb") as stream:
                async for block in chunks:
                    size += len(block)
                    if size > MAX_SOURCE_SIZE:
                        source_size(size)
                    stream.write(block)
                    digest.update(block)
            source_size(size)
            if declared_size is not None and size != declared_size:
                raise HTTPException(400, "Kích thước nhận được không khớp file đã chọn; hãy chọn lại file.")
            os.replace(partial, destination)
            previous = self.source_path
            self.source_path = destination
            self.state["source"] = dict(name=name, size=size, sha256=digest.hexdigest(), custom=True)
            if previous:
                with contextlib.suppress(OSError):
                    previous.unlink()
            return self.state["source"]
        finally:
            if partial:
                with contextlib.suppress(OSError):
                    partial.unlink(missing_ok=True)
            self.state["upload_in_progress"] = False

    async def select_path(self, path: Path):
        path = Path(path)
        async def blocks():
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    yield block
        return await self.upload(path.name, blocks(), path.stat().st_size)

    def clear_source(self):
        self.ensure_available()
        if self.source_path:
            self.source_path.unlink(missing_ok=True)
        self.source_path = None
        self.state["source"] = None

    async def artifact_path(self, peer: str) -> Path:
        path = self.artifacts.get(peer)
        metadata = self.state["metadata"]
        run_id = self.state["run_id"]
        if not path or not metadata or not path.is_file():
            raise HTTPException(404, "Peer chưa có file đầu ra đã xác minh.")
        try:
            valid = path.stat().st_size == metadata["file_size"] and (
                await asyncio.to_thread(digest_file, path) == metadata["file_hash"])
        except OSError as exc:
            raise HTTPException(404, "File đầu ra không còn tồn tại.") from exc
        if not valid:
            raise HTTPException(409, "File đầu ra đã thay đổi và không còn khớp SHA-256.")
        if self.state["run_id"] != run_id or self.artifacts.get(peer) != path:
            raise HTTPException(409, "Đã bắt đầu demo mới; chờ file mới hoàn tất.")
        return path

    def event(self, kind, message="", **fields):
        self.event_id += 1
        event = dict(id=self.event_id, time=time.time(), kind=kind, peer_id="demo", message=message)
        event.update(fields)
        self.state["events"].append(event)
        self.state["events"] = self.state["events"][-500:]

    def phase(self, title, message):
        self.state.update(phase=title, message=message)
        self.event("PHASE", message)

    def check(self, condition, label):
        self.state["checks"].append(dict(label=label, passed=bool(condition)))
        self.event("CHECK_PASS" if condition else "CHECK_FAIL", label)
        if not condition:
            raise AssertionError(label)

    def start(self, scenario):
        self.ensure_available()
        self.active_source = ((self.source_path, dict(self.state["source"]))
                              if self.source_path and self.state["source"] else None)
        self.artifacts.clear()
        self.state.update(status="running", error=None, results=[], checks=[], artifact=None,
                          phase="Khởi động", message="Đang tạo các tiến trình và thư mục dữ liệu riêng.")
        self.task = asyncio.create_task(self.run(scenario))

    async def refresh(self):
        async with self.refresh_lock:
            await self._refresh()

    async def _refresh(self):
        assert self.stack
        stack = self.stack
        async def read(peer):
            state, events = await asyncio.gather(
                stack.api(peer, "GET", "/api/debug/state"),
                stack.api(peer, "GET", f"/api/debug/events?after={self.cursors[peer]}"))
            return peer, state, events
        results = await asyncio.gather(*(read(peer) for peer in PEERS))
        incoming = []
        for peer, state, events in results:
            state["url"] = stack.urls[peer]
            self.state["peers"][peer] = state
            self.cursors[peer] = events["last_seq"]
            incoming.extend(events["events"])
        for event in sorted(incoming, key=lambda event: (event["time"], event["peer_id"], event["seq"])):
            self.event(event["kind"], **{k: v for k, v in event.items() if k != "kind"})

    async def watch(self):
        try:
            while True:
                await self.refresh()
                await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.monitor_error = exc

    async def stop_monitor(self):
        if self.monitor:
            self.monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor
            self.monitor = None

    async def wait_for(self, predicate, description, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.monitor_error:
                raise RuntimeError(f"Không đọc được dữ liệu peer: {self.monitor_error}")
            result = await predicate()
            if result:
                return result
            await asyncio.sleep(0.08)
        raise TimeoutError(description)

    async def job(self, peer, fid):
        assert self.stack
        result = await self.stack.api(peer, "GET", f"/api/downloads/{fid}")
        if result["status"] == "failed":
            raise RuntimeError(f"{peer}: {result.get('error', 'download failed')}")
        return result

    async def completed(self, peer, fid):
        async def done():
            state = await self.job(peer, fid)
            return state if state["status"] == "completed" else None
        return await self.wait_for(done, f"{peer} tải file quá hạn")

    async def download(self, peer, fid, parallelism=4):
        assert self.stack
        return await self.stack.api(peer, "POST", "/api/downloads", dict(file_id=fid, parallelism=parallelism))

    async def delay(self, peer, milliseconds):
        assert self.stack
        await self.stack.api(peer, "POST", f"/api/debug/upload-delay/{milliseconds}")

    async def case(self, scenario):
        await self.stop_monitor()
        if self.stack:
            await self.stack.close()
        run_id = f"visual-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.state.update(scenario=scenario, run_id=run_id, metadata=None, peers={}, events=[],
                          checks=[], artifact=None, phase="Khởi động", message="Tạo tracker và 3 peer độc lập.")
        self.cursors = dict.fromkeys(PEERS, 0)
        self.artifacts.clear()
        self.monitor_error = None
        self.stack = DemoStack(self.runtime_root / run_id)
        try:
            await self.stack.start()
        except BaseException:
            await self.stack.close()
            raise
        self.monitor = asyncio.create_task(self.watch())
        # A fresh source exists only in A's private shared directory.
        shared = self.stack.runtime / "peer-a" / "shared"
        shared.mkdir(parents=True, exist_ok=True)
        if self.active_source:
            original, selected = self.active_source
            name, size = selected["name"], selected["size"]
            await asyncio.to_thread(shutil.copyfile, original, shared / name)
            if await asyncio.to_thread(digest_file, shared / name) != selected["sha256"]:
                raise RuntimeError("File được chọn đã thay đổi trong lúc chuẩn bị nguồn A.")
        else:
            name, size = f"demo-{scenario}.bin", 64 * CHUNK_SIZE
            await asyncio.to_thread((shared / name).write_bytes, os.urandom(size))
        # At most 64 real chunks keeps all demos observable even for small PDFs.
        chunk_size = max(1, (size + 63) // 64)
        chunk_count = (size + chunk_size - 1) // chunk_size
        self.phase("Tạo metadata", f"Peer A chia {name} ({size:,} byte) thành {chunk_count} chunk và công bố SHA-256 lên tracker.")
        seeded = await self.stack.api("peer-a", "POST", "/api/seed", dict(source_name=name, chunk_size=chunk_size))
        metadata = seeded["metadata"]
        self.state["metadata"] = metadata
        fid = metadata["file_id"]

        if scenario != 2:
            self.phase("Chuẩn bị nguồn C", "Peer C tải bản sao từ A qua HTTP để có nguồn dự phòng.")
            await self.download("peer-c", fid, 8)
            await self.completed("peer-c", fid)
            self.check(True, "Peer C đã tải và xác minh đầy đủ bản sao từ A")

        if scenario == 1:
            await self.delay("peer-a", 320)
            await self.delay("peer-c", 320)
            self.phase("A + C → B", "B tải song song từ A và C; màu của chunk cho biết nguồn đã gửi.")
            await self.download("peer-b", fid)
            result = await self.completed("peer-b", fid)
            self.check(set(result["sources_used"]) == {"peer-a", "peer-c"}, "B nhận chunk trực tiếp từ cả A và C")
        elif scenario == 2:
            await self.delay("peer-a", 180)
            await self.delay("peer-b", 60)
            self.phase("A → B", "B tải với 1 worker; các chunk hợp lệ được chia sẻ ngay khi có.")
            await self.download("peer-b", fid, 1)
            async def partial():
                state = await self.job("peer-b", fid)
                return 6 <= state["downloaded_chunks"] < state["total_chunks"] and state["status"] == "running"
            await self.wait_for(partial, "Không bắt được trạng thái B đang tải dở", 20)
            self.phase("B → C trong lúc B đang tải", "C tham gia swarm và lấy các chunk B đã xác minh.")
            await self.download("peer-c", fid)
            await self.completed("peer-c", fid)
            await self.completed("peer-b", fid)
            await self.refresh()
            proof = [e for e in self.state["events"] if e["kind"] == "UPLOAD"
                     and e["peer_id"] == "peer-b" and e.get("file_id") == fid and e.get("completed") is False]
            c_result = await self.job("peer-c", fid)
            self.check(bool(proof) and "peer-b" in c_result["sources_used"], "B đã upload chunk cho C khi file của B chưa hoàn tất")
        elif scenario == 3:
            await self.delay("peer-a", 280)
            await self.delay("peer-c", 280)
            await self.stack.api("peer-c", "POST", "/api/debug/corrupt", dict(file_id=fid, chunk_index=1))
            self.event("CORRUPTION_INJECTED", "Đã đảo một byte của chunk #1 trên C; giữ nguyên hash metadata.", peer_id="peer-c", file_id=fid, chunk=1)
            self.phase("Phát hiện và tải lại chunk hỏng", "B kiểm tra SHA-256, loại chunk #1 sai từ C và thử nguồn A.")
            await self.download("peer-b", fid)
            result = await self.completed("peer-b", fid)
            await self.refresh()
            events = self.state["events"]
            rejected = any(e["kind"] == "INVALID_CHUNK" and e["peer_id"] == "peer-b"
                           and e.get("source") == "peer-c" and e.get("chunk") == 1 for e in events)
            replaced = any(e["kind"] == "DOWNLOAD_CHUNK" and e["peer_id"] == "peer-b"
                           and e.get("source") == "peer-a" and e.get("chunk") == 1 for e in events)
            self.check(result["invalid_chunks"] >= 1 and rejected, "SHA-256 phát hiện và từ chối chunk #1 hỏng từ C")
            self.check(replaced, "Cùng chunk #1 được tải lại thành công từ A")
        else:
            await self.delay("peer-a", 800)
            await self.delay("peer-c", 160)
            self.phase("B bắt đầu tải từ A + C", "Chờ A đang gửi chunk rồi ngừng phục vụ để quan sát B đổi nguồn.")
            await self.download("peer-b", fid, 2)
            async def in_flight():
                state = await self.job("peer-b", fid)
                active = await self.stack.api("peer-a", "GET", "/api/debug/active-uploads")
                return state["status"] == "running" and "peer-a" in state["sources_used"] and active["active_uploads"] > 0
            await self.wait_for(in_flight, "Không bắt được request đang tải từ A", 25)
            interruption = time.time()
            await self.stack.api("peer-a", "POST", "/api/debug/serving/false")
            self.phase("A ngừng phục vụ (HTTP 503)", "Tiến trình A vẫn chạy; B tự thử lại chunk bị gián đoạn từ C.")
            result = await self.completed("peer-b", fid)
            await self.refresh()
            retries = [e for e in self.state["events"] if e["kind"] == "RETRY"
                       and e["peer_id"] == "peer-b" and e.get("source") == "peer-a"
                       and e["time"] >= interruption]
            recovered = any(e["kind"] == "DOWNLOAD_CHUNK" and e["peer_id"] == "peer-b"
                            and e.get("source") == "peer-c" and e["time"] >= interruption
                            and any(r.get("chunk") == e.get("chunk") and r["time"] <= e["time"]
                                    for r in retries)
                            for e in self.state["events"])
            self.check(result["failed_requests"] > 0, "B nhận lỗi khi A ngừng phục vụ giữa lượt tải")
            self.check(recovered, "B tải lại chunk bị lỗi từ nguồn thay thế C")

        self.phase("Xác minh file cuối", "Đọc file đầu ra của B và tính lại SHA-256 độc lập.")
        output = self.stack.runtime / "peer-b" / "downloads" / name
        digest = await asyncio.to_thread(digest_file, output)
        verified = digest == metadata["file_hash"] and output.stat().st_size == metadata["file_size"]
        self.check(verified, "File ghép của B khớp kích thước và SHA-256 file gốc")
        self.state["artifact"] = dict(path=str(output), sha256=digest, verified=verified)
        self.artifacts["peer-b"] = output
        self.state["artifact"]["available_peers"] = ["peer-b"]
        if scenario == 2:
            c_output = self.stack.runtime / "peer-c" / "downloads" / name
            self.check(await asyncio.to_thread(digest_file, c_output) == metadata["file_hash"], "File cuối của C cũng khớp SHA-256")
            self.artifacts["peer-c"] = c_output
            self.state["artifact"]["available_peers"].append("peer-c")
        await self.refresh()

    async def run(self, scenario):
        cases = (1, 2, 3, 4) if scenario == 0 else (scenario,)
        try:
            for current in cases:
                await self.case(current)
                self.state["results"].append(dict(scenario=current, passed=True, message="Tất cả kiểm chứng đạt"))
                self.phase(f"Demo {current} đạt", "Dữ liệu đã truyền qua HTTP giữa các peer; file cuối đã xác minh SHA-256.")
                if current != cases[-1]:
                    await asyncio.sleep(1.5)
            self.state["status"] = "passed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.state.update(status="failed", phase="Demo gặp lỗi", error=str(exc), message=str(exc))
            self.state["results"].append(dict(scenario=self.state["scenario"], passed=False, message=str(exc)))
            self.event("ERROR", str(exc))
        finally:
            await self.stop_monitor()
            if self.stack and self.stack.client:
                # Always remove injected serving faults, including failed demos.
                for endpoint in ("/api/debug/serving/true", "/api/debug/upload-delay/60"):
                    with contextlib.suppress(Exception):
                        await self.stack.api("peer-a", "POST", endpoint)
                with contextlib.suppress(Exception):
                    await self.refresh()

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        await self.stop_monitor()
        if self.stack:
            await self.stack.close()
        if self.source_path:
            self.source_path.unlink(missing_ok=True)
            self.source_path = None


class RunRequest(BaseModel):
    scenario: Literal[0, 1, 2, 3, 4]


def create_app(controller: VisualDemo | None = None):
    demo = controller or VisualDemo()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await demo.close()

    app = FastAPI(title="DS02 · P2P live demo", lifespan=lifespan)
    app.state.demo = demo
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "visual-demo"}

    @app.get("/api/state")
    async def state():
        return demo.state

    def same_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and origin != f"http://{request.headers.get('host')}":
            raise HTTPException(403, "Demo controls require the local dashboard origin")

    @app.post("/api/source", status_code=201)
    async def upload_source(request: Request, name: str):
        same_origin(request)
        length = request.headers.get("content-length")
        try:
            declared = int(length) if length is not None else None
        except ValueError as exc:
            raise HTTPException(400, "Content-Length không hợp lệ.") from exc
        return await demo.upload(name, request.stream(), declared)

    @app.delete("/api/source")
    async def clear_source(request: Request):
        same_origin(request)
        demo.clear_source()
        return {"source": None}

    @app.get("/api/artifact/{peer}")
    async def download_artifact(peer: Literal["peer-b", "peer-c"]):
        path = await demo.artifact_path(peer)
        return FileResponse(path, media_type="application/octet-stream", filename=path.name,
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/run", status_code=202)
    async def run(request: Request, payload: RunRequest):
        same_origin(request)
        demo.start(payload.scenario)
        return {"status": "running"}

    @app.get("/")
    async def index():
        return FileResponse(ASSETS / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=ASSETS, check_dir=False), name="static")
    return app
