from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from scripts.build_curated_product_database import (
    create_output_schema,
    insert_products,
    source_rows_by_product_id,
)
from scripts.select_visual_candidates import select_candidates


def product(
    product_id: str,
    *,
    category: str,
    color: str,
    image_url: str,
    score_text: bool = True,
) -> dict[str, object]:
    return {
        "id": int(product_id.rsplit("_", 1)[-1]),
        "parent_asin": product_id,
        "title": f"{color} {category} product {product_id}",
        "brand": "brand",
        "category_path": category,
        "category_norm": category,
        "department": "women",
        "color_raw": color,
        "color_norm": color,
        "size_raw": "M",
        "size_norm": "M",
        "material": "cotton" if score_text else None,
        "season": "summer",
        "price": 20.0,
        "image_url": image_url,
        "rating": 4.5,
        "rating_count": 10,
        "stock_quantity": 5,
        "features_text": "feature" if score_text else None,
        "description_text": "description" if score_text else None,
        "price_source": "original",
        "size_source": "explicit_amazon",
        "color_source": "explicit_amazon",
        "season_source": "synthetic_demo",
        "category_source": "amazon_category",
        "brand_source": "original",
        "stock_source": "synthetic_demo",
    }


def test_candidate_selection_deduplicates_images_and_balances_colors() -> None:
    config = {
        "candidate_quotas": {"top": 3},
        "selection": {
            "require_image": True,
            "deduplicate_exact_image_url": True,
            "balance_by_color": True,
            "minimum_title_characters": 4,
        },
        "quality_score": {
            "audited_fashion200k": 8,
            "business_slots_available": 3,
            "features_text": 3,
            "description_text": 3,
            "material": 2,
            "rating_evidence": 2,
            "original_price": 1,
            "original_size": 1,
        },
    }
    rows = [
        product("ASIN_1", category="top", color="black", image_url="https://img/1"),
        product(
            "ASIN_2",
            category="top",
            color="white",
            image_url="https://img/1",
            score_text=False,
        ),
        product("ASIN_3", category="top", color="white", image_url="https://img/3"),
        product("ASIN_4", category="top", color="blue", image_url="https://img/4"),
        product("ASIN_5", category="top", color="black", image_url="https://img/5"),
    ]

    selected, statistics = select_candidates(rows, config, {})

    assert len(selected) == 3
    assert {row["color_norm"] for row in selected} == {"black", "white", "blue"}
    assert statistics["exact_image_duplicates_removed"] == 1
    assert "ASIN_2" not in {row["parent_asin"] for row in selected}


def create_source_database(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            parent_asin TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            image_url TEXT NOT NULL
        );
        CREATE TABLE product_moderation (
            product_id TEXT PRIMARY KEY,
            audit_version TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence REAL NOT NULL,
            audited_at TEXT NOT NULL
        );
        CREATE INDEX idx_products_title ON products(title);
        INSERT INTO products VALUES (7, 'ASIN_7', 'Test top', 'https://img/7');
        """
    )
    connection.commit()
    return connection


def passed_audit() -> dict[str, object]:
    return {
        "product_id": "ASIN_7",
        "status": "PASS",
        "audit_rounds": [{"round": 1}],
        "final_annotation": {
            "category": {"value": "top", "confidence": 0.99, "evidence": "上衣"},
            "subcategory": {
                "value": "tank_top",
                "confidence": 0.98,
                "evidence": "无袖",
            },
            "colors": [
                {
                    "value": "white",
                    "role": "base",
                    "coverage": "dominant",
                    "confidence": 0.97,
                    "evidence": "衣身为白色",
                }
            ],
            "color_depth": {
                "value": "light",
                "confidence": 0.96,
                "evidence": "整体明亮",
            },
            "patterns": [],
            "styles": [
                {"value": "casual", "confidence": 0.9, "evidence": "休闲轮廓"}
            ],
            "fits": [],
            "design_details": [],
            "materials": [],
            "occasions": [],
            "seasons": [],
            "overall_confidence": 0.94,
            "visual_description": "白色（底色），休闲，背心",
        },
    }


def test_curated_database_contains_only_passed_products_and_visual_evidence(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.db"
    source = create_source_database(source_path)
    source.row_factory = sqlite3.Row
    target_path = tmp_path / "curated.db"
    target = sqlite3.connect(target_path)
    target.execute("PRAGMA foreign_keys=ON")
    create_output_schema(source, target)
    audit = passed_audit()
    rows = source_rows_by_product_id(source, ["ASIN_7"])
    insert_products(target, rows, [audit])
    target.commit()

    assert target.execute("SELECT id, parent_asin FROM products").fetchone() == (
        1,
        "ASIN_7",
    )
    assert target.execute(
        "SELECT color_norm, role, coverage FROM product_colors"
    ).fetchone() == ("white", "base", "dominant")
    assert target.execute(
        "SELECT tag_type, tag_value FROM product_visual_tags"
    ).fetchone() == ("style", "casual")
    assert target.execute(
        "SELECT visual_description FROM product_visual_annotations"
    ).fetchone() == ("白色（底色），休闲，背心",)
    source.close()
    target.close()
