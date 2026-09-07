from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.currency import to_catalog_price
from backend.schemas import FilterStage, NormalizedFilters, ProductCard


DATABASE_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "experiments"
    / "catalog_hybrid_clean_v1.db"
)
PRODUCT_FROM_CLAUSE = """
FROM products
LEFT JOIN product_business_slots AS business
  ON business.product_id = products.parent_asin
"""
BUSINESS_COLOR_VALUES = {
    "black": "黑色",
    "white": "白色",
    "gray": "灰色",
    "blue": "蓝色",
    "red": "红色",
    "pink": "粉色",
    "green": "绿色",
    "yellow": "黄色",
    "purple": "紫色",
    "brown": "棕色",
    "orange": "橙色",
    "multicolor": "多色",
}
BUSINESS_COLOR_DEPTH_VALUES = {
    "light": "浅色",
    "medium": "中等",
    "dark": "深色",
}
BUSINESS_MATERIAL_VALUES = {
    "cotton": "棉",
    "linen": "亚麻",
    "silk": "真丝",
    "wool": "羊毛",
    "denim": "牛仔",
    "leather": "皮革",
    "polyester": "聚酯纤维",
    "lace": "蕾丝",
}
ACTIVE_PRODUCT_CLAUSE = """
NOT EXISTS (
    SELECT 1
    FROM product_moderation
    WHERE product_moderation.product_id = products.parent_asin
      AND product_moderation.status = 'FAIL'
)
"""

PRODUCT_SCOPE_CATEGORIES = {
    "clothing": ["top", "dress", "pants", "shorts", "skirt", "outerwear", "sweater", "swimwear", "underwear", "set"],
    "footwear": ["shoes"],
    "bags": ["bag"],
    "jewelry": ["jewelry", "watch"],
    "accessories": ["accessory"],
    "all": [],
}

LIGHT_COLOR_CLAUSE = """
(
    (
        color_norm IN ('white', 'beige', 'pink', 'yellow', 'silver')
        OR LOWER(color_raw) LIKE '%light%'
        OR LOWER(color_raw) LIKE '%pale%'
        OR LOWER(color_raw) LIKE '%pastel%'
        OR LOWER(color_raw) LIKE '%ivory%'
        OR LOWER(color_raw) LIKE '%cream%'
        OR LOWER(color_raw) LIKE '%mint%'
        OR LOWER(color_raw) LIKE '%sky%'
    )
    AND LOWER(color_raw) NOT LIKE '%black%'
    AND LOWER(color_raw) NOT LIKE '%charcoal%'
    AND LOWER(color_raw) NOT LIKE '%navy%'
    AND LOWER(color_raw) NOT LIKE '%dark%'
    AND LOWER(color_raw) NOT LIKE '%brown%'
    AND LOWER(color_raw) NOT LIKE '%burgundy%'
    AND LOWER(color_raw) NOT LIKE '%maroon%'
)
"""

DARK_COLOR_CLAUSE = """
(
    color_norm IN ('black', 'brown', 'gray')
    OR LOWER(color_raw) LIKE '%black%'
    OR LOWER(color_raw) LIKE '%charcoal%'
    OR LOWER(color_raw) LIKE '%navy%'
    OR LOWER(color_raw) LIKE '%dark%'
    OR LOWER(color_raw) LIKE '%brown%'
    OR LOWER(color_raw) LIKE '%burgundy%'
    OR LOWER(color_raw) LIKE '%maroon%'
)
"""


def color_depth_clause(color_depth: str) -> str:
    if color_depth == "light":
        return LIGHT_COLOR_CLAUSE
    if color_depth == "dark":
        return DARK_COLOR_CLAUSE
    if color_depth == "medium":
        return f"(NOT {LIGHT_COLOR_CLAUSE} AND NOT {DARK_COLOR_CLAUSE})"
    raise ValueError(f"unsupported color depth: {color_depth}")

def connect_read_only() -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def database_product_count() -> int:
    connection = connect_read_only()
    count = connection.execute(
        "SELECT COUNT(*) FROM products WHERE " + ACTIVE_PRODUCT_CLAUSE
    ).fetchone()[0]
    connection.close()
    return count


def _where_clause(clauses: list[str]) -> str:
    if not clauses:
        return ""
    return " WHERE " + " AND ".join(clauses)


def _count(
    connection: sqlite3.Connection,
    clauses: list[str],
    parameters: list[object],
) -> int:
    return connection.execute(
        "SELECT COUNT(*) " + PRODUCT_FROM_CLAUSE + _where_clause(clauses),
        parameters,
    ).fetchone()[0]


def _placeholders(values: list[str]) -> str:
    if not values:
        return ""
    return ", ".join("?" for _ in values)


def _business_json_match_clause(column: str) -> str:
    return (
        f"EXISTS (SELECT 1 FROM json_each(business.{column}) "
        "WHERE json_each.value = ?)"
    )


def _product_from_row(row: sqlite3.Row) -> ProductCard:
    values = dict(row)
    for source, target in (
        ("business_colors_json", "business_colors"),
        ("business_styles_json", "business_styles"),
        ("business_occasions_json", "business_occasions"),
        ("business_fits_json", "business_fits"),
        ("business_materials_json", "business_materials"),
        ("business_sizes_json", "business_sizes"),
    ):
        raw_value = values.pop(source)
        values[target] = json.loads(raw_value) if raw_value is not None else []
    return ProductCard.model_validate(values)


PRODUCT_SELECT = """
SELECT
    products.id - 1 AS embedding_row,
    products.parent_asin,
    products.title,
    products.brand,
    products.category_norm AS category,
    products.color_norm AS color,
    products.size_norm AS size,
    products.season,
    products.material,
    products.price,
    products.image_url,
    products.rating,
    products.rating_count,
    products.stock_quantity,
    products.price_source,
    products.size_source,
    products.color_source,
    products.season_source,
    products.stock_source,
    products.category_source,
    business.annotation_version AS business_annotation_version,
    business.category AS business_category,
    business.subcategory AS business_subcategory,
    business.colors_json AS business_colors_json,
    business.color_depth AS business_color_depth,
    business.styles_json AS business_styles_json,
    business.occasions_json AS business_occasions_json,
    business.fits_json AS business_fits_json,
    business.materials_json AS business_materials_json,
    business.sizes_json AS business_sizes_json,
    products.features_text,
    products.description_text
""" + PRODUCT_FROM_CLAUSE


def query_products(
    filters: NormalizedFilters,
    only_in_stock: bool,
    candidate_limit: int,
) -> tuple[int, list[ProductCard], list[FilterStage]]:
    connection = connect_read_only()
    clauses: list[str] = [ACTIVE_PRODUCT_CLAUSE]
    parameters: list[object] = []
    stages: list[FilterStage] = []

    def add_stage(
        name: str,
        value: str | float | bool | list[str],
        clause: str,
        clause_parameters: list[object],
    ) -> None:
        clauses.append(clause)
        parameters.extend(clause_parameters)
        stages.append(
            FilterStage(
                name=name,
                value=value,
                count=_count(connection, clauses, parameters),
            )
        )

    exact_filters = (
        ("category", filters.category, "category_norm"),
        ("size", filters.size, "size_norm"),
    )
    for name, value, column in exact_filters:
        if value is not None:
            add_stage(name, value, f"{column} = ?", [value])

    if filters.color is not None:
        business_color = BUSINESS_COLOR_VALUES.get(filters.color)
        if business_color is None:
            add_stage("color", filters.color, "color_norm = ?", [filters.color])
        else:
            business_clause = (
                "(json_array_length(business.colors_json) > 1)"
                if filters.color == "multicolor"
                else _business_json_match_clause("colors_json")
            )
            business_parameters = (
                [] if filters.color == "multicolor" else [business_color]
            )
            add_stage(
                "color",
                filters.color,
                "((business.product_id IS NOT NULL AND "
                + business_clause
                + ") OR (business.product_id IS NULL AND color_norm = ?))",
                [*business_parameters, filters.color],
            )

    if filters.color_depth is not None:
        add_stage(
            "color_depth",
            filters.color_depth,
            "((business.product_id IS NOT NULL AND business.color_depth = ?) "
            "OR (business.product_id IS NULL AND "
            + color_depth_clause(filters.color_depth)
            + "))",
            [BUSINESS_COLOR_DEPTH_VALUES[filters.color_depth]],
        )

    if filters.product_scope and filters.product_scope != "all":
        scope_categories = PRODUCT_SCOPE_CATEGORIES[filters.product_scope]
        add_stage(
            "product_scope",
            filters.product_scope,
            f"category_norm IN ({_placeholders(scope_categories)})",
            scope_categories,
        )

    if filters.season is not None:
        add_stage(
            "season",
            filters.season,
            "(season_source = 'synthetic_demo' OR season = ?)",
            [filters.season],
        )

    if filters.min_price is not None:
        add_stage(
            "min_price",
            filters.min_price,
            "price >= ?",
            [to_catalog_price(filters.min_price, filters.price_currency)],
        )
    if filters.max_price is not None:
        add_stage(
            "max_price",
            filters.max_price,
            "price <= ?",
            [to_catalog_price(filters.max_price, filters.price_currency)],
        )
    if filters.min_rating is not None:
        add_stage(
            "min_rating",
            filters.min_rating,
            "rating >= ?",
            [filters.min_rating],
        )
    if filters.brand is not None:
        add_stage(
            "brand",
            filters.brand,
            "LOWER(COALESCE(brand, '')) = ?",
            [filters.brand.casefold()],
        )
    if filters.material is not None:
        add_stage(
            "material",
            filters.material,
            "((business.product_id IS NOT NULL AND "
            + _business_json_match_clause("materials_json")
            + ") OR (business.product_id IS NULL "
            "AND LOWER(COALESCE(material, '')) LIKE ?))",
            [
                BUSINESS_MATERIAL_VALUES[filters.material],
                f"%{filters.material.casefold()}%",
            ],
        )
    if filters.excluded_colors:
        for color in filters.excluded_colors:
            business_color = BUSINESS_COLOR_VALUES.get(color)
            if business_color is None:
                add_stage(
                    "excluded_colors",
                    filters.excluded_colors,
                    "color_norm <> ?",
                    [color],
                )
            else:
                add_stage(
                    "excluded_colors",
                    filters.excluded_colors,
                    "((business.product_id IS NOT NULL AND NOT "
                    + _business_json_match_clause("colors_json")
                    + ") OR (business.product_id IS NULL AND color_norm <> ?))",
                    [business_color, color],
                )
    if filters.excluded_categories:
        placeholders = _placeholders(filters.excluded_categories)
        add_stage(
            "excluded_categories",
            filters.excluded_categories,
            f"category_norm NOT IN ({placeholders})",
            list(filters.excluded_categories),
        )
    if filters.excluded_materials:
        for material in filters.excluded_materials:
            add_stage(
                "excluded_materials",
                filters.excluded_materials,
                "((business.product_id IS NOT NULL AND NOT "
                + _business_json_match_clause("materials_json")
                + ") OR (business.product_id IS NULL "
                "AND LOWER(COALESCE(material, '')) NOT LIKE ?))",
                [
                    BUSINESS_MATERIAL_VALUES[material],
                    f"%{material.casefold()}%",
                ],
            )
    if filters.excluded_brands:
        placeholders = _placeholders(filters.excluded_brands)
        add_stage(
            "excluded_brands",
            filters.excluded_brands,
            f"LOWER(COALESCE(brand, '')) NOT IN ({placeholders})",
            [brand.casefold() for brand in filters.excluded_brands],
        )
    if filters.excluded_product_ids:
        placeholders = _placeholders(filters.excluded_product_ids)
        add_stage(
            "excluded_product_ids",
            filters.excluded_product_ids,
            f"parent_asin NOT IN ({placeholders})",
            list(filters.excluded_product_ids),
        )

    if only_in_stock:
        add_stage("only_in_stock", True, "stock_quantity > ?", [0])

    total_matches = _count(connection, clauses, parameters)
    where = _where_clause(clauses)

    product_select = PRODUCT_SELECT
    audited_limit = min(candidate_limit, max(50, candidate_limit // 4))
    audited_rows = connection.execute(
        product_select
        + where
        + " AND parent_asin LIKE 'F200K_%'"
        + " AND category_source = 'fashion200k_category'"
        + " ORDER BY rating DESC, rating_count DESC, stock_quantity DESC, parent_asin ASC LIMIT ?",
        [*parameters, audited_limit],
    ).fetchall()
    general_rows = connection.execute(
        product_select
        + where
        + " ORDER BY rating DESC, rating_count DESC, stock_quantity DESC, parent_asin ASC LIMIT ?",
        [*parameters, candidate_limit],
    ).fetchall()
    connection.close()
    rows_by_id = {
        row["parent_asin"]: row for row in [*audited_rows, *general_rows]
    }
    products = [_product_from_row(row) for row in rows_by_id.values()]
    return total_matches, products, stages


def query_embedding_rows(
    filters: NormalizedFilters,
    only_in_stock: bool,
) -> list[int]:
    clauses: list[str] = [ACTIVE_PRODUCT_CLAUSE]
    parameters: list[object] = []

    for value, column in (
        (filters.category, "category_norm"),
        (filters.size, "size_norm"),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    if filters.color is not None:
        business_color = BUSINESS_COLOR_VALUES.get(filters.color)
        if business_color is None:
            clauses.append("color_norm = ?")
            parameters.append(filters.color)
        else:
            if filters.color == "multicolor":
                business_clause = "json_array_length(business.colors_json) > 1"
                business_parameters: list[object] = []
            else:
                business_clause = _business_json_match_clause("colors_json")
                business_parameters = [business_color]
            clauses.append(
                "((business.product_id IS NOT NULL AND "
                + business_clause
                + ") OR (business.product_id IS NULL AND color_norm = ?))"
            )
            parameters.extend([*business_parameters, filters.color])
    if filters.color_depth is not None:
        clauses.append(
            "((business.product_id IS NOT NULL AND business.color_depth = ?) "
            "OR (business.product_id IS NULL AND "
            + color_depth_clause(filters.color_depth)
            + "))"
        )
        parameters.append(BUSINESS_COLOR_DEPTH_VALUES[filters.color_depth])
    if filters.product_scope and filters.product_scope != "all":
        scope_categories = PRODUCT_SCOPE_CATEGORIES[filters.product_scope]
        clauses.append(f"category_norm IN ({_placeholders(scope_categories)})")
        parameters.extend(scope_categories)
    if filters.season is not None:
        clauses.append("(season_source = 'synthetic_demo' OR season = ?)")
        parameters.append(filters.season)
    if filters.min_price is not None:
        clauses.append("price >= ?")
        parameters.append(to_catalog_price(filters.min_price, filters.price_currency))
    if filters.max_price is not None:
        clauses.append("price <= ?")
        parameters.append(to_catalog_price(filters.max_price, filters.price_currency))
    if filters.min_rating is not None:
        clauses.append("rating >= ?")
        parameters.append(filters.min_rating)
    if filters.brand is not None:
        clauses.append("LOWER(COALESCE(brand, '')) = ?")
        parameters.append(filters.brand.casefold())
    if filters.material is not None:
        clauses.append(
            "((business.product_id IS NOT NULL AND "
            + _business_json_match_clause("materials_json")
            + ") OR (business.product_id IS NULL "
            "AND LOWER(COALESCE(material, '')) LIKE ?))"
        )
        parameters.extend(
            [
                BUSINESS_MATERIAL_VALUES[filters.material],
                f"%{filters.material.casefold()}%",
            ]
        )
    for values, column in (
        (filters.excluded_categories, "category_norm"),
        (filters.excluded_product_ids, "parent_asin"),
    ):
        if values:
            clauses.append(f"{column} NOT IN ({_placeholders(values)})")
            parameters.extend(values)
    for color in filters.excluded_colors:
        business_color = BUSINESS_COLOR_VALUES.get(color)
        if business_color is None:
            clauses.append("color_norm <> ?")
            parameters.append(color)
        else:
            clauses.append(
                "((business.product_id IS NOT NULL AND NOT "
                + _business_json_match_clause("colors_json")
                + ") OR (business.product_id IS NULL AND color_norm <> ?))"
            )
            parameters.extend([business_color, color])
    if filters.excluded_brands:
        clauses.append(
            "LOWER(COALESCE(brand, '')) NOT IN "
            f"({_placeholders(filters.excluded_brands)})"
        )
        parameters.extend(value.casefold() for value in filters.excluded_brands)
    if filters.excluded_materials:
        for material in filters.excluded_materials:
            clauses.append(
                "((business.product_id IS NOT NULL AND NOT "
                + _business_json_match_clause("materials_json")
                + ") OR (business.product_id IS NULL "
                "AND LOWER(COALESCE(material, '')) NOT LIKE ?))"
            )
            parameters.extend(
                [
                    BUSINESS_MATERIAL_VALUES[material],
                    f"%{material.casefold()}%",
                ]
            )
    if only_in_stock:
        clauses.append("stock_quantity > ?")
        parameters.append(0)

    connection = connect_read_only()
    rows = connection.execute(
        "SELECT products.id - 1 " + PRODUCT_FROM_CLAUSE + _where_clause(clauses),
        parameters,
    ).fetchall()
    connection.close()
    return [int(row[0]) for row in rows]


def get_products_by_embedding_rows(embedding_rows: list[int]) -> list[ProductCard]:
    if not embedding_rows:
        return []
    connection = connect_read_only()
    rows = connection.execute(
        PRODUCT_SELECT
        + " WHERE products.id - 1 IN ("
        + _placeholders([str(value) for value in embedding_rows])
        + ") AND "
        + ACTIVE_PRODUCT_CLAUSE,
        embedding_rows,
    ).fetchall()
    connection.close()
    by_row = {
        int(row["embedding_row"]): _product_from_row(row)
        for row in rows
    }
    return [by_row[row] for row in embedding_rows]


def get_product_by_id(product_id: str) -> ProductCard | None:
    connection = connect_read_only()
    row = connection.execute(
        PRODUCT_SELECT
        + " WHERE products.parent_asin = ? AND "
        + ACTIVE_PRODUCT_CLAUSE
        + " LIMIT 1",
        [product_id],
    ).fetchone()
    connection.close()
    return _product_from_row(row) if row is not None else None
