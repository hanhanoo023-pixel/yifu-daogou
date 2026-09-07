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
ANNOTATION_VERSION = "visual-annotation-v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a new curated product database from PASS visual audits."
    )
    parser.add_argument("--source-database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--audits", type=Path, required=True)
    parser.add_argument("--output-database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--minimum-products", type=int, required=True)
    parser.add_argument("--maximum-products", type=int, required=True)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_passed_audits(path: Path) -> list[dict[str, Any]]:
    passed: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            row = json.loads(line)
            product_id = str(row["product_id"])
            if product_id in seen:
                raise ValueError(f"duplicate audit product_id at line {line_number}: {product_id}")
            seen.add(product_id)
            if row["status"] == "PASS":
                if not isinstance(row.get("final_annotation"), dict):
                    raise ValueError(f"PASS row has no final annotation: {product_id}")
                passed.append(row)
    return passed


def chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def source_rows_by_product_id(
    connection: sqlite3.Connection,
    product_ids: list[str],
) -> dict[str, sqlite3.Row]:
    connection.row_factory = sqlite3.Row
    rows: dict[str, sqlite3.Row] = {}
    for batch in chunks(product_ids):
        placeholders = ",".join("?" for _ in batch)
        for row in connection.execute(
            f"SELECT * FROM products WHERE parent_asin IN ({placeholders})",
            batch,
        ):
            rows[str(row["parent_asin"])] = row
    missing = sorted(set(product_ids) - rows.keys())
    if missing:
        raise ValueError(f"audited products missing from source database: {missing[:10]}")
    return rows


def schema_sql(connection: sqlite3.Connection, object_type: str, name: str) -> str:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type=? AND name=?",
        (object_type, name),
    ).fetchone()
    if row is None or row[0] is None:
        raise ValueError(f"missing {object_type} schema: {name}")
    return str(row[0])


def create_output_schema(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
) -> None:
    target.execute(schema_sql(source, "table", "products"))
    target.execute(schema_sql(source, "table", "product_moderation"))
    target.executescript(
        """
        CREATE TABLE product_visual_annotations (
            product_id TEXT PRIMARY KEY,
            annotation_version TEXT NOT NULL,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            color_depth TEXT NOT NULL,
            visual_description TEXT NOT NULL,
            overall_confidence REAL NOT NULL,
            audit_rounds INTEGER NOT NULL
        );

        CREATE TABLE product_colors (
            product_id TEXT NOT NULL,
            color_norm TEXT NOT NULL,
            role TEXT NOT NULL,
            coverage TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (product_id, color_norm),
            FOREIGN KEY (product_id) REFERENCES products(parent_asin)
        );

        CREATE TABLE product_visual_tags (
            product_id TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            tag_value TEXT NOT NULL,
            confidence REAL NOT NULL,
            evidence TEXT NOT NULL,
            PRIMARY KEY (product_id, tag_type, tag_value),
            FOREIGN KEY (product_id) REFERENCES products(parent_asin)
        );

        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE INDEX idx_product_colors_value_role
            ON product_colors(color_norm, role, product_id);
        CREATE INDEX idx_product_visual_tags_type_value
            ON product_visual_tags(tag_type, tag_value, product_id);
        """
    )
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


def insert_products(
    target: sqlite3.Connection,
    source_rows: dict[str, sqlite3.Row],
    audits: list[dict[str, Any]],
) -> None:
    source_columns = [
        str(row[1]) for row in target.execute("PRAGMA table_info(products)")
    ]
    non_id_columns = [column for column in source_columns if column != "id"]
    placeholders = ",".join("?" for _ in source_columns)
    insert_sql = (
        f"INSERT INTO products ({','.join(source_columns)}) VALUES ({placeholders})"
    )
    ordered_audits = sorted(
        audits,
        key=lambda row: (
            str(row["final_annotation"]["category"]["value"]),
            str(row["product_id"]),
        ),
    )
    for new_id, audit in enumerate(ordered_audits, start=1):
        product_id = str(audit["product_id"])
        source_row = source_rows[product_id]
        target.execute(
            insert_sql,
            [new_id, *(source_row[column] for column in non_id_columns)],
        )
        annotation = audit["final_annotation"]
        target.execute(
            """
            INSERT INTO product_visual_annotations VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                ANNOTATION_VERSION,
                annotation["category"]["value"],
                annotation["subcategory"]["value"],
                annotation["color_depth"]["value"],
                annotation["visual_description"],
                float(annotation["overall_confidence"]),
                len(audit["audit_rounds"]),
            ),
        )
        target.executemany(
            "INSERT INTO product_colors VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    product_id,
                    color["value"],
                    color["role"],
                    color["coverage"],
                    float(color["confidence"]),
                    color["evidence"],
                )
                for color in annotation["colors"]
            ],
        )
        for field, tag_type in (
            ("patterns", "pattern"),
            ("styles", "style"),
            ("fits", "fit"),
            ("design_details", "design_detail"),
            ("materials", "material"),
            ("occasions", "occasion"),
            ("seasons", "season"),
        ):
            target.executemany(
                "INSERT INTO product_visual_tags VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        product_id,
                        tag_type,
                        tag["value"],
                        float(tag["confidence"]),
                        tag["evidence"],
                    )
                    for tag in annotation[field]
                ],
            )


def main() -> None:
    args = parse_args()
    for path in (args.output_database, args.report):
        if path.exists():
            raise FileExistsError(path)
    if args.minimum_products < 1 or args.maximum_products < args.minimum_products:
        raise ValueError("invalid curated catalog product bounds")
    audits = read_passed_audits(args.audits)
    if not args.minimum_products <= len(audits) <= args.maximum_products:
        raise ValueError(
            f"PASS product count {len(audits)} is outside "
            f"[{args.minimum_products}, {args.maximum_products}]"
        )

    source = sqlite3.connect(f"file:{args.source_database}?mode=ro", uri=True)
    product_ids = [str(row["product_id"]) for row in audits]
    source_rows = source_rows_by_product_id(source, product_ids)
    args.output_database.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(args.output_database)
    target.execute("PRAGMA foreign_keys=ON")
    create_output_schema(source, target)
    insert_products(target, source_rows, audits)
    target.executemany(
        "INSERT INTO catalog_metadata VALUES (?, ?)",
        (
            ("annotation_version", ANNOTATION_VERSION),
            ("source_database_sha256", sha256(args.source_database)),
            ("audit_file_sha256", sha256(args.audits)),
        ),
    )
    target.commit()
    integrity = str(target.execute("PRAGMA integrity_check").fetchone()[0])
    product_count = int(target.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    color_count = int(target.execute("SELECT COUNT(*) FROM product_colors").fetchone()[0])
    tag_count = int(target.execute("SELECT COUNT(*) FROM product_visual_tags").fetchone()[0])
    category_counts = dict(
        target.execute(
            """
            SELECT category, COUNT(*)
            FROM product_visual_annotations
            GROUP BY category
            ORDER BY category
            """
        )
    )
    target.close()
    source.close()
    if integrity != "ok":
        raise RuntimeError(f"curated database integrity check failed: {integrity}")
    if product_count != len(audits):
        raise RuntimeError(
            f"curated product count mismatch: {product_count} != {len(audits)}"
        )

    report = {
        "annotation_version": ANNOTATION_VERSION,
        "source_database": str(args.source_database),
        "source_database_sha256": sha256(args.source_database),
        "audits": str(args.audits),
        "audits_sha256": sha256(args.audits),
        "output_database": str(args.output_database),
        "output_database_sha256": sha256(args.output_database),
        "integrity_check": integrity,
        "products": product_count,
        "colors": color_count,
        "visual_tags": tag_count,
        "category_counts": category_counts,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
