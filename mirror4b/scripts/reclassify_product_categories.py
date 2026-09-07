from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_filtered_product_database import normalize_category


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        """
        SELECT id, parent_asin, title, category_path, category_norm
        FROM products
        WHERE category_source = 'amazon_category'
        ORDER BY id
        """
    ).fetchall()

    transitions: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    updates: list[tuple[str, int]] = []
    unresolved_preserved = 0
    for row in rows:
        categories = str(row["category_path"]).split(" > ")
        new_category = normalize_category(categories, str(row["title"]))
        old_category = str(row["category_norm"])
        if new_category is None:
            unresolved_preserved += 1
            continue
        if new_category == old_category:
            continue
        transitions[(old_category, new_category)] += 1
        updates.append((new_category, int(row["id"])))
        key = (old_category, new_category)
        if len(examples[key]) < 5:
            examples[key].append(
                {
                    "parent_asin": str(row["parent_asin"]),
                    "title": str(row["title"]),
                    "category_path": str(row["category_path"]),
                }
            )

    if args.apply:
        with connection:
            connection.executemany(
                "UPDATE products SET category_norm = ? WHERE id = ?",
                updates,
            )

    category_counts = dict(
        connection.execute(
            "SELECT category_norm, COUNT(*) FROM products GROUP BY category_norm ORDER BY category_norm"
        ).fetchall()
    )
    integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
    fashion200k_records = connection.execute(
        "SELECT COUNT(*) FROM products WHERE category_source = 'fashion200k_category'"
    ).fetchone()[0]
    connection.close()

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": str(args.database),
        "database_sha256": sha256(args.database),
        "applied": args.apply,
        "amazon_records_scanned": len(rows),
        "changed_records": len(updates),
        "unresolved_records_preserved": unresolved_preserved,
        "fashion200k_records_unchanged": fashion200k_records,
        "integrity_check": integrity_check,
        "category_counts_after": category_counts,
        "transitions": [
            {
                "from": old_category,
                "to": new_category,
                "count": count,
                "examples": examples[(old_category, new_category)],
            }
            for (old_category, new_category), count in transitions.most_common()
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
