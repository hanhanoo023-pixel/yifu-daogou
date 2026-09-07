from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


APPAREL_CATEGORIES = {
    "top",
    "dress",
    "pants",
    "shorts",
    "skirt",
    "outerwear",
    "sweater",
    "swimwear",
    "underwear",
    "set",
    "costume",
}

ONE_SIZE_CATEGORIES = {"bag", "jewelry", "watch", "accessory", "other"}

APPAREL_SIZES = ("XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL")
SHOE_SIZES = ("5", "6", "7", "8", "9", "10", "11", "12")
COLORS = (
    "black",
    "white",
    "blue",
    "red",
    "green",
    "yellow",
    "pink",
    "purple",
    "brown",
    "gray",
    "beige",
    "orange",
)
SEASONS = ("spring", "summer", "autumn", "winter")

PRICE_RANGES = {
    "top": (12.99, 59.99),
    "dress": (19.99, 89.99),
    "pants": (18.99, 79.99),
    "shorts": (12.99, 49.99),
    "skirt": (15.99, 59.99),
    "outerwear": (29.99, 159.99),
    "sweater": (19.99, 89.99),
    "swimwear": (14.99, 69.99),
    "underwear": (8.99, 49.99),
    "shoes": (24.99, 149.99),
    "bag": (19.99, 169.99),
    "jewelry": (9.99, 129.99),
    "watch": (19.99, 199.99),
    "accessory": (7.99, 79.99),
    "set": (24.99, 109.99),
    "costume": (19.99, 99.99),
    "other": (9.99, 99.99),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def stable_number(parent_asin: str, field: str) -> int:
    digest = hashlib.sha256(f"{parent_asin}:{field}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def choose(values: tuple[str, ...], parent_asin: str, field: str) -> str:
    return values[stable_number(parent_asin, field) % len(values)]


def synthetic_price(parent_asin: str, category: str) -> float:
    minimum, maximum = PRICE_RANGES[category]
    cents_minimum = round(minimum * 100)
    cents_maximum = round(maximum * 100)
    cents = cents_minimum + stable_number(parent_asin, "price") % (
        cents_maximum - cents_minimum + 1
    )
    return cents / 100


def normalized_price(value: object, parent_asin: str, category: str) -> tuple[float, str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return round(float(value), 2), "original"
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match and float(match.group()) > 0:
            return round(float(match.group()), 2), "original_normalized"
    return synthetic_price(parent_asin, category), "synthetic"


def completed_size(value: str | None, parent_asin: str, category: str) -> tuple[str, str]:
    if value:
        return value, "original_or_extracted"
    if category in APPAREL_CATEGORIES:
        return choose(APPAREL_SIZES, parent_asin, "size"), "synthetic"
    if category == "shoes":
        return choose(SHOE_SIZES, parent_asin, "shoe_size"), "synthetic"
    if category in ONE_SIZE_CATEGORIES:
        return "ONE_SIZE", "synthetic"
    raise ValueError(category)


def add_source_columns(connection: sqlite3.Connection) -> None:
    existing = {row[1] for row in connection.execute("PRAGMA table_info(products)")}
    for column in (
        "price_source",
        "size_source",
        "color_source",
        "season_source",
        "category_source",
        "brand_source",
    ):
        if column not in existing:
            connection.execute(f"ALTER TABLE products ADD COLUMN {column} TEXT")


def main() -> None:
    args = parse_args()
    if args.database.exists():
        raise FileExistsError(args.database)
    if args.report.exists():
        raise FileExistsError(args.report)

    started_at = datetime.now(timezone.utc)
    started_timer = time.perf_counter()
    source = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    target = sqlite3.connect(args.database)
    source.backup(target)
    source.close()

    add_source_columns(target)
    target.execute("DROP INDEX IF EXISTS idx_products_filters")
    target.execute("DROP INDEX IF EXISTS idx_products_color_size_stock")
    target.execute("DROP INDEX IF EXISTS idx_products_price")

    statistics = Counter()
    batch: list[tuple[object, ...]] = []
    select_cursor = target.execute(
        """
        SELECT parent_asin, category_norm, price, size_norm, color_norm, season, brand
        FROM products
        """
    )
    for parent_asin, category, price, size, color, season, brand in select_cursor:
        completed_category = category or "other"
        completed_brand = brand.strip() if isinstance(brand, str) and brand.strip() else "Unbranded"
        completed_price, price_source = normalized_price(
            price, parent_asin, completed_category
        )
        completed_size_value, size_source = completed_size(
            size, parent_asin, completed_category
        )
        completed_color = color or choose(COLORS, parent_asin, "color")
        completed_season = season or choose(SEASONS, parent_asin, "season")
        color_source = "original_or_extracted" if color else "synthetic"
        season_source = "original_or_extracted" if season else "synthetic"
        category_source = "original_or_extracted" if category else "synthetic"
        brand_source = "original" if brand and str(brand).strip() else "synthetic"
        batch.append(
            (
                completed_category,
                completed_price,
                completed_size_value,
                completed_color,
                completed_season,
                completed_brand,
                price_source,
                size_source,
                color_source,
                season_source,
                category_source,
                brand_source,
                parent_asin,
            )
        )
        statistics["records"] += 1
        statistics[f"price_{price_source}"] += 1
        statistics[f"size_{size_source}"] += 1
        statistics[f"color_{color_source}"] += 1
        statistics[f"season_{season_source}"] += 1
        statistics[f"category_{category_source}"] += 1
        statistics[f"brand_{brand_source}"] += 1
        if len(batch) == 5_000:
            target.executemany(
                """
                UPDATE products SET
                    category_norm=?, price=?, size_norm=?, color_norm=?, season=?, brand=?,
                    price_source=?, size_source=?, color_source=?, season_source=?,
                    category_source=?, brand_source=?
                WHERE parent_asin=?
                """,
                batch,
            )
            batch.clear()
    if batch:
        target.executemany(
            """
            UPDATE products SET
                category_norm=?, price=?, size_norm=?, color_norm=?, season=?, brand=?,
                price_source=?, size_source=?, color_source=?, season_source=?,
                category_source=?, brand_source=?
            WHERE parent_asin=?
            """,
            batch,
        )
    target.commit()
    target.executescript(
        """
        CREATE INDEX idx_products_filters
            ON products (category_norm, color_norm, size_norm, season, stock_quantity);
        CREATE INDEX idx_products_color_size_stock
            ON products (color_norm, size_norm, stock_quantity);
        CREATE INDEX idx_products_price ON products (price);
        ANALYZE;
        """
    )
    target.commit()
    integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
    missing = target.execute(
        """
        SELECT
            SUM(stock_quantity IS NULL), SUM(price IS NULL OR typeof(price) NOT IN ('real','integer')),
            SUM(size_norm IS NULL OR size_norm=''), SUM(color_norm IS NULL OR color_norm=''),
            SUM(season IS NULL OR season=''), SUM(category_norm IS NULL OR category_norm=''),
            SUM(brand IS NULL OR brand='')
        FROM products
        """
    ).fetchone()
    target.close()

    report = {
        "source_database": str(args.source),
        "completed_database": str(args.database),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started_timer, 3),
        "database_size_bytes": args.database.stat().st_size,
        "integrity_check": integrity,
        "missing_after_completion": {
            "stock_quantity": missing[0],
            "price": missing[1],
            "size": missing[2],
            "color": missing[3],
            "season": missing[4],
            "category": missing[5],
            "brand": missing[6],
        },
        "statistics": dict(sorted(statistics.items())),
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
