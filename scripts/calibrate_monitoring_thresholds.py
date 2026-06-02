from __future__ import annotations

import argparse
import json
from pathlib import Path

from airadar.admin.calibration import calibrate_thresholds


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate AI Radar monitoring alert thresholds.")
    parser.add_argument("--db", dest="db_path", help="SQLite database path")
    parser.add_argument("--pipeline-log-dir", default="logs", help="Directory containing pipeline-*.log")
    parser.add_argument("--access-log", action="append", default=[], help="Access log path; may be repeated")
    parser.add_argument("--days", type=int, default=7, help="Lookback days for DB and pipeline baselines")
    args = parser.parse_args()

    calibration = calibrate_thresholds(
        db_path=Path(args.db_path) if args.db_path else None,
        pipeline_log_dir=Path(args.pipeline_log_dir),
        access_log_paths=[Path(path) for path in args.access_log] if args.access_log else None,
        days=args.days,
    )
    print(json.dumps(calibration, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
