# Test plan — DS02 P2P File-Sharing

## General conditions

- Tracker and 3 peers are separate processes connected by HTTP; test suite spins them up on distinct loopback ports with separate per-peer storage directories.
- Source files exist in Peer A's shared folder only; B and C download the bytes over HTTP from peers.
- Test files use deterministic pseudo-random data to compare bytes exactly; SHA-256 is independently checked.
- Execute from root: `python -m pip install -e ".[dev]"`, then `python -m pytest -v`.
- For Docker live demonstrations: `python scripts/make_sample.py`, `docker compose up -d --build`, `python scripts/demo.py`.
- For the browser demonstration: `python scripts/run_visual.py`, then open `http://127.0.0.1:8080`. Select an individual scenario or all four. Use `--port 8081` to choose another dashboard port.
- To verify the same four visual scenarios without a browser: `python scripts/run_visual.py --check`. Add `--file <path>` to use a real PDF, DOCX or other local file. This starts real tracker/peer subprocesses, checks scenario evidence and output hashes, then exits.

## Visual demonstration acceptance checks

Each scenario must use a fresh `runtime/visual-<timestamp>-<uuid>/` directory, one independent tracker process, three independent peer processes and separate peer data directories. Only A receives the original source before transfer; B/C must not read the controller's selected input or A's shared path. The expected results below are acceptance criteria, not a recorded test run.

| ID | Scenario | Required evidence |
|---|---|---|
| V01 | B downloads from A and C | B's `sources_used` contains both A and C; successful chunk events show each source |
| V02 | B shares before finishing | B emits `UPLOAD` with `completed=false`; C's sources include B; both final hashes match |
| V03 | Corrupt chunk detection and replacement | C's chunk #1 is corrupted; B emits `INVALID_CHUNK` for C/index 1 then successful `DOWNLOAD_CHUNK` for A/index 1 |
| V04 | Source interruption during transfer | A has an active upload before serving is disabled; B records failed requests and receives a retried chunk from C after interruption |
| V05 | Final integrity | Every scenario checks B's output length and independently calculated SHA-256 against metadata |
| V06 | UI and lifecycle | Inventories update, arrows follow actual transfer events, checks appear; repeating a scenario uses fresh data; Ctrl+C stops only owned processes and retains artifacts/logs |
| V07 | Diagnostic access | Missing admin token is rejected for state/events; events have increasing per-peer cursors and a bounded 2,048-event buffer; browser state/assets contain no stack credentials |
| V08 | Custom file transfer | Select a PDF, DOCX or arbitrary extension; raw upload preserves filename, exact bytes and SHA-256; all four cases use that source with fresh peer directories; B's downloaded artifact opens normally |
| V09 | Source selection limits | Accept 8 bytes–32 MiB; reject files outside limits, control characters, path traversal and reserved Windows names; chunking has at most 64 chunks and adds no bytes |
| V10 | Selection and artifact lifecycle | Selection persists across scenarios; “Dùng file mẫu” restores automatic 4 MiB input; restart resets selection; only a completed, independently hash-verified B output is downloadable |

V04 injects HTTP 503 through `/api/debug/serving/false`; A's process stays alive and serving is restored in cleanup. The Docker process-stop variation remains a separate demonstration. The dashboard is an observer/controller; chunks must continue to travel directly between peers and never through the dashboard or tracker. See [DEMO_VISUAL.md](DEMO_VISUAL.md) for the presentation script and evidence checklist.

For V08–V10, the browser uploads the original binary to `POST /api/source?name=...`; inputs stay under `runtime/visual-inputs/` and are copied only into A's shared directory for each scenario. Check selected-source metadata in `GET /api/state`, reset with `DELETE /api/source`, and fetch B's verified output through `GET /api/artifact/peer-b`. C's completed artifact is optional in scenario 2. Include a small valid document as well as a larger file so adaptive chunking is exercised; compare original and downloaded bytes/hashes in addition to checking the extension.

## Functional tests (8+)

| ID | Test | Automated test / scenario | Expected |
|---|---|---|---|
| F01 | SHA-256 | `test_sha256_matches_standard` | SHA-256 equals Python hashlib |
| F02 | Valid metadata | `test_metadata_contains_required_fields` | Required properties present |
| F03 | Chunk count validation | `test_invalid_chunk_count_rejected` | Metadata rejected if inconsistent |
| F04 | Chunk integrity | `test_corrupted_chunk_rejected` | Damaged bytes rejected |
| F05 | File and atomic chunk persistence | `test_file_digest_and_atomic_storage` | Exact bytes + hash |
| F06 | JSON checkpoint | `test_json_atomic_persistence` | Persist/reload chunk list |
| F07 | Empty file | `test_empty_file_metadata` | Zero chunks; valid SHA-256 |
| F08 | Tracker join/discovery | `test_tracker_register_and_discover` | ID, URL, chunks returned |
| F09 | File transfer from 2 peers | `test_parallel_two_peer_download_with_hash_verification` | B downloads from A/C concurrently, final bytes match |
| F10 | Restart checkpoint recovery | `test_restart_recovers_verified_chunk_state` | Recreates verified chunks from disk |
| F11 | Lease expiry | `test_tracker_lease_expires` | Silent peer removed from active view |

## Concurrency/conflict tests (2+)

| ID | Test | Expected |
|---|---|---|
| C01 | `test_two_concurrent_peer_announcements` | Tracker retains both announcements without lost update |
| C02 | `test_upload_before_finish_and_simultaneous_download` | C receives chunk from incomplete B while B downloads |
| C03 | Duplicate download during active job in `test_source_failure_recovery_and_concurrency_guard` | 409 (no duplicate job) |

**Consistency model:** immutable file metadata by file ID; monotonic chunk inventory after validation; eventual consistency of the chunk map at tracker (announced per chunk + heartbeat); local checkpoint updates are serialized via `asyncio.Lock`, and chunk files use atomic rename.

## Fault tolerance/recovery tests (2+)

| ID | Test | Expected |
|---|---|---|
| R01 | `test_detect_corruption_and_retry_other_source` | Integrity failure logged, retry healthy alternative; merged bytes match |
| R02 | `test_source_failure_recovery_and_concurrency_guard` | One source disabled mid-transfer, alternative serves missing pieces |
| R03 | `test_restart_recovers_verified_chunk_state` | Recover validated checkpoint after process recreation |

**Live process-failure variation:** `docker compose stop peer-a` during B download while C already has the content. Restart A with `docker compose start peer-a`. For a reproducible automatic demo without physically stopping a container, `/api/debug/serving/false` returns 503 and triggers retry/fallback; it is explicitly a *simulated* stop.

## Security/invalid-data tests (2+)

| ID | Test | Expected |
|---|---|---|
| S01 | `test_filename_traversal_rejected` and integration invalid path | `../secret`, absolute paths rejected |
| S02 | `test_api_rejects_missing_or_wrong_tracker_token` | Tracker announce requires token |
| S03 | `test_security_invalid_filename_and_unauthorized_transfer` | Chunk bytes unavailable without transfer token; admin actions require admin token |
| S04 | `test_tracker_rejects_invalid_announcement` | Duplicate/out-of-range chunk numbers refused |

## Performance/scalability test (1+)

`test_performance_throughput_metric` measures elapsed time and computes bytes/s. For an experiment comparing 1 vs 4 simultaneous chunk workers, run `scripts/performance.py` on a **fresh** set of peer data. The network's simulated upload delay is controlled with `UPLOAD_DELAY_MS` in `.env`. Report machine hardware, file size, delays, worker count, download times, and sources. This benchmark is illustrative, not a statistically controlled study; later runs may benefit from prior peer replication and therefore are not directly comparable unless topology/source availability is controlled.

## Source distribution verification

Windows / Python 3.14.4, 2026-10-19: the source ZIP was extracted into a fresh directory containing spaces and installed with `pip install -c constraints-demo.txt -e ".[dev]"` in a separate virtual environment.

- `python -m pytest -q`: **50 passed** in 32.19 seconds; one dependency deprecation warning.
- `python scripts/run_local.py`: all four network scenarios passed from the extracted source.
- `python scripts/run_visual.py --check --file docs/input_specification.pdf`: all four visual scenarios passed from the extracted source; final PDF SHA-256 matched `aa46fa6361e83bc01633d4d8a1e4aab562ec305a2ca8f4da9febdbb20222b02c`.
- Dashboard smoke check: HTML, JavaScript, CSS and `/api/state` returned successfully from the extracted source.
- `python -m pip check`: no broken requirements.
- Three packaging tests verify exclusions for runtime/user data and environment secrets, per-file SHA-256, reproducible archives and required source files.

The ZIP includes a source checksum manifest. Runtime files, uploaded documents, virtual environments and the stale generated report PDF are excluded; `docs/REPORT.md` is the current report. Direct execution on macOS/Linux has not been verified in this release check.

## Recorded verification with custom file selection

Windows, Python 3.14.4, 2026-10-19:

- `python -m pytest -q`: **47 passed** in 32.73 seconds; one dependency deprecation warning.
- `python scripts/run_local.py`: all four original network scenarios passed.
- `python scripts/run_visual.py --check --file docs/input_specification.pdf`: all four scenarios passed with the existing PDF, preserving its original SHA-256.
- Chrome headless selected a valid DOCX named `Tài liệu demo 100%.docx`, reloaded the page, ran Demo 2 and downloaded B's file through the browser. Filename and bytes matched; C's artifact also matched. Seven-byte input was rejected without changing the prior selection; sample reset worked; no JavaScript exceptions or horizontal overflow at 390px.
- A minimum eight-byte file passed all four scenarios. A maximum 32 MiB file passed Demo 1 with 512 KiB chunks and an independently verified output hash.

`tests/test_visual_files.py` covers input isolation, exact names/bytes/hashes, invalid Windows names, origins, size limits, interrupted/oversized upload cleanup, mutual exclusion, verified attachment downloads and later output corruption. `tests/test_chunk_stream.py` uses real TCP responses to cover delayed partial transport reads and oversized responses with/without declared lengths. Screenshots of the custom chooser and results are at `runtime/visual-file-selected.png`, `visual-file-result.png` and `visual-file-mobile.png`.

## Recorded baseline before custom file selection

Verified on Windows with Python 3.14.4 on 2026-10-19 after adding the visual dashboard, before adding custom source upload and artifact download:

- `python -m pytest -q`: **28 passed** in 28.14 seconds; one dependency deprecation warning.
- `python scripts/run_local.py`: all four existing network scenarios passed.
- `python scripts/run_visual.py --check`: all four visual-controller network scenarios passed, including independent output hashes.
- Chrome headless: Demo 3 reached 4/4 checks with no JavaScript exceptions; desktop and 390px layouts had no horizontal overflow. C's deliberately damaged chunk remained red while B completed 64 valid chunks.
- After tightening Demo 4's event timing/source checks, a separate dashboard run passed and exposed A's serving outage in live state before B recovered the same chunks from C.

The additional tests in `test_telemetry.py` and `test_visual.py` cover authenticated telemetry, bounded event retention/cursors, incomplete upload evidence, dashboard origin/host/input checks, overlapping runs, fault cleanup and partially failed process startup. Presentation screenshots from this verification are under `runtime/visual-*.png`.

Historical baseline in the provided development container: **20 passed** (`PYTHONPATH=src pytest -q`). Results depend on environment and should be reproduced on the presentation machine. The automated tests exercise actual network API calls over local TCP (not only mocked functions).

## Demo recording checklist

1. Capture Docker Compose 4-process status.
2. Show tracker GET metadata and peer inventory; note there are no file bytes at tracker.
3. Show Peers A,C as sources for Peer B's chunk stream (`sources_used`).
4. Show `UPLOAD peer=peer-b` while its own download remains `running`.
5. Show peer A unavailable/503 and peer B retry + source peer C.
6. Show `INVALID_CHUNK` and a successful retry elsewhere.
7. Show `DOWNLOAD_COMPLETE ... hash=OK` and compare file hashes/bytes.
8. Show restart and `RECOVER` log while retaining verified chunks.
