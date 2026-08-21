#!/usr/bin/env python3
"""Reset demo SQLite DB between runs (filled further in later pointers)."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import init_db
from backend.config import get_settings


def main() -> None:
    settings = get_settings()
    path = settings.sqlite_path
    if path.exists():
        path.unlink()
        print(f"Removed {path}")
    init_db(path)
    print(f"Reinitialized {path}")


if __name__ == "__main__":
    main()
