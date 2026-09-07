from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path


CATEGORY_PATTERNS = {
    "dress": re.compile(r"\b(dress|gown)\b"),
    "skirt": re.compile(r"\bskirt\b"),
    "pants": re.compile(r"\b(pants|trousers|jeans|leggings)\b"),
    "shorts": re.compile(r"\bshorts\b"),
    "outerwear": re.compile(r"\b(jacket|coat|blazer|cardigan)\b"),
    "top": re.compile(r"\b(top|shirt|blouse|tee|sweater|hoodie|vest|tank)\b"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--source-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-color-ratio", type=float, default=0.07)
    parser.add_argument("--expected-records", type=int, required=True)
    parser.add_argument("--expected-failures", type=int, required=True)
    return parser.parse_args()


def caption_categories(caption: str) -> list[str]:
    first_sentence = caption.casefold().split(".", 1)[0]
    return [
        category
        for category, pattern in CATEGORY_PATTERNS.items()
        if pattern.search(first_sentence)
    ]


def main() -> None:
    args = parse_args()
    for path in (args.output, args.report):
        if path.exists():
            raise FileExistsError(path)

    source_rows = [
        json.loads(line)
        for line in args.source_audit.read_text(encoding="utf-8").splitlines()
    ]
    if len(source_rows) != args.expected_records:
        raise RuntimeError(
            f"expected {args.expected_records} audit rows, got {len(source_rows)}"
        )

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    category_by_product = dict(
        connection.execute(
            "SELECT parent_asin, category_norm FROM products WHERE parent_asin LIKE 'F200K_%'"
        )
    )
    connection.close()
    if len(category_by_product) != args.expected_records:
        raise RuntimeError(
            f"expected {args.expected_records} Fashion200K products, "
            f"got {len(category_by_product)}"
        )

    output_rows: list[dict[str, object]] = []
    reason_counts: Counter[str] = Counter()
    for row in source_rows:
        product_id = str(row["product_id"])
        expected_category = str(category_by_product[product_id])
        detected_categories = caption_categories(str(row["image_caption"]))
        reasons: list[str] = []
        if row["status"] != "PASS":
            reasons.append(f"color_audit_{str(row['status']).casefold()}")
        if (
            row["status"] == "PASS"
            and row["expected_color"] != "multicolor"
            and float(row["expected_ratio"]) < args.minimum_color_ratio
        ):
            reasons.append("insufficient_expected_color_pixels")
        if (
            len(detected_categories) == 1
            and detected_categories[0] != expected_category
        ):
            reasons.append("caption_category_conflict")

        status = "FAIL" if reasons else "PASS"
        reason_counts.update(reasons)
        output_rows.append(
            {
                **row,
                "color_audit_status": row["status"],
                "expected_category": expected_category,
                "caption_categories": detected_categories,
                "status": status,
                "reason": "; ".join(reasons) if reasons else "strict quality gate passed",
                "confidence": float(row["confidence"]),
            }
        )

    failures = sum(row["status"] == "FAIL" for row in output_rows)
    if failures != args.expected_failures:
        raise RuntimeError(
            f"expected {args.expected_failures} strict failures, got {failures}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as target:
        for row in output_rows:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = {
        "source_audit": str(args.source_audit),
        "output": str(args.output),
        "products": len(output_rows),
        "passed": len(output_rows) - failures,
        "failed": failures,
        "minimum_color_ratio": args.minimum_color_ratio,
        "failure_reason_counts": dict(sorted(reason_counts.items())),
        "policy": {
            "color": "only source PASS rows with sufficient expected-color pixels",
            "category": "reject a single image-caption category that contradicts the database category",
        },
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
