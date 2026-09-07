from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.c_recommender.semantic import ProjectedE5Encoder, SemanticIndex


VARIANTS = ("amazon", "fashion200k", "hybrid")
COMPARISON_QUERIES = (
    {
        "id": "light_beach_top",
        "text": "适合夏天海边的浅色吊带上衣",
        "category": "top",
    },
    {
        "id": "red_white_blue_tank",
        "text": "红白蓝配色的美式街头流苏背心",
        "category": "top",
    },
    {
        "id": "retro_black_dress",
        "text": "复古黑色连衣裙",
        "category": "dress",
    },
    {
        "id": "wide_leg_pants",
        "text": "宽松高腰阔腿裤",
        "category": "pants",
    },
    {
        "id": "white_sandals",
        "text": "适合夏天的白色凉鞋",
        "category": "shoes",
    },
    {
        "id": "commute_handbag",
        "text": "简约通勤手提包",
        "category": "bag",
    },
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare isolated catalog data quality.")
    parser.add_argument("--amazon", type=Path, required=True)
    parser.add_argument("--fashion200k", type=Path, required=True)
    parser.add_argument("--hybrid", type=Path, required=True)
    parser.add_argument("--amazon-semantic", type=Path, required=True)
    parser.add_argument("--fashion200k-semantic", type=Path, required=True)
    parser.add_argument("--hybrid-semantic", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def scalar(connection: sqlite3.Connection, query: str) -> int:
    return int(connection.execute(query).fetchone()[0])


def inspect_database(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    total = scalar(connection, "SELECT COUNT(*) FROM products")
    statistics = {
        "products": total,
        "fashion200k_products": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE parent_asin LIKE 'F200K_%'",
        ),
        "amazon_products": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE parent_asin NOT LIKE 'F200K_%'",
        ),
        "distinct_images": scalar(
            connection, "SELECT COUNT(DISTINCT image_url) FROM products"
        ),
        "distinct_titles": scalar(
            connection, "SELECT COUNT(DISTINCT LOWER(TRIM(title))) FROM products"
        ),
        "with_material": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE material IS NOT NULL AND TRIM(material)<>''",
        ),
        "with_features": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE features_text IS NOT NULL AND TRIM(features_text)<>''",
        ),
        "with_description": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE description_text IS NOT NULL AND TRIM(description_text)<>''",
        ),
        "original_price": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE price_source='original'",
        ),
        "original_size": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE size_source='explicit_amazon'",
        ),
        "explicit_season": scalar(
            connection,
            "SELECT COUNT(*) FROM products WHERE season_source='explicit_amazon'",
        ),
        "business_slot_rows": scalar(
            connection, "SELECT COUNT(*) FROM product_business_slots"
        ),
    }
    statistics["coverage"] = {
        key: round(value / total, 6)
        for key, value in statistics.items()
        if key.startswith("with_") or key.startswith("original_") or key == "explicit_season"
    }
    statistics["category_counts"] = dict(
        connection.execute(
            "SELECT category_norm, COUNT(*) FROM products GROUP BY category_norm ORDER BY category_norm"
        )
    )
    statistics["business_slot_coverage"] = dict(
        connection.execute(
            """
            SELECT 'color', SUM(colors_json <> '[]') FROM product_business_slots
            UNION ALL SELECT 'style', SUM(styles_json <> '[]') FROM product_business_slots
            UNION ALL SELECT 'occasion', SUM(occasions_json <> '[]') FROM product_business_slots
            UNION ALL SELECT 'fit', SUM(fits_json <> '[]') FROM product_business_slots
            UNION ALL SELECT 'material', SUM(materials_json <> '[]') FROM product_business_slots
            UNION ALL SELECT 'size', SUM(sizes_json <> '[]') FROM product_business_slots
            """
        )
    )
    connection.close()
    return statistics


def semantic_comparison(
    *,
    database_path: Path,
    semantic_directory: Path,
    projection_path: Path,
    encoder: ProjectedE5Encoder,
) -> dict[str, Any]:
    index = SemanticIndex(
        embeddings_path=semantic_directory / "product_embeddings.npy",
        metadata_path=semantic_directory / "product_embeddings.meta.json",
        product_ids_path=semantic_directory / "product_ids.txt",
        database_path=database_path,
        projection_path=projection_path,
        encoder=encoder,
    )
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query_results: dict[str, Any] = {}
    for query in COMPARISON_QUERIES:
        rows = connection.execute(
            """
            SELECT id - 1 AS embedding_row, parent_asin, title, category_norm,
                   color_norm, image_url
            FROM products
            WHERE category_norm=?
            ORDER BY id
            """,
            (query["category"],),
        ).fetchall()
        embedding_rows = [int(row["embedding_row"]) for row in rows]
        by_embedding_row = {int(row["embedding_row"]): row for row in rows}
        top_rows = index.top_rows(
            raw_query=str(query["text"]),
            filters={"category": query["category"]},
            embedding_rows=embedding_rows,
            limit=12,
        )
        products = [
            {
                "product_id": by_embedding_row[row]["parent_asin"],
                "title": by_embedding_row[row]["title"],
                "category": by_embedding_row[row]["category_norm"],
                "color": by_embedding_row[row]["color_norm"],
                "source": (
                    "fashion200k"
                    if str(by_embedding_row[row]["parent_asin"]).startswith("F200K_")
                    else "amazon"
                ),
                "score": round(score, 6),
            }
            for row, score in top_rows
        ]
        query_results[str(query["id"])] = {
            "query": query["text"],
            "required_category": query["category"],
            "candidate_count": len(rows),
            "result_count": len(products),
            "unique_titles": len({str(product["title"]).casefold() for product in products}),
            "source_counts": {
                source: sum(product["source"] == source for product in products)
                for source in ("amazon", "fashion200k")
            },
            "products": products,
        }
    connection.close()
    return query_results


def main() -> None:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    paths = {
        "amazon": args.amazon,
        "fashion200k": args.fashion200k,
        "hybrid": args.hybrid,
    }
    semantic_paths = {
        "amazon": args.amazon_semantic,
        "fashion200k": args.fashion200k_semantic,
        "hybrid": args.hybrid_semantic,
    }
    encoder = ProjectedE5Encoder(args.base_model, args.projection, "cpu")
    report = {
        "comparison_scope": "data coverage and identical semantic-query retrieval",
        "variants": {
            name: {
                **inspect_database(path),
                "semantic_queries": semantic_comparison(
                    database_path=path,
                    semantic_directory=semantic_paths[name],
                    projection_path=args.projection,
                    encoder=encoder,
                ),
            }
            for name, path in paths.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
