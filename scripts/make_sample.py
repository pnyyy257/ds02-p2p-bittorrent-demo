"""Generate local demo files; only peer-a can access them via bind-mount."""
from pathlib import Path
import random

folder = Path(__file__).resolve().parents[1] / "sample_files"
folder.mkdir(exist_ok=True)
for i, name in enumerate(["sample.bin", "sample_swarm.bin", "sample_corrupt.bin", "sample_failure.bin"]):
    dest = folder / name
    if not dest.exists():
        rng = random.Random(2026 + i)
        with dest.open("wb") as f:
            for _ in range(64):
                f.write(rng.randbytes(65536))
    print(f"{dest.name}: {dest.stat().st_size:,} bytes")
