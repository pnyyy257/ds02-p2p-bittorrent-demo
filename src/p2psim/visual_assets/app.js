"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const peerIds = ["peer-a", "peer-b", "peer-c"];
  const peerColors = { "peer-a": "#5ce2c5", "peer-b": "#70b5ff", "peer-c": "#b39aff" };
  const scenarioHints = {
    1: "A và C có sẵn file. B ghép các chunk nhận từ hai nguồn và kiểm tra hash toàn file.",
    2: "B bắt đầu tải từ A. Khi B mới có một phần file, C sẽ nhận các chunk đó trực tiếp từ B.",
    3: "C trả về một chunk hỏng có chủ đích. B phải phát hiện sai SHA-256, loại bỏ và tải lại từ nguồn hợp lệ.",
    4: "A dừng phục vụ giữa chừng và trả HTTP 503. B phải thử lại, chuyển sang C và hoàn tất file."
  };
  const phaseNames = {
    idle: "Sẵn sàng", preparing: "Đang chuẩn bị môi trường", starting: "Khởi động tracker và peer",
    seeding: "Chuẩn bị nguồn chia sẻ", downloading: "Đang truyền các chunk", verifying: "Kiểm tra file đầu ra",
    completed: "Hoàn tất kiểm chứng", passed: "Demo thành công", failed: "Demo chưa đạt", stopping: "Dọn dẹp lượt chạy",
    seed_c: "Chuẩn bị file cho Peer C", preparing_c: "Chuẩn bị file cho Peer C"
  };
  const kindNames = {
    DOWNLOAD_CHUNK: "Nhận chunk", UPLOAD_CHUNK: "Gửi chunk", UPLOAD: "Gửi chunk", SEED: "Tạo nguồn chia sẻ", DOWNLOAD_START: "Bắt đầu tải", DOWNLOAD_STARTED: "Bắt đầu tải",
    DOWNLOAD_COMPLETE: "Tải hoàn tất", DOWNLOAD_COMPLETED: "Tải hoàn tất", INVALID_CHUNK: "Chunk sai hash",
    CHUNK_INVALID: "Chunk sai hash", DOWNLOAD_INVALID_CHUNK: "Chunk sai hash", HASH_MISMATCH: "Hash không khớp",
    DOWNLOAD_RETRY: "Thử tải lại", RETRY: "Thử lại", SOURCE_FAILED: "Nguồn tải gặp lỗi", DOWNLOAD_FAILED: "Lỗi tải file",
    REQUEST_FAILED: "Yêu cầu thất bại", DOWNLOAD_ERROR: "Lỗi yêu cầu chunk", SERVING_DISABLED: "Ngừng cấp dữ liệu",
    PEER_STOPPED: "Peer ngừng phục vụ", PEER_STARTED: "Peer khởi động", ANNOUNCE: "Cập nhật tracker",
    ANNOUNCED: "Cập nhật tracker", REGISTER: "Đăng ký file", FILE_REGISTERED: "Đăng ký file",
    SHARE: "Chia sẻ file", FILE_SHARED: "Chia sẻ file", SCENARIO_START: "Bắt đầu demo", SCENARIO_PASSED: "Demo đạt",
    SCENARIO_FAILED: "Demo chưa đạt", CHECK: "Kiểm chứng", CHECK_PASS: "Kiểm chứng đạt", CHECK_FAIL: "Kiểm chứng lỗi", PHASE: "Giai đoạn demo",
    SERVING_CHANGED: "Đổi trạng thái phục vụ", CORRUPTION_INJECTED: "Tạo chunk hỏng", CORRUPT_CHUNK: "Gửi chunk hỏng", FAULT_INJECTED: "Kích hoạt tình huống lỗi"
  };
  let selected = 1;
  let currentState = null;
  let currentRun = null;
  let previousScenario = null;
  let maxEventId = -1;
  let firstEventTime = null;
  let eventTotal = 0;
  let inFlight = false;
  let pendingRun = false;
  let selectedSource = null;
  let sourceOperation = null;
  let sourceFile = null;
  let sourceProgress = null;
  let sourceError = "";
  let sourceRevision = 0;
  let connected = false;
  let recentFlows = new Map();
  let chunkSources = new Map();
  let rejectedChunks = new Map();
  let damagedChunks = new Set();
  let announcedStatus = "";

  function setText(id, value) { $(id).textContent = value; }
  function shortPeer(value) {
    if (!value) return "—";
    const text = String(value);
    return peerIds.includes(text) ? text.slice(-1).toUpperCase() : text;
  }
  function bytes(value) {
    const n = Number(value) || 0;
    if (n >= 1048576) return `${(n / 1048576).toFixed(1)} MiB`;
    if (n >= 1024) return `${Math.round(n / 1024)} KiB`;
    return `${n} B`;
  }
  function relativeTime(value) {
    const time = Number(value);
    if (!Number.isFinite(time) || firstEventTime === null) return "—";
    const seconds = Math.max(0, time - firstEventTime);
    return `+${seconds.toFixed(2)}s`;
  }
  function getKind(event) { return String(event.kind || "EVENT").toUpperCase(); }
  function eventClass(kind) {
    if (/INVALID|CORRUPT|MISMATCH|CHECK_FAIL/.test(kind)) return "error";
    if (/FAILED|ERROR|RETRY|DISABLED|STOPPED|FAULT/.test(kind)) return "warning";
    if (/COMPLETE|PASSED|VERIFIED|CHECK_PASS/.test(kind)) return "success";
    return "";
  }
  function setConnection(ok) {
    connected = ok;
    $("connection").className = `connection ${ok ? "connected" : "disconnected"}`;
    setText("connection-text", ok ? "Local · Đã kết nối" : "Mất kết nối máy chủ");
    updateControls();
  }
  function updateControls() {
    const running = pendingRun || currentState?.status === "running";
    const sourceBusy = sourceOperation !== null || currentState?.upload_in_progress === true;
    const busy = running || sourceBusy;
    document.querySelectorAll(".scenario").forEach((button) => { button.disabled = busy; });
    $("run-all").disabled = busy || !connected;
    $("run-selected").disabled = busy || !connected;
    $("choose-source").disabled = busy || !connected;
    $("source-input").disabled = busy || !connected;
    $("reset-source").disabled = busy || !connected || !selectedSource?.custom;
    setText("run-label", running ? "Đang chạy…" : sourceBusy ? "Đang xử lý file…" : `Chạy demo ${selected}`);
  }
  function renderSource() {
    const uploading = sourceOperation === "upload";
    const remoteUpload = currentState?.upload_in_progress === true;
    const active = uploading && sourceFile ? { name: sourceFile.name, size: sourceFile.size, custom: true } : selectedSource;
    $("source-picker").classList.toggle("custom", active?.custom === true);
    $("source-picker").classList.toggle("uploading", uploading || remoteUpload);
    setText("source-type", active?.custom ? "FILE CỦA BẠN" : "FILE MẪU");
    setText("source-name", active?.name || "File mẫu tự động");
    $("source-name").title = active?.name || "File mẫu tự động";
    setText("source-meta", active?.custom ? `${bytes(active.size)} · ${Number(active.size).toLocaleString("vi-VN")} byte · Giữ nguyên tên và nội dung` : "4 MiB · Tạo mới khi chạy demo");
    $("source-meta").title = active?.sha256 ? `SHA-256: ${active.sha256}` : "";
    let status = active?.custom ? "Đã chọn · Sẵn sàng chạy demo" : "Sẵn sàng dùng file mẫu";
    if (currentState?.status === "running") status = "Đang dùng trong lượt demo";
    if (remoteUpload) status = "Máy chủ đang nhận và kiểm tra file…";
    if (sourceOperation === "clear") status = "Đang chuyển về file mẫu…";
    if (uploading) status = sourceProgress?.loaded >= sourceFile.size ? "Đã gửi file · Chờ máy chủ kiểm tra…" : "Đang gửi file lên máy chủ local…";
    if ($("source-status").textContent !== status) setText("source-status", status);
    $("source-upload").hidden = !uploading;
    const progress = sourceProgress?.total ? Math.min(100, Math.floor(sourceProgress.loaded / sourceProgress.total * 100)) : 0;
    $("source-progress-fill").style.width = `${progress}%`;
    $("source-progress").setAttribute("aria-valuenow", String(progress));
    setText("source-progress-text", `${progress}%`);
    $("source-error").hidden = !sourceError;
    setText("source-error", sourceError);
    updateControls();
  }
  function sourceRequestError(status, data) {
    if (status === 413) return "File vượt giới hạn 32 MiB. Hãy chọn file nhỏ hơn.";
    if (status === 409) return "Một demo hoặc lượt gửi file khác đang chạy. Hãy đợi hoàn tất rồi thử lại.";
    const detail = data?.detail || data?.error;
    if (typeof detail === "string") return detail;
    if (detail) return JSON.stringify(detail);
    return `Máy chủ từ chối file (HTTP ${status}). Hãy kiểm tra tên file và giới hạn 8 byte–32 MiB.`;
  }
  async function uploadSource(file) {
    if (!file || sourceOperation || currentState?.status === "running" || currentState?.upload_in_progress) return;
    sourceError = "";
    if (file.size < 8 || file.size > 32 * 1024 * 1024) {
      sourceError = file.size < 8 ? "File cần có ít nhất 8 byte để thể hiện đủ 4 kịch bản. Hãy chọn file khác." : "File vượt giới hạn 32 MiB. Hãy chọn file nhỏ hơn.";
      renderSource();
      return;
    }
    sourceOperation = "upload";
    sourceFile = file;
    sourceProgress = { loaded: 0, total: file.size };
    sourceRevision += 1;
    renderSource();
    try {
      const source = await new Promise((resolve, reject) => {
        const request = new XMLHttpRequest();
        request.open("POST", `/api/source?name=${encodeURIComponent(file.name)}`);
        request.setRequestHeader("Content-Type", "application/octet-stream");
        request.responseType = "json";
        request.timeout = 120000;
        request.upload.addEventListener("progress", (event) => {
          if (event.lengthComputable) sourceProgress = { loaded: event.loaded, total: event.total };
          renderSource();
        });
        request.addEventListener("load", () => {
          if (request.status >= 200 && request.status < 300 && request.response?.custom) resolve(request.response);
          else reject(new Error(sourceRequestError(request.status, request.response)));
        });
        request.addEventListener("error", () => reject(new Error("Không gửi được file. Kiểm tra kết nối máy chủ local và thử lại.")));
        request.addEventListener("timeout", () => reject(new Error("Gửi file quá thời gian chờ. Hãy kiểm tra kết nối và thử lại.")));
        request.addEventListener("abort", () => reject(new Error("Lượt gửi file đã bị hủy. File trước đó vẫn được giữ lại.")));
        request.send(file);
      });
      selectedSource = source;
    } catch (error) {
      sourceError = `Không thể chọn file: ${error.message}`;
    } finally {
      sourceRevision += 1;
      sourceOperation = null;
      sourceFile = null;
      sourceProgress = null;
      $("source-input").value = "";
      renderSource();
      await poll();
    }
  }
  async function resetSource() {
    if (sourceOperation || currentState?.status === "running" || currentState?.upload_in_progress) return;
    sourceOperation = "clear";
    sourceError = "";
    sourceRevision += 1;
    renderSource();
    try {
      const response = await fetch("/api/source", { method: "DELETE", signal: AbortSignal.timeout(10000) });
      if (!response.ok) {
        let data = null;
        try { data = await response.json(); } catch (_) { /* The status still explains non-JSON failures. */ }
        throw new Error(sourceRequestError(response.status, data));
      }
      selectedSource = null;
      $("source-input").value = "";
    } catch (error) {
      sourceError = `Không thể dùng file mẫu: ${error.message}`;
    } finally {
      sourceRevision += 1;
      sourceOperation = null;
      renderSource();
      await poll();
    }
  }
  function selectScenario(value) {
    selected = value;
    document.querySelectorAll(".scenario").forEach((button) => {
      const active = Number(button.dataset.scenario) === selected;
      button.classList.toggle("selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
    setText("scenario-hint", scenarioHints[selected]);
    updateControls();
  }
  function resetEvents() {
    maxEventId = -1;
    firstEventTime = null;
    eventTotal = 0;
    recentFlows.clear();
    chunkSources.clear();
    rejectedChunks.clear();
    damagedChunks.clear();
    $("event-log").replaceChildren();
    setText("event-count", "0 sự kiện");
  }
  function renderEvents(events) {
    const newEvents = events.filter((event) => Number(event.id) > maxEventId).sort((a, b) => Number(a.id) - Number(b.id));
    if (!newEvents.length) return;
    if (firstEventTime === null) firstEventTime = Number(newEvents[0].time) || 0;
    const fragment = document.createDocumentFragment();
    for (const event of newEvents) {
      maxEventId = Math.max(maxEventId, Number(event.id));
      eventTotal += 1;
      const kind = getKind(event);
      const receiver = event.peer_id;
      const source = event.source;
      const chunkKey = `${receiver}:${event.file_id || ""}:${event.chunk}`;
      const eventAge = Date.now() - Number(event.time) * 1000;
      if (kind === "CORRUPTION_INJECTED" && peerIds.includes(receiver) && Number.isInteger(event.chunk)) damagedChunks.add(chunkKey);
      if (kind === "DOWNLOAD_CHUNK" && peerIds.includes(receiver) && peerIds.includes(source)) {
        if (eventAge >= -1000 && eventAge < 1600) recentFlows.set(`${source}:${receiver}`, { source, receiver, chunk: event.chunk, expires: Number(event.time) * 1000 + 1600 });
        chunkSources.set(chunkKey, source);
        rejectedChunks.delete(`${receiver}:${event.chunk}`);
        damagedChunks.delete(chunkKey);
      }
      if (/INVALID|MISMATCH/.test(kind) && Number.isInteger(event.chunk) && eventAge >= -1000 && eventAge < 2200) rejectedChunks.set(`${receiver}:${event.chunk}`, Number(event.time) * 1000 + 2200);
      const row = document.createElement("div");
      row.className = `event-row ${eventClass(kind)}`;
      const time = document.createElement("span");
      time.className = "event-time";
      time.textContent = relativeTime(event.time);
      if (Number.isFinite(Number(event.time))) time.title = new Date(Number(event.time) * 1000).toLocaleTimeString("vi-VN", { hour12: false });
      const peer = document.createElement("span");
      peer.className = `event-peer ${peerIds.includes(receiver) ? receiver : ""}`;
      peer.textContent = peerIds.includes(receiver) ? `Peer ${shortPeer(receiver)}` : (receiver === "demo" ? "Hệ thống" : receiver || "Hệ thống");
      const label = document.createElement("span");
      label.className = "event-kind";
      label.textContent = kindNames[kind] || kind.replaceAll("_", " ");
      label.title = kind;
      const detail = document.createElement("span");
      detail.className = "event-detail";
      const fallback = [
        source ? `Nguồn ${shortPeer(source)}` : "",
        Number.isInteger(event.chunk) ? `chunk #${event.chunk}` : "",
        Number.isFinite(event.size) ? bytes(event.size) : "",
        kind === "INVALID_CHUNK" ? "SHA-256 không khớp, loại bỏ" : "",
        kind === "UPLOAD" && event.completed === false ? "File chưa hoàn tất (completed=false)" : "",
        kind === "SERVING_CHANGED" ? (event.serving_enabled === false ? "Dừng cấp dữ liệu · HTTP 503" : "Đã bật cấp dữ liệu") : "",
        event.attempt ? `Lần thử ${event.attempt}` : "",
        event.error ? String(event.error) : ""
      ].filter(Boolean).join(" · ");
      detail.textContent = event.message || fallback || "—";
      row.append(time, peer, label, detail);
      fragment.append(row);
    }
    const log = $("event-log");
    log.querySelector(".log-empty")?.remove();
    log.append(fragment);
    while (log.children.length > 500) log.firstElementChild.remove();
    setText("event-count", `${eventTotal} sự kiện${eventTotal > 500 ? " · hiện 500 gần nhất" : ""}`);
    if ($("follow-events").checked) log.scrollTop = log.scrollHeight;
  }
  function updatePeer(id, peer, metadata) {
    const card = $(id);
    const field = (name) => card.querySelector(`[data-field="${name}"]`);
    const file = (peer?.files || []).find((item) => item.file_id === metadata?.file_id);
    const total = Number(metadata?.chunk_count) || 0;
    const chunks = new Set(Array.isArray(file?.chunks) ? file.chunks : []);
    const count = chunks.size;
    const damaged = [...chunks].filter((index) => damagedChunks.has(`${id}:${metadata?.file_id || ""}:${index}`)).length;
    card.classList.toggle("has-damage", damaged > 0);
    const valid = count - damaged;
    const percent = total ? Math.min(100, Math.round(valid / total * 100)) : 0;
    const stats = file?.status || {};
    field("percent").replaceChildren(document.createTextNode(String(percent)), Object.assign(document.createElement("span"), { textContent: "%" }));
    field("chunk-count").textContent = `${valid} / ${total} ${damaged ? "hợp lệ" : "chunk"}`;
    field("progress-bar").style.width = `${percent}%`;
    const running = stats.status === "downloading" || stats.status === "running";
    field("state").textContent = damaged ? `${damaged} chunk bị gây hỏng` : file?.completed ? "Đã hoàn tất" : running ? "Đang tải file" : count > 0 ? "Đã có một phần file" : peer ? "Chưa có chunk" : "Chưa khởi tạo";
    field("online").textContent = !peer ? "Chờ" : peer.serving_enabled === false ? "Dừng cấp" : "Trực tuyến";
    field("online").className = `peer-online ${!peer ? "" : peer.serving_enabled === false ? "stopped" : "ready"}`;
    field("uploads").textContent = String(peer?.active_uploads || 0);
    let sources = stats.sources_used || [];
    if (!Array.isArray(sources)) sources = typeof sources === "object" ? Object.keys(sources) : [sources];
    field("sources").textContent = sources.map(shortPeer).join(", ") || "—";
    const invalid = Number(stats.invalid_chunks) || 0;
    const failed = Number(stats.failed_requests) || 0;
    field("footer").textContent = damaged ? `Kho có ${count} chunk · ${damaged} chunk hỏng` : peer?.serving_enabled === false ? "HTTP 503 · Tạm dừng phục vụ" : invalid || failed ? `${invalid} chunk bị loại · ${failed} yêu cầu lỗi` : file?.completed ? "✓ Đủ chunk · SHA-256 đã xác thực" : "Kho dữ liệu độc lập";
    field("footer").title = field("footer").textContent;
    const cRole = currentState?.scenario === 2 ? "Nhận chunk từ B" : "Nguồn dữ liệu";
    field("role").textContent = id === "peer-a" ? "Nguồn dữ liệu" : id === "peer-b" ? "Peer nhận file" : cRole;
    const grid = field("chunks");
    const maxVisible = 128;
    const gridTotal = total ? Math.min(total, maxVisible) : 24;
    grid.querySelector(".chunk-summary")?.remove();
    while (grid.children.length > gridTotal) grid.lastElementChild.remove();
    while (grid.children.length < gridTotal) grid.append(document.createElement("span"));
    [...grid.children].forEach((cell, index) => {
      const present = chunks.has(index);
      const damaged = damagedChunks.has(`${id}:${metadata?.file_id || ""}:${index}`);
      const rejected = !present && (rejectedChunks.get(`${id}:${index}`) || 0) > Date.now();
      cell.className = `chunk${present ? " present" : ""}${rejected ? " rejected" : ""}${damaged ? " damaged" : ""}`;
      const source = chunkSources.get(`${id}:${metadata?.file_id || ""}:${index}`);
      const sourceColor = damaged ? "#ff8b98" : present ? peerColors[source || id] : "";
      cell.style.backgroundColor = sourceColor || "";
      cell.style.borderColor = sourceColor || "";
      cell.title = total ? `Chunk #${index}: ${damaged ? "chunk bị gây hỏng; SHA-256 không còn khớp" : present ? "đã xác thực SHA-256" : rejected ? "bị loại do sai hash" : "chưa có"}${source ? ` · nhận từ ${shortPeer(source)}` : ""}` : "Chờ khởi tạo file";
    });
    if (total > maxVisible) {
      const summary = document.createElement("span");
      summary.className = "chunk-summary";
      summary.textContent = `${maxVisible} / ${total} ô · hiện các chunk đầu`;
      grid.append(summary);
    }
    grid.setAttribute("aria-label", `Peer ${shortPeer(id)}: ${valid} trên ${total} chunk hợp lệ${damaged ? `; ${damaged} chunk bị gây hỏng trong kho` : ""}${total > maxVisible ? `; hiển thị ${maxVisible} chunk đầu` : ""}`);
  }
  function renderChecks(state) {
    const checks = state.checks || [];
    setText("evidence-count", `${checks.filter((check) => check.passed).length} / ${checks.length} đạt`);
    if (checks.length) {
      const nodes = checks.map((check) => {
        const row = document.createElement("div");
        row.className = `check ${check.passed ? "passed" : "failed"}`;
        const icon = document.createElement("span");
        icon.className = "check-icon";
        icon.textContent = check.passed ? "✓" : "×";
        const label = document.createElement("span");
        label.textContent = check.label;
        row.append(icon, label);
        return row;
      });
      $("checks").replaceChildren(...nodes);
    } else if (state.status === "running") {
      const waiting = document.createElement("p");
      waiting.className = "evidence-intro";
      waiting.textContent = "Đang thu thập bằng chứng từ các peer…";
      $("checks").replaceChildren(waiting);
    }
    const artifact = state.artifact;
    $("artifact").hidden = !artifact?.verified;
    if (artifact?.verified) {
      setText("artifact-hash", artifact.sha256 || "—");
      setText("artifact-path", artifact.path || "—");
      ["download-b", "download-c"].forEach((id) => {
        $(id).download = state.metadata?.file_name || "";
        $(id).title = state.metadata?.file_name || "File đầu ra đã xác minh SHA-256";
      });
    }
    $("download-c").hidden = !(artifact?.verified && state.scenario === 2 && state.status === "passed");
    const results = state.results || [];
    $("results").hidden = !results.length;
    $("results").replaceChildren(...results.map((result) => {
      const row = document.createElement("div");
      row.className = `result${result.passed ? "" : " failed"}`;
      row.title = result.message || "";
      const label = document.createElement("span");
      label.textContent = `Demo ${result.scenario}`;
      const status = document.createElement("strong");
      status.textContent = result.passed ? "✓ Đạt" : "× Chưa đạt";
      row.append(label, status);
      return row;
    }));
    $("error-box").hidden = !state.error;
    setText("error-box", state.error || "");
  }
  function render(state) {
    currentState = state;
    if (state.run_id !== currentRun) {
      currentRun = state.run_id;
      previousScenario = state.scenario;
      resetEvents();
    } else if (state.scenario !== previousScenario) {
      previousScenario = state.scenario;
      recentFlows.clear();
      rejectedChunks.clear();
      damagedChunks.clear();
    }
    if (state.status === "running" && Number(state.scenario) >= 1 && Number(state.scenario) <= 4) selectScenario(Number(state.scenario));
    renderEvents(state.events || []);
    const status = ["running", "passed", "failed"].includes(state.status) ? state.status : "idle";
    const names = { idle: "Sẵn sàng thử nghiệm", running: `Đang chạy demo ${state.scenario || "…"}`, passed: "Kiểm chứng thành công", failed: "Demo chưa đạt" };
    $("session-status").className = `session-status ${status}`;
    setText("status-symbol", { idle: "◌", running: "⇄", passed: "✓", failed: "!" }[status]);
    setText("status-label", names[status]);
    setText("status-detail", status === "idle" ? "Chọn một kịch bản để bắt đầu" : status === "running" ? "Dữ liệu thực từ các peer" : status === "passed" ? "Đã đối chiếu các điều kiện" : "Xem bằng chứng và nhật ký");
    $("network-badge").className = `live-badge ${status}`;
    const badgeText = document.createTextNode({ idle: " CHỜ DEMO", running: " LIVE HTTP", passed: " ĐÃ HOÀN TẤT", failed: " CÓ LỖI" }[status]);
    const badgeDot = document.createElement("span");
    badgeDot.className = "dot";
    $("network-badge").replaceChildren(badgeDot, badgeText);
    setText("phase-label", phaseNames[state.phase] || String(state.phase || "Sẵn sàng").replaceAll("_", " "));
    setText("phase-message", state.message || "Mỗi demo chạy tracker và 3 peer độc lập. Tiến độ lấy từ chunk thực có trên từng peer.");
    const metadata = state.metadata;
    setText("file-name", metadata?.file_name || "Chưa có file thử nghiệm");
    setText("file-meta", metadata ? `${bytes(metadata.file_size)} · ${metadata.chunk_count} chunk × ${bytes(metadata.chunk_size)} · HTTP binary` : "File và kho dữ liệu riêng được tạo khi chạy demo");
    peerIds.forEach((id) => updatePeer(id, state.peers?.[id], metadata));
    renderChecks(state);
    renderSource();
    drawConnections();
    const announce = `${state.run_id}:${status}:${state.scenario}`;
    if (announce !== announcedStatus) { announcedStatus = announce; setText("announcement", names[status]); }
  }
  function svgElement(name, attributes) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
    return node;
  }
  function drawConnections() {
    const canvas = $("network-canvas").getBoundingClientRect();
    if (!canvas.width) return;
    const bounds = {};
    ["tracker", ...peerIds].forEach((id) => {
      const rect = $(id).getBoundingClientRect();
      bounds[id] = { x: rect.left - canvas.left, y: rect.top - canvas.top, w: rect.width, h: rect.height };
    });
    const tracker = bounds.tracker;
    const trackerLines = [];
    peerIds.forEach((id) => {
      const peer = bounds[id];
      const x1 = tracker.x + tracker.w / 2;
      const y1 = tracker.y + tracker.h;
      const x2 = peer.x + peer.w / 2;
      const y2 = peer.y;
      trackerLines.push(svgElement("path", { d: `M ${x1} ${y1} C ${x1} ${y1 + 25}, ${x2} ${y2 - 25}, ${x2} ${y2}`, class: "tracker-link" }));
    });
    $("tracker-lines").replaceChildren(...trackerLines);
    const flows = [];
    const now = Date.now();
    for (const [key, flow] of recentFlows) {
      if (flow.expires < now) { recentFlows.delete(key); continue; }
      const source = bounds[flow.source];
      const target = bounds[flow.receiver];
      const reverse = source.x > target.x;
      const adjacent = Math.abs(peerIds.indexOf(flow.source) - peerIds.indexOf(flow.receiver)) === 1;
      let path;
      let labelX;
      let labelY;
      if (adjacent) {
        const startX = reverse ? source.x - 2 : source.x + source.w + 2;
        const endX = reverse ? target.x + target.w + 2 : target.x - 2;
        const y = source.y + source.h * (reverse ? .68 : .4);
        path = `M ${startX} ${y} L ${endX} ${y}`;
        labelX = (startX + endX) / 2;
        labelY = y - 8;
      } else {
        const startX = source.x + source.w * .6;
        const endX = target.x + target.w * .4;
        const startY = source.y + source.h + 3;
        const y = startY + 17;
        path = `M ${startX} ${startY} C ${startX} ${y + 12}, ${endX} ${y + 12}, ${endX} ${startY}`;
        labelX = (startX + endX) / 2;
        labelY = y + 5;
      }
      flows.push(svgElement("path", { d: path, class: "flow-link", stroke: peerColors[flow.source], "marker-end": `url(#arrow-${flow.source.slice(-1)})` }));
      const label = svgElement("text", { x: labelX, y: labelY, "text-anchor": "middle", class: "flow-label" });
      label.textContent = Number.isInteger(flow.chunk) ? `#${flow.chunk}` : "chunk";
      flows.push(label);
    }
    $("flow-lines").replaceChildren(...flows);
  }
  async function poll() {
    if (inFlight) return;
    inFlight = true;
    const sourceVersionAtRequest = sourceRevision;
    try {
      const response = await fetch("/api/state", { cache: "no-store", signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const state = await response.json();
      if (!sourceOperation && sourceVersionAtRequest === sourceRevision) selectedSource = state.source || null;
      setConnection(true);
      render(state);
    } catch (error) {
      setConnection(false);
      setText("phase-message", "Không đọc được trạng thái từ máy chủ local. Đang tự kết nối lại…");
      recentFlows.clear();
      drawConnections();
    } finally { inFlight = false; }
  }
  async function run(scenario) {
    if (pendingRun || sourceOperation || currentState?.status === "running" || currentState?.upload_in_progress) return;
    pendingRun = true;
    updateControls();
    $("error-box").hidden = true;
    try {
      const response = await fetch("/api/run", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario }), signal: AbortSignal.timeout(10000)
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try { const data = await response.json(); detail = data.detail || data.error || detail; } catch (_) { /* Preserve HTTP status when response is not JSON. */ }
        throw new Error(response.status === 409 ? "Một demo đang chạy. Vui lòng đợi lượt chạy hiện tại hoàn tất." : String(detail));
      }
      document.querySelector(".workspace").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      await poll();
    } catch (error) {
      $("error-box").hidden = false;
      setText("error-box", `Không thể bắt đầu demo: ${error.message}`);
    } finally { pendingRun = false; updateControls(); }
  }
  document.querySelectorAll(".scenario").forEach((button) => button.addEventListener("click", () => selectScenario(Number(button.dataset.scenario))));
  $("run-selected").addEventListener("click", () => run(selected));
  $("run-all").addEventListener("click", () => run(0));
  $("choose-source").addEventListener("click", () => $("source-input").click());
  $("source-input").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file) uploadSource(file);
  });
  $("reset-source").addEventListener("click", resetSource);
  $("follow-events").addEventListener("change", () => { if ($("follow-events").checked) $("event-log").scrollTop = $("event-log").scrollHeight; });
  window.addEventListener("resize", drawConnections);
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(drawConnections).observe($("network-canvas"));
  renderSource();
  drawConnections();
  poll();
  setInterval(poll, 350);
  setInterval(() => { if (recentFlows.size) drawConnections(); }, 200);
})();
