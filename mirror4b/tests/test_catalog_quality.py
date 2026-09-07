import sqlite3
from pathlib import Path

from scripts.audit_amazon_catalog_quality import build_report


def create_test_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            parent_asin TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            category_path TEXT NOT NULL,
            category_norm TEXT NOT NULL,
            image_url TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO products VALUES (?, ?, ?, ?, ?)",
        [
            (
                "A1",
                "Compatible iPhone HDMI Cable Adapter",
                "Clothing > Shirts",
                "top",
                "https://example.com/a1.jpg",
            ),
            (
                "A2",
                "Women's Cable Ribbed Knit Short Sleeve Top",
                "Clothing > Tops",
                "top",
                "https://example.com/a2.jpg",
            ),
            (
                "A3",
                "Compact Mini Sun Umbrella UV Protection",
                "Luggage > Backpacks",
                "bag",
                "https://example.com/a3.jpg",
            ),
            (
                "A4",
                "Umbrella Academy Bi-Fold Wallet",
                "Women > Wallets",
                "bag",
                "https://example.com/a4.jpg",
            ),
            (
                "F200K_1",
                "black lace dress",
                "dresses",
                "dress",
                "/images/1.jpg",
            ),
            (
                "F200K_2",
                "black lace dress",
                "dresses",
                "dress",
                "/images/2.jpg",
            ),
        ],
    )
    connection.commit()
    connection.close()


def test_catalog_quality_audit_is_high_precision_and_read_only(tmp_path: Path) -> None:
    database = tmp_path / "catalog.db"
    create_test_database(database)

    report = build_report(database)

    assert report["amazon_products_scanned"] == 4
    assert report["candidate_count"] == 2
    assert report["action_counts"] == {
        "EXCLUDE_NON_FASHION": 1,
        "RECLASSIFY": 1,
    }
    assert [item["product_id"] for item in report["candidates"]] == ["A1", "A3"]
    assert report["fashion200k"] == {
        "products": 2,
        "distinct_images": 2,
        "duplicate_titles": {
            "groups": 1,
            "duplicate_rows": 1,
            "maximum_group_size": 2,
        },
        "recommended_policy": (
            "Keep all unique images in the catalog and deduplicate normalized titles "
            "only in each displayed recommendation page."
        ),
    }
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 6
    connection.close()
