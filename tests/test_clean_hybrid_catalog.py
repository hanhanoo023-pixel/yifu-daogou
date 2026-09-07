import json
import sqlite3
from pathlib import Path

from scripts.build_clean_hybrid_catalog import build_clean_catalog


def create_source_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            parent_asin TEXT NOT NULL UNIQUE,
            category_norm TEXT NOT NULL,
            category_source TEXT NOT NULL
        );
        CREATE TABLE product_moderation (
            product_id TEXT PRIMARY KEY,
            audit_version TEXT NOT NULL,
            status TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence REAL NOT NULL,
            audited_at TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(parent_asin)
        );
        CREATE TABLE catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?)",
        [
            (1, "A1", "top", "amazon_category"),
            (2, "A2", "bag", "amazon_category"),
            (3, "A3", "accessory", "amazon_category"),
        ],
    )
    connection.commit()
    connection.close()


def test_builds_clean_copy_without_deleting_or_reordering_products(tmp_path: Path) -> None:
    source = tmp_path / "source.db"
    audit = tmp_path / "audit.json"
    overrides = tmp_path / "overrides.json"
    output = tmp_path / "clean.db"
    decisions = tmp_path / "decisions.jsonl"
    report = tmp_path / "report.json"
    create_source_database(source)
    audit.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "product_id": "A1",
                        "current_category": "top",
                        "proposed_action": "EXCLUDE_NON_FASHION",
                        "proposed_category": None,
                        "rule_id": "electronics",
                        "evidence": "screen protector",
                        "title": "Screen Protector",
                        "category_path": "Clothing > Tops",
                    },
                    {
                        "product_id": "A2",
                        "current_category": "bag",
                        "proposed_action": "RECLASSIFY",
                        "proposed_category": "accessory",
                        "rule_id": "umbrella",
                        "evidence": "umbrella",
                        "title": "Compact Umbrella",
                        "category_path": "Travel > Backpacks",
                    },
                    {
                        "product_id": "A3",
                        "current_category": "accessory",
                        "proposed_action": "EXCLUDE_NON_FASHION",
                        "proposed_category": None,
                        "rule_id": "power_bank",
                        "evidence": "power bank",
                        "title": "Heated Scarf with Power Bank",
                        "category_path": "Women > Scarves",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    overrides.write_text(
        json.dumps(
            {
                "review_version": "test-review-v1",
                "decisions": [
                    {
                        "product_id": "A3",
                        "decision": "KEEP",
                        "reason": "wearable scarf",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = build_clean_catalog(
        source_path=source,
        audit_path=audit,
        overrides_path=overrides,
        output_path=output,
        decisions_path=decisions,
        report_path=report,
    )

    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(output)
    assert source_connection.execute("SELECT COUNT(*) FROM product_moderation").fetchone()[0] == 0
    assert target_connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 3
    assert target_connection.execute(
        "SELECT GROUP_CONCAT(parent_asin, ',') FROM products ORDER BY id"
    ).fetchone()[0] == "A1,A2,A3"
    assert target_connection.execute(
        "SELECT product_id FROM product_moderation"
    ).fetchone()[0] == "A1"
    assert target_connection.execute(
        "SELECT category_norm FROM products WHERE parent_asin='A2'"
    ).fetchone()[0] == "accessory"
    assert result["decision_counts"] == {
        "EXCLUDE_NON_FASHION": 1,
        "KEEP": 1,
        "RECLASSIFY": 1,
    }
    assert result["row_ids_preserved"] is True
    source_connection.close()
    target_connection.close()
