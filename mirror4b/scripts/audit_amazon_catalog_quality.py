from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "experiments" / "catalog_hybrid.db"
DEFAULT_OUTPUT = (
    PROJECT_ROOT / "data" / "reports" / "amazon_catalog_quality_dry_run_v1.json"
)

FASHION_SIGNAL = re.compile(
    r"\b(?:dress|gown|skirt|pants|trousers|jeans|leggings|shorts|jacket|coat|"
    r"blazer|shirt|t-?shirt|tee|top|blouse|camisole|tank|sweater|hoodie|bra|"
    r"panties|underwear|swimsuit|bikini|shoes?|sneakers?|sandals?|boots?|pumps?|"
    r"loafers?|slippers?|handbags?|backpacks?|wallets?|purses?|totes?|clutches?|"
    r"wristlets?|necklaces?|earrings?|bracelets?|rings?|watches?|hats?|scarves?|"
    r"belts?|gloves?|socks?)\b",
    re.IGNORECASE,
)

NON_FASHION_RULES = (
    (
        "electronics_display_accessory",
        re.compile(
            r"\b(?:screen protector|tempered glass|hdmi (?:cable|adapter)|"
            r"digital av adapter|usb(?:-[a-z])? (?:cable|adapter|charger)|"
            r"phone charger|charging cable|power bank)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "lighting_or_hardware",
        re.compile(
            r"\b(?:led flashlight|flashlight set|drill bits?|socket wrench|"
            r"screwdriver set)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "home_textile_or_furnishing",
        re.compile(
            r"\b(?:shower curtain|window curtain|duvet cover|bed sheets?|"
            r"pillow cases?|tablecloth|area rug)\b",
            re.IGNORECASE,
        ),
    ),
)

UMBRELLA = re.compile(r"\bumbrella\b", re.IGNORECASE)
BAG_OR_WALLET_SIGNAL = re.compile(
    r"\b(?:bags?|handbags?|backpacks?|wallets?|purses?|totes?|clutches?|"
    r"wristlets?|card holders?|straps?)\b",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a read-only, high-precision Amazon catalog audit report."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def audit_amazon_product(row: sqlite3.Row) -> dict[str, object] | None:
    title = str(row["title"])
    for rule_id, pattern in NON_FASHION_RULES:
        match = pattern.search(title)
        if match is not None and FASHION_SIGNAL.search(title) is None:
            return {
                "product_id": str(row["parent_asin"]),
                "current_category": str(row["category_norm"]),
                "proposed_action": "EXCLUDE_NON_FASHION",
                "proposed_category": None,
                "rule_id": rule_id,
                "evidence": match.group(0),
                "title": title,
                "category_path": str(row["category_path"]),
            }

    if (
        row["category_norm"] == "bag"
        and UMBRELLA.search(title) is not None
        and BAG_OR_WALLET_SIGNAL.search(title) is None
    ):
        return {
            "product_id": str(row["parent_asin"]),
            "current_category": "bag",
            "proposed_action": "RECLASSIFY",
            "proposed_category": "accessory",
            "rule_id": "standalone_umbrella",
            "evidence": "umbrella",
            "title": title,
            "category_path": str(row["category_path"]),
        }
    return None


def duplicate_title_statistics(connection: sqlite3.Connection) -> dict[str, int]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS groups_count,
               COALESCE(SUM(group_size - 1), 0) AS duplicate_rows,
               COALESCE(MAX(group_size), 0) AS maximum_group_size
        FROM (
            SELECT COUNT(*) AS group_size
            FROM products
            WHERE parent_asin LIKE 'F200K_%'
            GROUP BY LOWER(TRIM(title))
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()
    return {
        "groups": int(row["groups_count"]),
        "duplicate_rows": int(row["duplicate_rows"]),
        "maximum_group_size": int(row["maximum_group_size"]),
    }


def build_report(database: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT parent_asin, title, category_path, category_norm
        FROM products
        WHERE parent_asin NOT LIKE 'F200K_%'
        ORDER BY parent_asin
        """
    ).fetchall()
    candidates = [
        candidate
        for row in rows
        if (candidate := audit_amazon_product(row)) is not None
    ]
    action_counts = Counter(str(item["proposed_action"]) for item in candidates)
    rule_counts = Counter(str(item["rule_id"]) for item in candidates)
    fashion_products = int(
        connection.execute(
            "SELECT COUNT(*) FROM products WHERE parent_asin LIKE 'F200K_%'"
        ).fetchone()[0]
    )
    distinct_fashion_images = int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT image_url) FROM products
            WHERE parent_asin LIKE 'F200K_%'
            """
        ).fetchone()[0]
    )
    duplicate_titles = duplicate_title_statistics(connection)
    connection.close()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "dry_run_read_only",
        "database": str(database),
        "amazon_products_scanned": len(rows),
        "candidate_count": len(candidates),
        "action_counts": dict(sorted(action_counts.items())),
        "rule_counts": dict(sorted(rule_counts.items())),
        "candidates": candidates,
        "fashion200k": {
            "products": fashion_products,
            "distinct_images": distinct_fashion_images,
            "duplicate_titles": duplicate_titles,
            "recommended_policy": (
                "Keep all unique images in the catalog and deduplicate normalized titles "
                "only in each displayed recommendation page."
            ),
        },
    }


def main() -> None:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    report = build_report(args.database)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
