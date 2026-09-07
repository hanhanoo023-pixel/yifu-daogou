from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BATCH_SIZE = 5_000
EXPECTED_RECORDS = 252_413

CATEGORY_RULES = (
    ("underwear", ("underwear", "lingerie", "bras", "panties", "boxers", "briefs")),
    ("swimwear", ("swimsuit", "swimwear", "bikini", "swim trunks", "cover-ups")),
    ("shoes", ("shoes", "sneakers", "sandals", "boots", "pumps", "flats", "slippers", "loafers")),
    ("bag", ("bags", "handbags", "backpacks", "wallets", "purses", "pouches", "wristlets", "totes", "clutches")),
    ("jewelry", ("jewelry", "necklaces", "earrings", "bracelets", "anklets", "rings", "brooches")),
    ("watch", ("watches", "wrist watches")),
    ("pants", ("pants", "jeans", "trousers", "leggings", "jeggings", "tights", "sweatpants")),
    ("shorts", ("shorts",)),
    ("skirt", ("skirts", "skirt")),
    ("outerwear", ("coats", "jackets", "vests", "outerwear", "rainwear")),
    ("sweater", ("sweaters", "pullovers", "cardigans", "hoodies", "sweatshirts")),
    (
        "top",
        (
            "t-shirts",
            "t shirts",
            "tees",
            "tops",
            "shirts",
            "blouses",
            "polos",
            "tunics",
            "tanks",
            "camis",
        ),
    ),
    ("set", ("sets", "outfit sets")),
    ("accessory", ("accessories", "sunglasses", "hats", "scarves", "belts", "gloves", "mittens", "masks", "wigs", "headbands", "socks", "keychains", "ties", "umbrellas")),
    ("costume", ("costumes", "cosplay")),
    ("dress", ("dresses", "dress")),
)

CATEGORY_PATH_OVERRIDES = (
    ("pants", ("pants",)),
    ("underwear", ("underwear", "lingerie")),
    ("swimwear", ("swim", "swimsuits", "swimwear", "competitive swimwear")),
    ("shoes", ("shoes", "boot shop")),
    ("jewelry", ("jewelry", "jewelry accessories")),
    ("watch", ("watches", "watch accessories")),
)

CATEGORY_AGGREGATE_NODES = {
    "clothing, shoes & jewelry",
    "shoe, jewelry & watch accessories",
    "costumes & accessories",
}

COLOR_RULES = (
    ("yellow", ("yellow", "mustard", "lemon")),
    ("black", ("black",)),
    ("white", ("white", "ivory")),
    ("blue", ("blue", "navy", "cobalt", "teal", "turquoise")),
    ("red", ("red", "burgundy", "maroon", "wine")),
    ("green", ("green", "olive", "lime", "emerald")),
    ("pink", ("pink", "rose")),
    ("purple", ("purple", "violet", "lavender")),
    ("brown", ("brown", "chocolate", "camel")),
    ("gray", ("gray", "grey", "charcoal")),
    ("beige", ("beige", "khaki", "cream", "tan")),
    ("orange", ("orange", "coral")),
    ("silver", ("silver",)),
    ("gold", ("gold",)),
    ("multicolor", ("multicolor", "multi-color", "rainbow")),
)

SIZE_RULES = (
    ("4XL", (r"\b4x[- ]?large\b", r"\b4xl\b")),
    ("3XL", (r"\b3x[- ]?large\b", r"\b3xl\b", r"\bxxx[- ]?large\b")),
    ("2XL", (r"\b2x[- ]?large\b", r"\b2xl\b", r"\bxx[- ]?large\b", r"\bxxl\b")),
    ("XL", (r"\bx[- ]?large\b", r"\bxl\b")),
    ("L", (r"\blarge\b", r"\bsize[ :\-]*l\b")),
    ("M", (r"\bmedium\b", r"\bsize[ :\-]*m\b")),
    ("S", (r"\bsmall\b", r"\bsize[ :\-]*s\b")),
    ("XS", (r"\bx[- ]?small\b", r"\bxs\b")),
)

SEASON_RULES = (
    ("summer", ("summer",)),
    ("winter", ("winter",)),
    ("spring", ("spring",)),
    ("autumn", ("autumn", "fall")),
)

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
ONE_SIZE_CATEGORIES = {"bag", "jewelry", "watch", "accessory"}
APPAREL_SIZES = ("XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL")
SHOE_SIZES = ("5", "6", "7", "8", "9", "10", "11", "12")
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
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-records", type=int, default=EXPECTED_RECORDS)
    return parser.parse_args()


def stable_number(parent_asin: str, field: str) -> int:
    digest = hashlib.sha256(f"{parent_asin}:{field}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def choose(values: tuple[str, ...], parent_asin: str, field: str) -> str:
    return values[stable_number(parent_asin, field) % len(values)]


def match_keywords(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...]) -> str | None:
    lowered = text.lower()
    for normalized, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return normalized
    return None


def match_category_text(text: str) -> str | None:
    for normalized, keywords in CATEGORY_RULES:
        if any(
            re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", text, re.IGNORECASE)
            for keyword in keywords
        ):
            return normalized
    return None


def normalize_category(categories: list[str], title: str) -> str | None:
    searchable_categories = [
        category.strip()
        for category in (categories[1:] if categories else categories)
        if category.strip().lower() not in CATEGORY_AGGREGATE_NODES
    ]
    for normalized, markers in CATEGORY_PATH_OVERRIDES:
        if any(
            category.lower() == marker
            if normalized == "pants"
            else re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", category, re.IGNORECASE)
            for category in searchable_categories
            for marker in markers
        ):
            return normalized
    for category in reversed(searchable_categories):
        matched = match_category_text(category)
        if matched:
            return matched
    return match_category_text(title)


def normalize_explicit_color(raw_color: str) -> str | None:
    normalized_text = re.sub(
        r"(?<![a-z])rose[ -]+gold(?![a-z])",
        "gold",
        raw_color,
        flags=re.IGNORECASE,
    )
    normalized_text = re.sub(
        r"(?<![a-z])rose[ -]+red(?![a-z])",
        "red",
        normalized_text,
        flags=re.IGNORECASE,
    )
    matched_colors = {
        normalized
        for normalized, keywords in COLOR_RULES
        if any(
            re.search(
                rf"(?<![a-z]){re.escape(keyword)}(?![a-z])",
                normalized_text,
                flags=re.IGNORECASE,
            )
            for keyword in keywords
        )
    }
    if len(matched_colors) > 1:
        return "multicolor"
    return next(iter(matched_colors)) if matched_colors else None


def normalize_explicit_size(raw_size: str) -> str | None:
    for normalized, patterns in SIZE_RULES:
        if any(re.search(pattern, raw_size, flags=re.IGNORECASE) for pattern in patterns):
            return normalized
    return None


def normalize_explicit_season(raw_season: str) -> str | None:
    return match_keywords(raw_season, SEASON_RULES)


def choose_product_image(images: list[object]) -> str | None:
    for value in images:
        image = value if isinstance(value, dict) else {}
        url = image.get("hi_res") or image.get("large") or image.get("thumb")
        if image.get("variant") == "MAIN" and isinstance(url, str) and url.strip():
            return url.strip()
    for value in images:
        image = value if isinstance(value, dict) else {}
        url = image.get("hi_res") or image.get("large") or image.get("thumb")
        if isinstance(url, str) and url.strip():
            return url.strip()
    return None


def flatten_text(values: list[object]) -> str | None:
    text = " ".join(str(value).strip() for value in values if str(value).strip())
    return text or None


def normalized_price(value: object, parent_asin: str, category: str) -> tuple[float, str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return round(float(value), 2), "original"
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match and float(match.group()) > 0:
            return round(float(match.group()), 2), "original_normalized"
    minimum, maximum = PRICE_RANGES[category]
    minimum_cents = round(minimum * 100)
    maximum_cents = round(maximum * 100)
    cents = minimum_cents + stable_number(parent_asin, "price") % (
        maximum_cents - minimum_cents + 1
    )
    return cents / 100, "synthetic_demo"


def completed_size(raw_size: str | None, parent_asin: str, category: str) -> tuple[str, str]:
    if raw_size:
        normalized = normalize_explicit_size(raw_size)
        if normalized:
            return normalized, "explicit_amazon"
    if category in APPAREL_CATEGORIES:
        return choose(APPAREL_SIZES, parent_asin, "size"), "synthetic_demo"
    if category == "shoes":
        return choose(SHOE_SIZES, parent_asin, "shoe_size"), "synthetic_demo"
    if category in ONE_SIZE_CATEGORIES:
        return "ONE_SIZE", "synthetic_demo"
    raise ValueError(category)


def completed_season(raw_season: str | None, parent_asin: str) -> tuple[str, str]:
    if raw_season:
        normalized = normalize_explicit_season(raw_season)
        if normalized:
            return normalized, "explicit_amazon"
    return choose(SEASONS, parent_asin, "season"), "synthetic_demo"


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            parent_asin TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            brand TEXT,
            category_path TEXT NOT NULL,
            category_norm TEXT NOT NULL,
            department TEXT,
            color_raw TEXT NOT NULL,
            color_norm TEXT NOT NULL,
            size_raw TEXT,
            size_norm TEXT NOT NULL,
            material TEXT,
            season TEXT NOT NULL,
            price REAL NOT NULL,
            image_url TEXT NOT NULL,
            rating REAL NOT NULL,
            rating_count INTEGER NOT NULL,
            stock_quantity INTEGER NOT NULL CHECK (stock_quantity BETWEEN 0 AND 100),
            features_text TEXT,
            description_text TEXT,
            price_source TEXT NOT NULL,
            size_source TEXT NOT NULL,
            color_source TEXT NOT NULL,
            season_source TEXT NOT NULL,
            category_source TEXT NOT NULL,
            brand_source TEXT NOT NULL,
            stock_source TEXT NOT NULL
        );
        """
    )


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE INDEX idx_products_filters
            ON products (category_norm, color_norm, size_norm, season, stock_quantity);
        CREATE INDEX idx_products_color_size_stock
            ON products (color_norm, size_norm, stock_quantity);
        CREATE INDEX idx_products_rating
            ON products (rating DESC, rating_count DESC);
        CREATE INDEX idx_products_price ON products (price);
        ANALYZE;
        """
    )


def build_row(item: dict[str, object]) -> tuple[object, ...] | None:
    parent_asin = str(item.get("parent_asin") or "").strip()
    title = str(item.get("title") or "").strip()
    if not parent_asin or not title:
        return None

    categories = [str(value).strip() for value in (item.get("categories") or [])]
    category = normalize_category(categories, title)
    if not category:
        return None

    image_url = choose_product_image(item.get("images") or [])
    if not image_url:
        return None

    details = item.get("details") or {}
    raw_color_value = details.get("Color")
    raw_color = str(raw_color_value).strip() if raw_color_value else None
    if not raw_color:
        return None
    color = normalize_explicit_color(raw_color)
    if not color:
        return None

    raw_size_value = details.get("Size")
    raw_size = str(raw_size_value).strip() if raw_size_value else None
    size, size_source = completed_size(raw_size, parent_asin, category)

    raw_season_value = details.get("Seasons")
    raw_season = str(raw_season_value).strip() if raw_season_value else None
    season, season_source = completed_season(raw_season, parent_asin)

    price, price_source = normalized_price(item.get("price"), parent_asin, category)
    stock_quantity = stable_number(parent_asin, "stock") % 101

    brand_value = item.get("store")
    brand = str(brand_value).strip() if brand_value else None
    brand_source = "original" if brand else "missing"
    material_value = details.get("Material") or details.get("Fabric Type")
    material = str(material_value).strip() if material_value else None
    features_text = flatten_text(item.get("features") or [])
    description_text = flatten_text(item.get("description") or [])
    rating_value = item.get("average_rating")
    rating_count_value = item.get("rating_number")

    return (
        parent_asin,
        title,
        brand,
        " > ".join(categories),
        category,
        details.get("Department") or None,
        raw_color,
        color,
        raw_size,
        size,
        material,
        season,
        price,
        image_url,
        float(rating_value) if rating_value is not None else 0.0,
        int(rating_count_value) if rating_count_value is not None else 0,
        stock_quantity,
        features_text,
        description_text,
        price_source,
        size_source,
        "explicit_amazon",
        season_source,
        "amazon_category",
        brand_source,
        "synthetic_demo",
    )


def update_statistics(statistics: Counter[str], row: tuple[object, ...]) -> None:
    statistics["selected_records"] += 1
    statistics[f"category:{row[4]}"] += 1
    statistics[f"color:{row[7]}"] += 1
    statistics[f"price_source:{row[19]}"] += 1
    statistics[f"size_source:{row[20]}"] += 1
    statistics[f"season_source:{row[22]}"] += 1
    statistics[f"brand_source:{row[24]}"] += 1
    statistics["in_stock" if row[16] > 0 else "out_of_stock"] += 1


def grouped_statistics(statistics: Counter[str], prefix: str) -> dict[str, int]:
    marker = prefix + ":"
    values = {
        key.removeprefix(marker): count
        for key, count in statistics.items()
        if key.startswith(marker)
    }
    return dict(sorted(values.items(), key=lambda item: (-item[1], item[0])))


def main() -> None:
    args = parse_args()
    if args.database.exists():
        raise FileExistsError(args.database)
    if args.report.exists():
        raise FileExistsError(args.report)

    started_at = datetime.now(timezone.utc)
    started_timer = time.perf_counter()
    statistics: Counter[str] = Counter()

    connection = sqlite3.connect(args.database)
    connection.execute("PRAGMA journal_mode = MEMORY")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA cache_size = -200000")
    create_schema(connection)
    insert_sql = """
        INSERT OR IGNORE INTO products (
            parent_asin, title, brand, category_path, category_norm, department,
            color_raw, color_norm, size_raw, size_norm, material, season, price,
            image_url, rating, rating_count, stock_quantity, features_text,
            description_text, price_source, size_source, color_source,
            season_source, category_source, brand_source, stock_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    batch: list[tuple[object, ...]] = []
    connection.execute("BEGIN")
    with gzip.open(args.source, "rt", encoding="utf-8") as source:
        for line in source:
            statistics["scanned_records"] += 1
            item = json.loads(line)
            row = build_row(item)
            if row is None:
                continue
            batch.append(row)
            update_statistics(statistics, row)
            if len(batch) == BATCH_SIZE:
                before = connection.total_changes
                connection.executemany(insert_sql, batch)
                statistics["duplicate_parent_asin"] += len(batch) - (
                    connection.total_changes - before
                )
                batch.clear()
        if batch:
            before = connection.total_changes
            connection.executemany(insert_sql, batch)
            statistics["duplicate_parent_asin"] += len(batch) - (
                connection.total_changes - before
            )
    connection.commit()
    create_indexes(connection)
    connection.commit()

    database_records = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    integrity_check = connection.execute("PRAGMA integrity_check").fetchone()[0]
    missing_required = connection.execute(
        """
        SELECT
            SUM(parent_asin=''), SUM(title=''), SUM(category_norm=''),
            SUM(color_raw=''), SUM(color_norm=''), SUM(image_url=''),
            SUM(size_norm=''), SUM(season=''), SUM(price IS NULL),
            SUM(stock_quantity IS NULL),
            SUM(color_source!='explicit_amazon'),
            SUM(stock_source!='synthetic_demo')
        FROM products
        """
    ).fetchone()
    source_counts = {
        "price": dict(
            connection.execute(
                "SELECT price_source, COUNT(*) FROM products GROUP BY price_source ORDER BY COUNT(*) DESC"
            )
        ),
        "size": dict(
            connection.execute(
                "SELECT size_source, COUNT(*) FROM products GROUP BY size_source ORDER BY COUNT(*) DESC"
            )
        ),
        "season": dict(
            connection.execute(
                "SELECT season_source, COUNT(*) FROM products GROUP BY season_source ORDER BY COUNT(*) DESC"
            )
        ),
        "color": dict(
            connection.execute(
                "SELECT color_source, COUNT(*) FROM products GROUP BY color_source ORDER BY COUNT(*) DESC"
            )
        ),
        "stock": dict(
            connection.execute(
                "SELECT stock_source, COUNT(*) FROM products GROUP BY stock_source ORDER BY COUNT(*) DESC"
            )
        ),
    }
    connection.close()

    report = {
        "source": str(args.source),
        "database": str(args.database),
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started_timer, 3),
        "expected_records": args.expected_records,
        "scanned_records": statistics["scanned_records"],
        "selected_before_deduplication": statistics["selected_records"],
        "duplicate_parent_asin": statistics["duplicate_parent_asin"],
        "database_records": database_records,
        "database_size_bytes": args.database.stat().st_size,
        "integrity_check": integrity_check,
        "missing_or_invalid_required": {
            "parent_asin": missing_required[0],
            "title": missing_required[1],
            "category": missing_required[2],
            "color_raw": missing_required[3],
            "color": missing_required[4],
            "image": missing_required[5],
            "size": missing_required[6],
            "season": missing_required[7],
            "price": missing_required[8],
            "stock": missing_required[9],
            "non_explicit_color_source": missing_required[10],
            "non_synthetic_stock_source": missing_required[11],
        },
        "stock": {
            "in_stock": statistics["in_stock"],
            "out_of_stock": statistics["out_of_stock"],
        },
        "category_counts": grouped_statistics(statistics, "category"),
        "color_counts": grouped_statistics(statistics, "color"),
        "field_source_counts": source_counts,
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if database_records != args.expected_records:
        raise RuntimeError(
            f"expected {args.expected_records} records, built {database_records}"
        )
    if integrity_check != "ok" or any(missing_required):
        raise RuntimeError("database validation failed")


if __name__ == "__main__":
    main()
