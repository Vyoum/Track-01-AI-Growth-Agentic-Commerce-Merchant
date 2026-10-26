#!/usr/bin/env python3
"""Load demo customers, history, proposals, and audit rows into SQLite.

Examples:

    python scripts/seed_demo.py
    python scripts/seed_demo.py --force
    python scripts/seed_demo.py --db /tmp/checkout_agent.db --csv /tmp/demo-csv --force
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the conversational checkout demo database")
    parser.add_argument(
        "--db",
        default=str(ROOT / "backend" / "data" / "checkout_agent.db"),
        help="SQLite file to create or refresh",
    )
    parser.add_argument(
        "--csv",
        default="",
        help="Optional directory for one CSV per table",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing seeded rows",
    )
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ.setdefault("USE_MOCK_CATALOG", "true")
    os.environ.setdefault("STORE_PROVIDER", "mock")
    os.environ.setdefault("RAZORPAY_KEY_ID", "")
    os.environ.setdefault("RAZORPAY_KEY_SECRET", "")

    from backend.config import get_settings
    from backend.services.data_loader import reload_demo_data
    from backend.services.demo_seed import export_sqlite_csv, seed_demo_database

    get_settings.cache_clear()
    reload_demo_data()
    summary = seed_demo_database(db_path, force=args.force)
    printable = {key: value for key, value in summary.items() if key != "journeys"}
    if args.csv:
        printable["csv"] = export_sqlite_csv(db_path, Path(args.csv).resolve())
    print(json.dumps(printable, indent=2))


if __name__ == "__main__":
    main()
