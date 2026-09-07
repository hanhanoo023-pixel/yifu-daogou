from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.evaluator import run_draft_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--suite",
        choices=("all", "intent", "recommendation", "safety"),
        default="all",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/reports/evaluation_draft_report.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_draft_evaluation(args.suite)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
