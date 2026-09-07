from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


BATCH_SIZE = 5_000

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

COLOR_KEYWORD_MAP = {
    keyword: normalized
    for normalized, keywords in COLOR_RULES
    for keyword in keywords
}
COLOR_PATTERN = re.compile(
    r"(?<![a-z])(" + "|".join(
        re.escape(keyword)
        for keyword in sorted(COLOR_KEYWORD_MAP, key=len, reverse=True)
    ) + r")(?![a-z])",
    flags=re.IGNORECASE,
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

INFERRED_SIZE_RULES = (
    ("4XL", (r"\b4x[- ]?large\b", r"\b4xl\b")),
    ("3XL", (r"\b3x[- ]?large\b", r"\b3xl\b", r"\bxxx[- ]?large\b")),
    ("2XL", (r"\b2x[- ]?large\b", r"\b2xl\b", r"\bxx[- ]?large\b", r"\bxxl\b")),
    ("XL", (r"\bx[- ]?large\b", r"\bxl\b")),
    ("XS", (r"\bx[- ]?small\b", r"\bxs\b")),
    ("L", (r"\bsizes?\s*[:\-]?\s*l\b", r"(?:^|[\(\[,;/\-])\s*l\s*(?:$|[\)\],;/\-])")),
    ("M", (r"\bsizes?\s*[:\-]?\s*m\b", r"(?:^|[\(\[,;/\-])\s*m\s*(?:$|[\)\],;/\-])")),
    ("S", (r"\bsizes?\s*[:\-]?\s*s\b", r"(?:^|[\(\[,;/\-])\s*s\s*(?:$|[\)\],;/\-])")),
)

SEASON_RULES = (
    ("summer", ("summer", "hot weather", "beach", "lightweight", "breathable", "sleeveless", "tank top", "camisole")),
    ("winter", ("winter", "cold weather", "thermal", "fleece", "insulated", "down jacket", "snow")),
    ("spring", ("spring",)),
    ("autumn", ("autumn", "fall season")),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


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


def normalize_color(raw_color: str | None, searchable_text: str) -> str | None:
    text = raw_color if raw_color else searchable_text
    match = COLOR_PATTERN.search(text)
    return COLOR_KEYWORD_MAP[match.group(1).lower()] if match else None


def normalize_size(raw_size: str | None, searchable_text: str) -> str | None:
    text = raw_size if raw_size else searchable_text
    rules = SIZE_RULES if raw_size else INFERRED_SIZE_RULES
    for normalized, patterns in rules:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            return normalized
    return None


def normalize_season(raw_season: str | None, searchable_text: str) -> str | None:
    text = raw_season if raw_season else searchable_text
    return match_keywords(text, SEASON_RULES)


def choose_image(images: list[dict[str, str]]) -> str | None:
    main_images = [image for image in images if image.get("variant") == "MAIN"]
    candidates = main_images if main_images else images
    if not candidates:
        return None
    image = candidates[0]
    return image.get("hi_res") or image.get("large") or image.get("thumb")


def flatten_text(values: list[object]) -> str | None:
    text = " ".join(str(value).strip() for value in values if str(value).strip())
    return text or None


def product_row(item: dict[str, object]) -> tuple[object, ...]:
    details = item["details"] or {}
    categories = item["categories"] or []
    features_text = flatten_text(item["features"] or [])
    description_text = flatten_text(item["description"] or [])
    title = str(item["title"])
    category_path = " > ".join(str(category) for category in categories)
    raw_color_value = details.get("Color")
    raw_size_value = details.get("Size")
    raw_season_value = details.get("Seasons")
    raw_color = str(raw_color_value).strip() if raw_color_value else None
    raw_size = str(raw_size_value).strip() if raw_size_value else None
    raw_season = str(raw_season_value).strip() if raw_season_value else None
    material_value = details.get("Material") or details.get("Fabric Type")
    material = str(material_value).strip() if material_value else None
    searchable_text = " ".join(
        value
        for value in (title, category_path, features_text, description_text, material)
        if value
    )
    attribute_text = " ".join(
        value
        for value in (title, category_path, features_text)
        if value
    )
    return (
        item["parent_asin"],
        title,
        item["store"] or None,
        category_path,
        normalize_category([str(category) for category in categories], title),
        details.get("Department") or None,
        raw_color,
        normalize_color(raw_color, attribute_text),
        raw_size,
        normalize_size(raw_size, title),
        material,
        normalize_season(raw_season, searchable_text),
        item["price"],
        choose_image(item["images"] or []),
        item["average_rating"],
        item["rating_number"],
        item["stock_quantity"],
        features_text,
        description_text,
    )


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            parent_asin TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            brand TEXT,
            category_path TEXT,
            category_norm TEXT,
            department TEXT,
            color_raw TEXT,
            color_norm TEXT,
            size_raw TEXT,
            size_norm TEXT,
            material TEXT,
            season TEXT,
            price REAL,
            image_url TEXT,
            rating REAL NOT NULL,
            rating_count INTEGER NOT NULL,
            stock_quantity INTEGER NOT NULL CHECK (stock_quantity BETWEEN 0 AND 100),
            features_text TEXT,
            description_text TEXT
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
        CREATE INDEX idx_products_price
            ON products (price);
        ANALYZE;
        """
    )


def update_statistics(statistics: dict[str, object], row: tuple[object, ...]) -> None:
    statistics["imported_records"] += 1
    if row[4]:
        statistics["category_normalized"] += 1
        statistics["category_counts"][row[4]] += 1
    if row[7]:
        statistics["color_normalized"] += 1
        statistics["color_counts"][row[7]] += 1
    if row[9]:
        statistics["size_normalized"] += 1
        statistics["size_counts"][row[9]] += 1
    if row[11]:
        statistics["season_normalized"] += 1
        statistics["season_counts"][row[11]] += 1
    if row[12] is not None:
        statistics["with_price"] += 1
    if row[13]:
        statistics["with_image"] += 1
    if row[16] == 0:
        statistics["out_of_stock"] += 1
    else:
        statistics["in_stock"] += 1


def serializable_statistics(statistics: dict[str, object]) -> dict[str, object]:
    result = dict(statistics)
    for key in ("category_counts", "color_counts", "size_counts", "season_counts"):
        result[key] = dict(statistics[key].most_common())
    return result


def main() -> None:
    args = parse_args()
    if args.database.exists():
        raise FileExistsError(args.database)
    if args.report.exists():
        raise FileExistsError(args.report)

    started_at = datetime.now(timezone.utc)
    started_timer = time.perf_counter()
    statistics: dict[str, object] = {
        "imported_records": 0,
        "category_normalized": 0,
        "color_normalized": 0,
        "size_normalized": 0,
        "season_normalized": 0,
        "with_price": 0,
        "with_image": 0,
        "in_stock": 0,
        "out_of_stock": 0,
        "category_counts": Counter(),
        "color_counts": Counter(),
        "size_counts": Counter(),
        "season_counts": Counter(),
    }

    connection = sqlite3.connect(args.database)
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA cache_size = -200000")
    create_schema(connection)

    insert_sql = """
        INSERT INTO products (
            parent_asin, title, brand, category_path, category_norm, department,
            color_raw, color_norm, size_raw, size_norm, material, season, price,
            image_url, rating, rating_count, stock_quantity, features_text,
            description_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    batch: list[tuple[object, ...]] = []
    connection.execute("BEGIN")
    with args.source.open("r", encoding="utf-8") as source:
        for line in source:
            item = json.loads(line)
            row = product_row(item)
            batch.append(row)
            update_statistics(statistics, row)
            if len(batch) == BATCH_SIZE:
                connection.executemany(insert_sql, batch)
                batch.clear()
        if batch:
            connection.executemany(insert_sql, batch)
    connection.commit()
    create_indexes(connection)
    connection.commit()
    connection.close()

    finished_at = datetime.now(timezone.utc)
    report = {
        "source": str(args.source),
        "database": str(args.database),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_seconds": round(time.perf_counter() - started_timer, 3),
        "database_size_bytes": args.database.stat().st_size,
        **serializable_statistics(statistics),
    }
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
