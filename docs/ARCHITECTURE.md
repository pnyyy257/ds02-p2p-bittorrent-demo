# DS02 Technical architecture

## Nodes and process isolation

`tracker`, `peer-a`, `peer-b`, `peer-c` each run a separate `uvicorn` process in a distinct container. Each peer has a unique Peer ID, internal hostname and private persistent directory. Only Seeder A is mounted to the source `sample_files` directory; B/C never read original files from shared host storage. The system also runs over separately bound localhost TCP ports for tests.

The optional browser demonstration (`python scripts/run_visual.py`) runs a local dashboard on `127.0.0.1:8080`. For each scenario, its controller creates a fresh `runtime/visual-<timestamp>-<uuid>/` directory and starts one tracker plus three independent peer processes on separately allocated loopback ports. Each peer retains its own data directory. Only A's shared directory receives the source, either a selected local file or the automatically generated 4 MiB sample. C obtains its initial replica through the chunk API in scenarios 1, 3 and 4. Scenario 2 starts C while B is still downloading.

The dashboard is a control and observation layer: it provisions the original source for A, starts downloads, injects demo faults and polls authenticated state/event APIs. It does not relay peer chunk transfers, and tracker responsibilities remain metadata and inventories. Per-stack credentials are generated in the backend and are not sent to browser assets or dashboard state. Existing Docker and CLI entry points operate independently.

The browser's native file picker accepts any extension and uploads the original bytes to `POST /api/source?name=...` using `application/octet-stream`. The controller accepts 8 bytes–32 MiB, preserves the original filename and bytes, and rejects control characters, path traversal and reserved Windows names. It stores the input privately under ignored `runtime/visual-inputs/`, then copies it only into each new A shared directory. B/C still obtain their chunks over peer HTTP. Chunk size is `ceil(file_size / 64)`, producing at most 64 chunks without padding or format conversion. The lower size limit leaves enough chunks for source sharing and failure demonstrations.

Peer downloaders accumulate HTTP body segments through EOF before checking a chunk. The read remains bounded to the expected chunk length plus one byte, so a valid large chunk split across transport reads is accepted while oversized responses remain bounded and rejected.

`GET /api/state` includes selected-source metadata for the dashboard lifetime; selection survives repeated scenarios, including all four cases, and resets when the dashboard restarts. `DELETE /api/source` returns to the automatic sample. `GET /api/artifact/peer-b` lets the browser download B's completed file after independent hash verification; C's completed output is also available for scenario 2. This final artifact download is separate from peer-to-peer chunk transport.

## API contract

| Provider | Endpoint | Purpose | Authentication |
|---|---|---|---|
| Tracker | `GET /health` | Liveness | None |
| Tracker | `GET /api/files` | Available file list | None |
| Tracker | `GET /api/files/{id}` | Immutable metadata + live peer inventory | None |
| Tracker | `PUT /api/announce` | Publish metadata and chunk inventory | `X-Tracker-Token` |
| Tracker | `DELETE /api/peers/{id}` | Graceful departure | `X-Tracker-Token` |
| Peer | `GET /health` | Liveness | None |
| Peer | `POST /api/seed` | Split local file and share chunks | `X-Admin-Token` |
| Peer | `GET /api/files` | Inspect local file summaries | `X-Admin-Token` |
| Peer | `GET /api/chunks/{id}/{n}` | Direct peer-to-peer chunk payload | `X-Peer-Token` |
| Peer | `POST /api/downloads` | Background parallel download | `X-Admin-Token` |
| Peer | `GET /api/downloads/{id}` | Download progress, failures, sources | `X-Admin-Token` |
| Peer | `POST /api/debug/corrupt` | Simulate malicious/damaged chunk | `X-Admin-Token` |
| Peer | `POST /api/debug/serving/{boolean}` | Simulate temporarily unreachable source | `X-Admin-Token` |
| Peer | `GET /api/debug/state` | Local chunk inventories, download/serving state | `X-Admin-Token` |
| Peer | `GET /api/debug/events?after=0&file_id=...` | Incremental transfer telemetry; optional file filter | `X-Admin-Token` |
| Peer | `GET /api/debug/active-uploads` | In-flight upload count for fault timing | `X-Admin-Token` |
| Peer | `POST /api/debug/upload-delay/{milliseconds}` | Set demo upload delay, bounded to 0–2000 ms | `X-Admin-Token` |

## Visual demonstration and telemetry

Each peer stores at most 2,048 recent events in memory. An event includes an increasing local `seq`, UNIX timestamp `time`, `kind` and `peer_id`; transfer events include `file_id`, chunk index and source where applicable. `GET /api/debug/events` returns events after a cursor and a `last_seq` cursor. This bounded diagnostic buffer is not a persistent audit log; normal process logs remain under the scenario runtime directory.

The controller collects state and events from all three peers. The browser draws transfer arrows from successful `DOWNLOAD_CHUNK` events and displays inventories, phase changes, failures and verification results. Demo 2 requires an actual B `UPLOAD` event with `completed=false` and C reporting B among its sources. Demo 3 checks both rejection of C's corrupted chunk #1 and successful receipt of that same index from A. Demo 4 waits for an active upload from A, disables serving to trigger HTTP 503, and checks that B retries an affected chunk through C. This is a serving failure; A's process stays alive. The injected serving fault is removed in cleanup.

After each scenario, B's output size and SHA-256 are checked against metadata independently of the transfer status; scenario 2 also verifies C's final hash. Each new scenario replaces the prior stack with a fresh one while retaining the chosen source. The dashboard remains available after completion until Ctrl+C. Shutdown terminates only its owned subprocesses and preserves downloaded files and logs. `python scripts/run_visual.py --check` executes the same four scenarios without the browser, reports their checks and exits with a nonzero status on failure. Add `--file <path>` to check the four scenarios against a selected local file.

## Metadata structure

```json
{
  "file_id": "SHA256-hex-of-original-file",
  "file_name": "sample.bin",
  "file_size": 4194304,
  "chunk_size": 65536,
  "chunk_count": 64,
  "chunk_hashes": ["SHA256-hex-of-chunk-0", "..."],
  "file_hash": "SHA256-hex-of-original-file",
  "tracker_url": "http://tracker:8000"
}
```

This is an illustrative structure; placeholders above are NOT valid real SHA-256 values. Actual metadata is generated automatically by the Seeder. Tracker rejects conflicting metadata for a given file ID.

## Chunk transfer sequence

```mermaid
sequenceDiagram
    participant A as Seeder A
    participant T as Tracker
    participant B as Peer B
    participant C as Peer C
    A->>T: PUT announce(file + chunks 0..N)
    C->>T: GET file metadata + peers
    C->>A: GET chunk 0 (direct bytes)
    A-->>C: chunk bytes
    C->>C: verify SHA256; atomic write
    C->>T: PUT announce(chunks={0})
    B->>T: GET file metadata + availability
    B->>A: GET chunk 2
    B->>C: GET chunk 0 (from incomplete C)
    A-->>B: bytes
    C-->>B: bytes
    B->>B: verify chunks, merge in order
    B->>B: SHA256(final file) == file_hash
```

## Recovery and failure model

- **Crash-stop / transient loss:** 2s connect timeout, 6s request timeout, bounded retries (default 12) across discovered alternative peers; TTL expires silent peers from listing.
- **Byzantine data / transmission corruption:** digest mismatch means reject chunk and try another source. Peer keeps an invalid corrupted chunk marked as advertised (intentional for demo); receivers do not accept it.
- **Restart/recovery:** chunk data and checkpoint JSON persist. At peer startup, every claimed chunk is rehashed and only valid ones are made available. Caller can reissue `/api/downloads` to continue missing chunks.
- **Tracker crash:** existing transfers require tracker lookup for each chunk, so they may pause/fail. On tracker recovery, online peers reannounce every three seconds; restart pending downloads if needed. No replicated tracker in this prototype.
- **Ordering and duplicates:** workers pull unique indexes from local queue; chunk hashes map each index to a unique payload. Atomic writes and checksums give idempotent chunk receipt. No global total order is required for independent immutable chunks.

## Security and non-goals

Three shared credentials keep tracker's write API, peers' transfer API and local control API distinct. Use strong independent secrets when configuring `.env`; an exposed HTTP peer admin API and shared tokens are NOT secure over untrusted Internet links. Restrict networks or add TLS and per-peer cryptographic credentials for a real deployment. Do not allow arbitrary server-side paths: Seeder only reads simple filenames from its own `/data/shared` directory. The prototype intentionally omits NAT traversal, DHT, choking, torrent file compatibility and tit-for-tat.
