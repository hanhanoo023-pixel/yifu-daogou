from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.build_catalog_variants import build_variant
from scripts.compare_catalog_variants import inspect_database


def make_source(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            parent_asin TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            category_norm TEXT NOT NULL,
            color_raw TEXT NOT NULL,
            material TEXT,
            image_url TEXT NOT NULL,
            rating_count INTEGER NOT NULL,
            features_text TEXT,
            description_text TEXT,
            price_source TEXT NOT NULL,
            size_source TEXT NOT NULL,
            season_source TEXT NOT NULL
        );
        CREATE TABLE product_moderation (
            product_id TEXT PRIMARY KEY,
            audit_version TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence REAL NOT NULL,
            audited_at TEXT NOT NULL
        );
        CREATE INDEX idx_products_category ON products(category_norm);
        INSERT INTO products VALUES
            (1, 'AMZ_1', 'Amazon top', 'top', 'Blue', 'cotton', 'https://img/a1', 20, 'feature', 'description', 'original', 'explicit_amazon', 'explicit_amazon'),
            (2, 'AMZ_2', 'Amazon shoes', 'shoes', 'White', NULL, 'https://img/a2', 10, 'feature', NULL, 'synthetic_demo', 'synthetic_demo', 'synthetic_demo'),
            (3, 'F200K_1', 'black dress', 'dress', 'black', NULL, '/api/fashion200k-images/1.jpg', 0, NULL, NULL, 'synthetic_demo', 'synthetic_demo', 'synthetic_demo'),
            (4, 'F200K_2', 'white top', 'top', 'white', NULL, '/api/fashion200k-images/2.jpg', 0, NULL, NULL, 'synthetic_demo', 'synthetic_demo', 'synthetic_demo'),
            (5, 'F200K_FAIL', 'failed top', 'top', 'red', NULL, '/api/fashion200k-images/fail.jpg', 0, NULL, NULL, 'synthetic_demo', 'synthetic_demo', 'synthetic_demo');
        INSERT INTO product_moderation VALUES
            ('F200K_FAIL', 'test', 'FAIL', 'image mismatch', 0.2, '2026-08-28T00:00:00Z');
        """
    )
    connection.commit()
    return connection


def business_annotation(category: str, subcategory: str, color: str) -> dict[str, object]:
    return {
        "category": category,
        "subcategory": subcategory,
        "color": [color],
        "color_depth": "深色" if color == "黑色" else "浅色",
        "style": ["简约"],
        "occasion": ["日常"],
        "fit": [],
        "material": [],
        "size": ["S", "M"],
    }


def test_builds_three_isolated_catalogs_with_images_and_business_slots(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.db"
    source = make_source(source_path)
    annotations = {
        "F200K_1": business_annotation("裙装", "连衣裙", "黑色"),
        "F200K_2": business_annotation("上衣", "上衣", "白色"),
    }

    reports = {}
    for variant in ("amazon", "fashion200k", "hybrid"):
        reports[variant] = build_variant(
            source=source,
            output_path=tmp_path / f"{variant}.db",
            variant=variant,
            business_annotations=annotations,
            source_database_sha256="source-sha",
            business_annotations_sha256="slots-sha",
        )
    source.close()

    assert reports["amazon"]["products"] == 2
    assert reports["amazon"]["business_slot_rows"] == 0
    assert reports["fashion200k"]["products"] == 2
    assert reports["fashion200k"]["business_slot_rows"] == 2
    assert reports["fashion200k"]["distinct_images"] == 2
    assert reports["hybrid"]["products"] == 4

    fashion = sqlite3.connect(tmp_path / "fashion200k.db")
    description = fashion.execute(
        "SELECT description_text FROM products WHERE parent_asin='F200K_1'"
    ).fetchone()[0]
    assert "颜色=黑色" in description
    assert fashion.execute(
        "SELECT COUNT(*) FROM products WHERE parent_asin='F200K_FAIL'"
    ).fetchone() == (0,)
    fashion.close()

    comparison = inspect_database(tmp_path / "hybrid.db")
    assert comparison["products"] == 4
    assert comparison["business_slot_rows"] == 2
    assert comparison["distinct_images"] == 4
