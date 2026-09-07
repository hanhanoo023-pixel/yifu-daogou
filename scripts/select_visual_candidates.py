from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "products_complete.db"
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "curated_catalog.yaml"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select a diverse, image-backed catalog for strict visual review."
    )
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--business-annotations", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("curated catalog config must be a mapping")
    quotas = config.get("candidate_quotas")
    if not isinstance(quotas, dict) or not quotas:
        raise ValueError("candidate_quotas must be a non-empty mapping")
    if any(not isinstance(value, int) or value <= 0 for value in quotas.values()):
        raise ValueError("every candidate quota must be a positive integer")
    return config


def load_active_products(
    database_path: Path,
    categories: Iterable[str],
) -> list[dict[str, Any]]:
    category_values = sorted(set(categories))
    placeholders = ",".join("?" for _ in category_values)
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        f"""
        SELECT p.*
        FROM products AS p
        WHERE p.category_norm IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1
              FROM product_moderation AS moderation
              WHERE moderation.product_id = p.parent_asin
                AND moderation.status = 'FAIL'
          )
        ORDER BY p.id
        """,
        category_values,
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def load_business_annotations(
    path: Path,
    wanted_product_ids: set[str],
) -> dict[str, dict[str, Any]]:
    annotations: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            row = json.loads(line)
            product_id = str(row["product_id"])
            if product_id not in wanted_product_ids:
                continue
            if product_id in annotations:
                raise ValueError(
                    f"duplicate business annotation for {product_id} at line {line_number}"
                )
            annotations[product_id] = row
    return annotations


def text_present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def product_quality_score(
    product: dict[str, Any],
    business_slots: dict[str, dict[str, Any]],
    weights: dict[str, int],
) -> int:
    product_id = str(product["parent_asin"])
    score = 0
    if product_id.startswith("F200K_"):
        score += int(weights["audited_fashion200k"])
    if product_id in business_slots:
        score += int(weights["business_slots_available"])
    if text_present(product.get("features_text")):
        score += int(weights["features_text"])
    if text_present(product.get("description_text")):
        score += int(weights["description_text"])
    if text_present(product.get("material")):
        score += int(weights["material"])
    if int(product.get("rating_count") or 0) > 0:
        score += int(weights["rating_evidence"])
    if product.get("price_source") == "original":
        score += int(weights["original_price"])
    if product.get("size_source") == "explicit_amazon":
        score += int(weights["original_size"])
    return score


def deduplicate_exact_images(
    products: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    by_image: dict[str, dict[str, Any]] = {}
    for product in products:
        image_url = str(product["image_url"]).strip()
        current = by_image.get(image_url)
        if current is None or (
            -int(product["selection_score"]), str(product["parent_asin"])
        ) < (
            -int(current["selection_score"]), str(current["parent_asin"])
        ):
            by_image[image_url] = product
    return list(by_image.values()), len(products) - len(by_image)


def balanced_category_selection(
    products: list[dict[str, Any]],
    quota: int,
) -> list[dict[str, Any]]:
    by_color: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in products:
        by_color[str(product["color_norm"])].append(product)
    queues: dict[str, deque[dict[str, Any]]] = {}
    for color, rows in by_color.items():
        rows.sort(key=lambda row: (-int(row["selection_score"]), str(row["parent_asin"])))
        queues[color] = deque(rows)

    selected: list[dict[str, Any]] = []
    colors = sorted(queues)
    while colors and len(selected) < quota:
        remaining_colors: list[str] = []
        for color in colors:
            queue = queues[color]
            if queue and len(selected) < quota:
                selected.append(queue.popleft())
            if queue:
                remaining_colors.append(color)
        colors = remaining_colors
    return selected


def select_candidates(
    products: list[dict[str, Any]],
    config: dict[str, Any],
    business_slots: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    selection = dict(config["selection"])
    weights = {key: int(value) for key, value in config["quality_score"].items()}
    eligible: list[dict[str, Any]] = []
    rejected = Counter()
    for product in products:
        title = str(product["title"]).strip()
        image_url = str(product["image_url"]).strip()
        if selection["require_image"] and not image_url:
            rejected["missing_image"] += 1
            continue
        if len(title) < int(selection["minimum_title_characters"]):
            rejected["short_title"] += 1
            continue
        product = dict(product)
        product["selection_score"] = product_quality_score(
            product, business_slots, weights
        )
        eligible.append(product)

    duplicates_removed = 0
    if selection["deduplicate_exact_image_url"]:
        eligible, duplicates_removed = deduplicate_exact_images(eligible)

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in eligible:
        by_category[str(product["category_norm"])].append(product)

    selected: list[dict[str, Any]] = []
    for category, quota_value in config["candidate_quotas"].items():
        category_rows = by_category.get(str(category), [])
        if selection["balance_by_color"]:
            chosen = balanced_category_selection(category_rows, int(quota_value))
        else:
            chosen = sorted(
                category_rows,
                key=lambda row: (-int(row["selection_score"]), str(row["parent_asin"])),
            )[: int(quota_value)]
        selected.extend(chosen)

    selected.sort(key=lambda row: (str(row["category_norm"]), str(row["parent_asin"])))
    statistics = {
        "missing_image": rejected["missing_image"],
        "short_title": rejected["short_title"],
        "exact_image_duplicates_removed": duplicates_removed,
    }
    return selected, statistics


def candidate_record(
    product: dict[str, Any],
    business_slots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    product_id = str(product["parent_asin"])
    slots = business_slots.get(product_id)
    return {
        "product_id": product_id,
        "title": product["title"],
        "category": product["category_norm"],
        "image_url": product["image_url"],
        "color_raw": product["color_raw"],
        "color_norm": product["color_norm"],
        "material": product["material"],
        "features_text": product["features_text"],
        "description_text": product["description_text"],
        "category_source": product["category_source"],
        "color_source": product["color_source"],
        "material_source": "explicit_product_text" if text_present(product["material"]) else None,
        "selection_score": int(product["selection_score"]),
        "business_slots": (
            {
                key: slots[key]
                for key in (
                    "category",
                    "subcategory",
                    "color",
                    "color_depth",
                    "style",
                    "occasion",
                    "fit",
                    "material",
                    "size",
                )
            }
            if slots is not None
            else None
        ),
    }


def write_candidates(path: Path, rows: list[dict[str, Any]]) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    args = parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    config = load_config(args.config)
    products = load_active_products(args.database, config["candidate_quotas"])
    f200k_ids = {
        str(product["parent_asin"])
        for product in products
        if str(product["parent_asin"]).startswith("F200K_")
    }
    business_slots = load_business_annotations(args.business_annotations, f200k_ids)
    selected, rejected = select_candidates(products, config, business_slots)
    output_rows = [candidate_record(product, business_slots) for product in selected]
    write_candidates(args.output, output_rows)

    category_counts = Counter(row["category"] for row in output_rows)
    color_counts = Counter(row["color_norm"] for row in output_rows)
    source_counts = Counter(
        "fashion200k" if row["product_id"].startswith("F200K_") else "amazon"
        for row in output_rows
    )
    report = {
        "version": config["version"],
        "database": str(args.database),
        "database_sha256": sha256(args.database),
        "business_annotations": str(args.business_annotations),
        "business_annotations_sha256": sha256(args.business_annotations),
        "eligible_before_selection": len(products),
        "selected": len(output_rows),
        "rejected": rejected,
        "category_counts": dict(sorted(category_counts.items())),
        "color_counts": dict(sorted(color_counts.items())),
        "source_counts": dict(sorted(source_counts.items())),
        "business_annotation_matches": sum(
            row["business_slots"] is not None for row in output_rows
        ),
        "output": str(args.output),
        "output_sha256": sha256(args.output),
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
