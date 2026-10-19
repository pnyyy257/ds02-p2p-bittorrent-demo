"""Run the browser dashboard with Chọn file, or check four real-network scenarios.

Example: python scripts/run_visual.py --check --file "C:/files/my-document.pdf"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from p2psim.visual import VisualDemo, create_app


async def check(source: Path | None = None):
    demo = VisualDemo()
    try:
        if source:
            await demo.select_path(source)
        demo.start(0)
        await demo.task
        print(json.dumps({key: demo.state[key] for key in ("status", "results", "error", "artifact")},
                         ensure_ascii=True, indent=2))
        return 0 if demo.state["status"] == "passed" else 1
    finally:
        await demo.close()


def main():
    parser = argparse.ArgumentParser(description="DS02 P2P live visual demonstration")
    parser.add_argument("--port", type=int, default=8080, help="Dashboard loopback port (default: 8080)")
    parser.add_argument("--check", action="store_true", help="Run all 4 dashboard scenarios and exit")
    parser.add_argument("--file", type=Path, help="Use a real file for --check (8 bytes to 32 MiB)")
    args = parser.parse_args()
    if args.file and not args.check:
        parser.error("--file requires --check; use Chọn file in the browser for interactive demos")
    if args.check:
        return asyncio.run(check(args.file))
    if not 1 <= args.port <= 65535:
        parser.error("--port must be in [1, 65535]")
    import uvicorn
    print(f"\nDS02 P2P Demo: http://127.0.0.1:{args.port}\n"
          "Open this address in your browser. Press Ctrl+C to stop all owned processes.\n"
          "Each demo creates fresh peer directories under runtime/visual-*.\n", flush=True)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
