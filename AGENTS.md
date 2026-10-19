# Codex / AI coding guidelines for DS02

Read `README.md`, `docs/ARCHITECTURE.md` and `docs/input_specification.pdf` before changing architecture.

**Invariants**
- Tracker may store only metadata and chunk inventories. Never proxy or store file bytes on tracker.
- Peers must upload **while** downloading, transferring actual HTTP binary chunks peer-to-peer.
- Keep a minimum of 3 independent peers and preserve per-peer independent data directories.
- Preserve SHA-256 chunk and whole-file verification, bounded retries, source switching, checkpoint resume and eventual-consistent announcements.
- Do not introduce shared storage to replace network communication.
- Keep basic access control and input limits. Do not commit `.env` or real secrets.
- Before edits, explain short plan; make small changes; run `python -m pytest -q` and `python scripts/run_local.py`.
- When introducing or removing a feature, update README, report, test plan and demonstration scripts.
- Do not fabricate individual Git commits/contributions, performance metrics, or test results.

**Priority:** meet the mandatory specification before advanced features or fancy UI.
