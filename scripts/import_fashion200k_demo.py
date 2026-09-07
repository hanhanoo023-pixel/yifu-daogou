from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path


HF_DATASET_REVISION = "b9d0e6c9c14337620e442c93f40f7906942fe893"
HF_PARQUET_URLS = tuple(
    "https://hf-mirror.com/datasets/Marqo/fashion200k/resolve/"
    f"{HF_DATASET_REVISION}/data/data-{index:05d}-of-00009.parquet"
    for index in range(9)
)

CATEGORY_MAP = {
    ("上衣", "上衣"): "top",
    ("外套", "夹克"): "outerwear",
    ("裙装", "连衣裙"): "dress",
    ("裙装", "半身裙"): "skirt",
    ("裤装", "裤子"): "pants",
}

COLOR_MAP = {
    "黑色": "black",
    "蓝色": "blue",
    "多色": "multicolor",
    "白色": "white",
    "灰色": "gray",
    "粉色": "pink",
    "棕色": "brown",
    "红色": "red",
    "绿色": "green",
    "紫色": "purple",
    "黄色": "yellow",
    "橙色": "orange",
}

SIZE_ORDER = ("XS", "S", "M", "L", "XL")
SEASONS = ("spring", "summer", "autumn", "winter")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def normalized_name(value: object) -> str:
    return " ".join(str(value).strip().lower().split())


def item_sort_key(item_id: str) -> tuple[int, int]:
    base, suffix = item_id.rsplit("_", 1)
    return int(base), int(suffix)


def connect_duckdb():
    import duckdb

    connection = duckdb.connect()
    connection.execute("PRAGMA disable_progress_bar")
    return connection


def map_products(demo_path: Path, mapping_path: Path, report_path: Path) -> None:
    demo = read_jsonl(demo_path)
    by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for product in demo:
        by_name[normalized_name(product["name"])].append(product)

    connection = connect_duckdb()
    connection.execute("CREATE TEMP TABLE wanted_names(name VARCHAR PRIMARY KEY)")
    connection.executemany(
        "INSERT INTO wanted_names VALUES (?)",
        [(name,) for name in sorted(by_name)],
    )

    candidates: dict[str, list[dict[str, object]]] = defaultdict(list)
    shard_counts: dict[str, int] = {}
    for shard_index, url in enumerate(HF_PARQUET_URLS):
        rows = connection.execute(
            f"""
            SELECT p.item_ID, p.category1, p.category2, p.category3
            FROM read_parquet('{url}') AS p
            INNER JOIN wanted_names AS w
                ON lower(trim(p.category3)) = w.name
            """
        ).fetchall()
        shard_counts[str(shard_index)] = len(rows)
        for item_id, category1, category2, category3 in rows:
            candidates[normalized_name(category3)].append(
                {
                    "item_id": item_id,
                    "category1": category1,
                    "category2": category2,
                    "source_name": category3,
                    "shard_index": shard_index,
                }
            )
        print(json.dumps({"mapped_shard": shard_index, "candidates": len(rows)}), flush=True)
    connection.close()

    mappings: list[dict[str, object]] = []
    unmapped: list[dict[str, object]] = []
    for name, products in sorted(by_name.items()):
        products.sort(key=lambda product: str(product["product_id"]))
        source_rows = sorted(
            candidates.get(name, []),
            key=lambda row: item_sort_key(str(row["item_id"])),
        )
        for index, product in enumerate(products):
            if index >= len(source_rows):
                unmapped.append(
                    {
                        "product_id": product["product_id"],
                        "name": product["name"],
                        "reason": "no unused exact category3 match",
                    }
                )
                continue
            mappings.append(
                {
                    "product_id": product["product_id"],
                    "name": product["name"],
                    **source_rows[index],
                }
            )

    write_jsonl(mapping_path, mappings)
    write_jsonl(mapping_path.with_name(mapping_path.stem + "_unmapped.jsonl"), unmapped)
    report = {
        "dataset": "Marqo/fashion200k",
        "dataset_revision": HF_DATASET_REVISION,
        "mapping_rule": "exact normalized demo name == Fashion200K category3; distinct item_ID allocation",
        "demo_products": len(demo),
        "demo_unique_names": len(by_name),
        "mapped_products": len(mappings),
        "unmapped_products": len(unmapped),
        "mapped_unique_names": len({normalized_name(row["name"]) for row in mappings}),
        "source_candidates": sum(shard_counts.values()),
        "shard_candidate_counts": shard_counts,
        "products_without_color": sum(not product.get("color") for product in demo),
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def fetch_images(
    mapping_path: Path,
    image_dir: Path,
    resolved_mapping_path: Path,
    report_path: Path,
    shard_index: int | None,
) -> None:
    from PIL import Image

    mappings = read_jsonl(mapping_path)
    by_shard: dict[int, list[dict[str, object]]] = defaultdict(list)
    for row in mappings:
        by_shard[int(row["shard_index"])].append(row)
    if shard_index is not None:
        by_shard = defaultdict(list, {shard_index: by_shard[shard_index]})

    image_dir.mkdir(parents=True, exist_ok=True)
    output_rows: list[dict[str, object]] = []
    format_counts: Counter[str] = Counter()
    connection = connect_duckdb()
    for shard_index, wanted_rows in sorted(by_shard.items()):
        connection.execute("DROP TABLE IF EXISTS wanted_items")
        connection.execute(
            "CREATE TEMP TABLE wanted_items(item_id VARCHAR PRIMARY KEY, product_id VARCHAR UNIQUE)"
        )
        connection.executemany(
            "INSERT INTO wanted_items VALUES (?, ?)",
            [
                (str(row["item_id"]), str(row["product_id"]))
                for row in wanted_rows
            ],
        )
        url = HF_PARQUET_URLS[shard_index]
        rows = connection.execute(
            f"""
            SELECT w.product_id, p.item_ID, p.image.path, p.image.bytes
            FROM read_parquet('{url}') AS p
            INNER JOIN wanted_items AS w ON p.item_ID = w.item_id
            ORDER BY w.product_id
            """
        ).fetchall()
        if len(rows) != len(wanted_rows):
            raise RuntimeError(
                f"shard {shard_index}: expected {len(wanted_rows)} images, got {len(rows)}"
            )
        mapping_lookup = {str(row["product_id"]): row for row in wanted_rows}
        for product_id, item_id, source_path, image_bytes in rows:
            with Image.open(io.BytesIO(image_bytes)) as image:
                image_format = str(image.format).lower()
                width, height = image.size
                image.verify()
            extension = {"jpeg": ".jpg", "png": ".png", "webp": ".webp"}[image_format]
            destination = image_dir / f"{product_id}{extension}"
            if destination.exists():
                raise FileExistsError(destination)
            destination.write_bytes(image_bytes)
            format_counts[image_format] += 1
            output_rows.append(
                {
                    **mapping_lookup[str(product_id)],
                    "source_image_path": source_path,
                    "image_file": str(destination),
                    "image_url": f"/api/fashion200k-images/{destination.name}",
                    "image_format": image_format,
                    "image_width": width,
                    "image_height": height,
                    "image_bytes": len(image_bytes),
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                }
            )
        print(json.dumps({"fetched_shard": shard_index, "images": len(rows)}), flush=True)
    connection.close()

    output_rows.sort(key=lambda row: str(row["product_id"]))
    write_jsonl(resolved_mapping_path, output_rows)
    report = {
        "mapped_products": len(mappings),
        "images_fetched": len(output_rows),
        "image_bytes": sum(int(row["image_bytes"]) for row in output_rows),
        "image_formats": dict(format_counts),
        "output_directory": str(image_dir),
        "resolved_mapping": str(resolved_mapping_path),
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def selected_size(size_stock: dict[str, object]) -> str:
    available = [size for size in SIZE_ORDER if int(size_stock.get(size, 0)) > 0]
    if not available:
        raise ValueError("size_stock has no available size")
    return max(available, key=lambda size: (int(size_stock[size]), -SIZE_ORDER.index(size)))


def merge_image_mappings(
    input_paths: list[Path], output_path: Path, report_path: Path
) -> None:
    rows = [row for path in input_paths for row in read_jsonl(path)]
    product_ids = [str(row["product_id"]) for row in rows]
    if len(product_ids) != len(set(product_ids)):
        raise RuntimeError("duplicate product_id found while merging image mappings")
    rows.sort(key=lambda row: str(row["product_id"]))
    write_jsonl(output_path, rows)
    report = {
        "input_files": [str(path) for path in input_paths],
        "images": len(rows),
        "image_bytes": sum(int(row["image_bytes"]) for row in rows),
        "image_formats": dict(Counter(str(row["image_format"]) for row in rows)),
        "output": str(output_path),
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def synthetic_season(product_id: str) -> str:
    digest = hashlib.sha256(f"{product_id}:season".encode()).digest()
    return SEASONS[int.from_bytes(digest[:4], "big") % len(SEASONS)]


def import_database(
    demo_path: Path,
    resolved_mapping_path: Path,
    database_path: Path,
    backup_path: Path,
    report_path: Path,
) -> None:
    if backup_path.exists():
        raise FileExistsError(backup_path)
    demo = {str(row["product_id"]): row for row in read_jsonl(demo_path)}
    mappings = read_jsonl(resolved_mapping_path)
    existing = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    before_count = existing.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    existing.close()
    shutil.copy2(database_path, backup_path)

    rows: list[tuple[object, ...]] = []
    skipped_without_color: list[str] = []
    for mapping in mappings:
        product_id = str(mapping["product_id"])
        product = demo[product_id]
        colors = [str(value) for value in product.get("color") or []]
        if not colors:
            skipped_without_color.append(product_id)
            continue
        unknown_colors = [color for color in colors if color not in COLOR_MAP]
        if unknown_colors:
            raise ValueError(f"{product_id}: unknown colors {unknown_colors}")
        category_key = (str(product["category"]), str(product["subcategory"]))
        category_norm = CATEGORY_MAP[category_key]
        size_stock = dict(product["size_stock"])
        size = selected_size(size_stock)
        stock_quantity = sum(int(value) for value in size_stock.values())
        description = "；".join(str(value) for value in product.get("selling_points") or []) or None
        rows.append(
            (
                product_id,
                str(product["name"]),
                None,
                f"Fashion200K > {product['category']} > {product['subcategory']}",
                category_norm,
                "women",
                "/".join(colors),
                COLOR_MAP[colors[0]],
                json.dumps(size_stock, ensure_ascii=False, sort_keys=True),
                size,
                product.get("material"),
                synthetic_season(product_id),
                float(product["price"]),
                str(mapping["image_url"]),
                0.0,
                0,
                stock_quantity,
                " ".join(str(value) for value in product.get("style_tags") or []) or None,
                description,
                "synthetic_demo",
                "synthetic_demo",
                "fashion200k_title",
                "synthetic_demo",
                "fashion200k_category",
                "missing",
                "synthetic_demo",
            )
        )

    connection = sqlite3.connect(database_path)
    duplicate_count = connection.execute(
        "SELECT COUNT(*) FROM products WHERE parent_asin LIKE 'F200K_%'"
    ).fetchone()[0]
    if duplicate_count:
        raise RuntimeError(f"database already contains {duplicate_count} F200K products")
    connection.executemany(
        """
        INSERT INTO products (
            parent_asin, title, brand, category_path, category_norm, department,
            color_raw, color_norm, size_raw, size_norm, material, season, price,
            image_url, rating, rating_count, stock_quantity, features_text,
            description_text, price_source, size_source, color_source,
            season_source, category_source, brand_source, stock_source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    after_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    imported_count = connection.execute(
        "SELECT COUNT(*) FROM products WHERE parent_asin LIKE 'F200K_%'"
    ).fetchone()[0]
    connection.close()
    if integrity != "ok":
        raise RuntimeError(f"database integrity check failed: {integrity}")

    report = {
        "database": str(database_path),
        "backup": str(backup_path),
        "before_count": before_count,
        "mapped_images": len(mappings),
        "skipped_without_color": len(skipped_without_color),
        "skipped_without_color_ids": skipped_without_color,
        "imported_count": imported_count,
        "after_count": after_count,
        "integrity_check": integrity,
    }
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    map_parser = subparsers.add_parser("map")
    map_parser.add_argument("--demo", type=Path, required=True)
    map_parser.add_argument("--mapping", type=Path, required=True)
    map_parser.add_argument("--report", type=Path, required=True)

    fetch_parser = subparsers.add_parser("fetch-images")
    fetch_parser.add_argument("--mapping", type=Path, required=True)
    fetch_parser.add_argument("--image-dir", type=Path, required=True)
    fetch_parser.add_argument("--resolved-mapping", type=Path, required=True)
    fetch_parser.add_argument("--report", type=Path, required=True)
    fetch_parser.add_argument("--shard-index", type=int, choices=range(9))

    merge_parser = subparsers.add_parser("merge-images")
    merge_parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    merge_parser.add_argument("--report", type=Path, required=True)

    import_parser = subparsers.add_parser("import-db")
    import_parser.add_argument("--demo", type=Path, required=True)
    import_parser.add_argument("--mapping", type=Path, required=True)
    import_parser.add_argument("--database", type=Path, required=True)
    import_parser.add_argument("--backup", type=Path, required=True)
    import_parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "map":
        map_products(args.demo, args.mapping, args.report)
    elif args.command == "fetch-images":
        fetch_images(
            args.mapping,
            args.image_dir,
            args.resolved_mapping,
            args.report,
            args.shard_index,
        )
    elif args.command == "merge-images":
        merge_image_mappings(args.inputs, args.output, args.report)
    else:
        import_database(args.demo, args.mapping, args.database, args.backup, args.report)


if __name__ == "__main__":
    main()
