"""Write `checksums.sha256` (`<sha256>  <name>` per line) for the given files.

    python scripts/make_checksums.py OUT.sha256 FILE [FILE ...]

The model updater (`src/updater.py`) reads this exact format.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path


def make(out: Path, files: list[Path]) -> None:
    lines = [f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}\n" for f in files]
    out.write_text("".join(lines), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    make(Path(sys.argv[1]), [Path(a) for a in sys.argv[2:]])
