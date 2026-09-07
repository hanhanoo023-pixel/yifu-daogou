from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "products_complete.db"
BUSINESS_ANNOTATION_VERSION = "fashion200k-slots-v1.3"
BUSINESS_FIELDS = (
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
VARIANT_FILES = {
    "amazon": "catalog_amazon_current.db",
    "fashion200k": "catalog_fashion_v1.3.db",
    "hybrid": "catalog_hybrid.db",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build isolated Amazon, Fashion200K, and hybrid catalog databases."
    )
    parser.add_argument("--source-database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--business-annotations", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-amazon", type=int, default=252_413)
    parser.add_argument("--expected-fashion200k", type=int, default=3_294)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_sql(connection: sqlite3.Connection, object_type: str, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
        (object_type, name),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"missing {object_type} schema: {name}")
    return str(row[0])


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
            missing = sorted(set(BUSINESS_FIELDS) - row.keys())
            if missing:
                raise ValueError(f"{product_id} missing business fields: {missing}")
            annotations[product_id] = row
    missing_products = sorted(wanted_product_ids - annotations.keys())
    if missing_products:
        raise ValueError(
            f"Fashion200K products missing from business annotations: {missing_products[:10]}"
        )
    return annotations


def joined(values: object) -> str:
    if not isinstance(values, list):
        raise ValueError("business multi-value field must be a list")
    return "、".join(str(value) for value in values if str(value))


def business_description(annotation: dict[str, Any]) -> str:
    parts: list[str] = []
    for field, label in (
        ("subcategory", "品类"),
        ("color", "颜色"),
        ("color_depth", "明暗"),
        ("style", "风格"),
        ("occasion", "场景"),
        ("fit", "版型"),
        ("material", "材质"),
    ):
        value = annotation[field]
        rendered = joined(value) if isinstance(value, list) else str(value)
        if rendered and rendered != "未知":
            parts.append(f"{label}={rendered}")
    return "；".join(parts)


def enriched_fashion_row(
    row: sqlite3.Row,
    columns: list[str],
    annotation: dict[str, Any],
) -> list[Any]:
    values = {column: row[column] for column in columns}
    description = business_description(annotation)
    existing_features = str(values.get("features_text") or "").strip()
    existing_description = str(values.get("description_text") or "").strip()
    values["features_text"] = "；".join(
        value for value in (existing_features, f"业务结构化标签：{description}") if value
    )
    values["description_text"] = "；".join(
        value for value in (existing_description, f"业务标注描述：{description}") if value
    )
    return [values[column] for column in columns]


def create_variant_schema(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
) -> None:
    target.execute(schema_sql(source, "table", "products"))
    target.execute(schema_sql(source, "table", "product_moderation"))
    target.executescript(
        """
        CREATE TABLE product_business_slots (
            product_id TEXT PRIMARY KEY,
            annotation_version TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            colors_json TEXT NOT NULL,
            color_depth TEXT NOT NULL,
            styles_json TEXT NOT NULL,
            occasions_json TEXT NOT NULL,
            fits_json TEXT NOT NULL,
            materials_json TEXT NOT NULL,
            sizes_json TEXT NOT NULL,
            structured_description TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(parent_asin)
        );

        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX idx_business_slots_category
            ON product_business_slots(category, subcategory);
        CREATE INDEX idx_business_slots_color_depth
            ON product_business_slots(color_depth);
        """
    )


def create_source_indexes(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
) -> None:
    for row in source.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type='index'
          AND tbl_name='products'
          AND sql IS NOT NULL
        ORDER BY name
        """
    ):
        target.execute(str(row[0]))


def source_query(variant: str) -> str:
    if variant == "amazon":
        return """
            SELECT p.* FROM products AS p
            WHERE p.parent_asin NOT LIKE 'F200K_%'
            ORDER BY p.id
        """
    if variant == "fashion200k":
        return """
            SELECT p.* FROM products AS p
            WHERE p.parent_asin LIKE 'F200K_%'
              AND NOT EXISTS (
                  SELECT 1 FROM product_moderation AS moderation
                  WHERE moderation.product_id=p.parent_asin
                    AND moderation.status='FAIL'
              )
            ORDER BY p.id
        """
    if variant == "hybrid":
        return """
            SELECT p.* FROM products AS p
            WHERE p.parent_asin NOT LIKE 'F200K_%'
               OR NOT EXISTS (
                  SELECT 1 FROM product_moderation AS moderation
                  WHERE moderation.product_id=p.parent_asin
                    AND moderation.status='FAIL'
               )
            ORDER BY p.id
        """
    raise ValueError(f"unsupported catalog variant: {variant}")


def insert_business_slots(
    target: sqlite3.Connection,
    product_id: str,
    annotation: dict[str, Any],
) -> None:
    target.execute(
        """
        INSERT INTO product_business_slots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            BUSINESS_ANNOTATION_VERSION,
            annotation["category"],
            annotation["subcategory"],
            json.dumps(annotation["color"], ensure_ascii=False, sort_keys=True),
            annotation["color_depth"],
            json.dumps(annotation["style"], ensure_ascii=False, sort_keys=True),
            json.dumps(annotation["occasion"], ensure_ascii=False, sort_keys=True),
            json.dumps(annotation["fit"], ensure_ascii=False, sort_keys=True),
            json.dumps(annotation["material"], ensure_ascii=False, sort_keys=True),
            json.dumps(annotation["size"], ensure_ascii=False, sort_keys=True),
            business_description(annotation),
        ),
    )


def build_variant(
    *,
    source: sqlite3.Connection,
    output_path: Path,
    variant: str,
    business_annotations: dict[str, dict[str, Any]],
    source_database_sha256: str,
    business_annotations_sha256: str,
) -> dict[str, Any]:
    if output_path.exists():
        raise FileExistsError(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(output_path)
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA journal_mode=OFF")
    target.execute("PRAGMA synchronous=OFF")
    create_variant_schema(source, target)
    columns = [str(row[1]) for row in source.execute("PRAGMA table_info(products)")]
    non_id_columns = [column for column in columns if column != "id"]
    insert_sql = (
        f"INSERT INTO products ({','.join(columns)}) VALUES "
        f"({','.join('?' for _ in columns)})"
    )
    product_count = 0
    fashion_count = 0
    for source_row in source.execute(source_query(variant)):
        product_count += 1
        product_id = str(source_row["parent_asin"])
        annotation: dict[str, Any] | None = None
        if product_id.startswith("F200K_"):
            fashion_count += 1
            annotation = business_annotations[product_id]
            values = enriched_fashion_row(
                source_row,
                non_id_columns,
                annotation,
            )
        else:
            values = [source_row[column] for column in non_id_columns]
        target.execute(insert_sql, [product_count, *values])
        if annotation is not None:
            insert_business_slots(target, product_id, annotation)
    create_source_indexes(source, target)
    metadata = {
        "variant": variant,
        "business_annotation_version": BUSINESS_ANNOTATION_VERSION,
        "source_database_sha256": source_database_sha256,
        "business_annotations_sha256": business_annotations_sha256,
        "products": product_count,
        "amazon_products": product_count - fashion_count,
        "fashion200k_products": fashion_count,
    }
    target.executemany(
        "INSERT INTO catalog_metadata VALUES (?, ?)",
        [(key, json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
    )
    target.commit()
    integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    distinct_images = int(
        target.execute("SELECT COUNT(DISTINCT image_url) FROM products").fetchone()[0]
    )
    business_slot_rows = int(
        target.execute("SELECT COUNT(*) FROM product_business_slots").fetchone()[0]
    )
    category_counts = dict(
        target.execute(
            "SELECT category_norm, COUNT(*) FROM products GROUP BY category_norm ORDER BY category_norm"
        )
    )
    target.close()
    if integrity != "ok":
        raise RuntimeError(f"{variant} database integrity check failed: {integrity}")
    return {
        **metadata,
        "database": str(output_path),
        "database_sha256": sha256(output_path),
        "integrity_check": integrity,
        "distinct_images": distinct_images,
        "business_slot_rows": business_slot_rows,
        "category_counts": category_counts,
    }


def main() -> None:
    args = parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    output_paths = {
        variant: args.output_directory / filename
        for variant, filename in VARIANT_FILES.items()
    }
    existing = [str(path) for path in output_paths.values() if path.exists()]
    if existing:
        raise FileExistsError(f"catalog outputs already exist: {existing}")

    source = sqlite3.connect(f"file:{args.source_database}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    amazon_count = int(
        source.execute(
            "SELECT COUNT(*) FROM products WHERE parent_asin NOT LIKE 'F200K_%'"
        ).fetchone()[0]
    )
    active_fashion_ids = {
        str(row[0])
        for row in source.execute(
            """
            SELECT p.parent_asin FROM products AS p
            WHERE p.parent_asin LIKE 'F200K_%'
              AND NOT EXISTS (
                  SELECT 1 FROM product_moderation AS moderation
                  WHERE moderation.product_id=p.parent_asin
                    AND moderation.status='FAIL'
              )
            """
        )
    }
    if amazon_count != args.expected_amazon:
        raise ValueError(f"expected {args.expected_amazon} Amazon products, got {amazon_count}")
    if len(active_fashion_ids) != args.expected_fashion200k:
        raise ValueError(
            f"expected {args.expected_fashion200k} active Fashion200K products, "
            f"got {len(active_fashion_ids)}"
        )

    business_annotations = load_business_annotations(
        args.business_annotations,
        active_fashion_ids,
    )
    source_database_sha256 = sha256(args.source_database)
    business_annotations_sha256 = sha256(args.business_annotations)
    reports = {
        variant: build_variant(
            source=source,
            output_path=output_path,
            variant=variant,
            business_annotations=business_annotations,
            source_database_sha256=source_database_sha256,
            business_annotations_sha256=business_annotations_sha256,
        )
        for variant, output_path in output_paths.items()
    }
    source.close()

    report = {
        "source_database": str(args.source_database),
        "source_database_sha256": source_database_sha256,
        "business_annotations": str(args.business_annotations),
        "business_annotations_sha256": business_annotations_sha256,
        "variants": reports,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
