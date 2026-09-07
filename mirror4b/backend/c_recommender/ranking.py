from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from backend.product_explanation import build_product_explanation
from backend.schemas import NormalizedFilters, ProductCard
from backend.style_matcher import (
    load_scene_rules,
    match_body_goal,
    match_scene,
    match_style,
    product_style_text,
)


WEIGHTS_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "ranking_weights.yaml"
)

BUSINESS_COLOR_LABELS = {
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
BUSINESS_COLOR_DEPTH_LABELS = {
    "light": "浅色",
    "medium": "中等",
    "dark": "深色",
}
BUSINESS_MATERIAL_LABELS = {
    "cotton": "棉",
    "linen": "亚麻",
    "silk": "真丝",
    "wool": "羊毛",
    "denim": "牛仔",
    "leather": "皮革",
    "chiffon": "雪纺",
    "polyester": "聚酯纤维",
    "lace": "蕾丝",
}
BUSINESS_STYLE_LABELS = {
    "casual": "休闲",
    "formal": "正式",
    "minimalist": "简约",
    "retro": "复古",
    "elegant": "优雅",
    "gentle": "甜美",
    "sporty": "运动",
}
BUSINESS_OCCASION_LABELS = {
    scene: {str(label) for label in rule["business_labels"]}
    for scene, rule in load_scene_rules().items()
}
BUSINESS_ATTRIBUTE_LABELS = {
    "top": "上衣",
    "dress": "连衣裙",
    "skirt": "半身裙",
    "pants": "裤子",
    "outerwear": "夹克",
    "accessory": "配饰",
    "slim": "修身",
    "loose": "宽松",
    "long sleeve": "长袖",
    "short sleeve": "短袖",
    "sleeveless": "无袖",
    "high waist": "高腰",
    "a-line": "A字版",
}


@lru_cache(maxsize=1)
def load_weights() -> dict[str, float]:
    weights = yaml.safe_load(WEIGHTS_PATH.read_text(encoding="utf-8"))
    return {str(key): float(value) for key, value in weights.items()}


def _add_exact_match(
    components: dict[str, float],
    matched: list[str],
    *,
    name: str,
    label: str,
    wanted: str | None,
    actual: str | None,
    weight_name: str,
    weights: dict[str, float],
) -> None:
    if wanted is not None and actual is not None and wanted.casefold() == actual.casefold():
        components[name] = weights[weight_name]
        matched.append(f"{label}：{actual}")


def score_product(
    product: ProductCard,
    filters: NormalizedFilters,
    weights: dict[str, float],
) -> ProductCard:
    components: dict[str, float] = {}
    matched: list[str] = []
    unmatched: list[str] = []

    _add_exact_match(
        components,
        matched,
        name="category",
        label="品类",
        wanted=filters.category,
        actual=product.category,
        weight_name="category_exact",
        weights=weights,
    )
    business_color = (
        BUSINESS_COLOR_LABELS.get(filters.color) if filters.color is not None else None
    )
    if business_color is not None and product.business_colors:
        if business_color in product.business_colors:
            components["color"] = weights["business_color_exact"]
            matched.append(f"业务标注颜色：{business_color}")
    else:
        _add_exact_match(
            components,
            matched,
            name="color",
            label="颜色",
            wanted=filters.color,
            actual=product.color,
            weight_name="color_exact",
            weights=weights,
        )
    if filters.color_depth is not None and product.business_color_depth is not None:
        business_color_depth = BUSINESS_COLOR_DEPTH_LABELS[filters.color_depth]
        if business_color_depth == product.business_color_depth:
            components["color_depth"] = weights["business_color_depth_exact"]
            matched.append(f"业务标注明暗：{business_color_depth}")
    if filters.size is not None and product.business_sizes:
        if filters.size in product.business_sizes:
            components["size"] = weights["business_size_exact"]
            matched.append(f"业务标注尺码：{filters.size}")
    else:
        _add_exact_match(
            components,
            matched,
            name="size",
            label="尺码",
            wanted=filters.size,
            actual=product.size,
            weight_name="size_exact",
            weights=weights,
        )
    if product.season_source != "synthetic_demo":
        _add_exact_match(
            components,
            matched,
            name="season",
            label="季节",
            wanted=filters.season,
            actual=product.season,
            weight_name="season_exact",
            weights=weights,
        )
    _add_exact_match(
        components,
        matched,
        name="brand",
        label="品牌",
        wanted=filters.brand,
        actual=product.brand,
        weight_name="brand_exact",
        weights=weights,
    )
    if filters.material and product.business_materials:
        business_material = BUSINESS_MATERIAL_LABELS[filters.material]
        if business_material in product.business_materials:
            components["material"] = weights["business_material_exact"]
            matched.append(f"业务标注材质：{business_material}")
    elif filters.material and product.material:
        if filters.material.casefold() in product.material.casefold():
            components["material"] = weights["material_exact"]
            matched.append(f"材质：{product.material}")

    if product.stock_quantity > 0:
        components["stock"] = weights["stock_available"]

    if (
        product.parent_asin.startswith("F200K_")
        and product.category_source == "fashion200k_category"
        and product.image_url is not None
    ):
        components["strict_visual_audit"] = weights["strict_visual_audit"]
        matched.append("图片、颜色和品类已通过严格审核")
    if product.price is not None and product.price_source != "synthetic_demo":
        components["original_price"] = weights["original_price"]
    if product.size is not None and product.size_source != "synthetic_demo":
        components["original_size"] = weights["original_size"]
    if product.season is not None and product.season_source != "synthetic_demo":
        components["original_season"] = weights["original_season"]

    for style in filters.style_preferences:
        business_style = BUSINESS_STYLE_LABELS.get(style, style)
        if business_style in product.business_styles:
            components[f"style_{style}"] = weights["business_style_exact"]
            matched.append(f"业务标注风格：{business_style}")
            continue
        label, positive_terms, negative_terms = match_style(product, style)
        if positive_terms:
            components[f"style_{style}"] = weights["style_match"]
            matched.append(f"风格：{label}（依据：{positive_terms[0]}）")
        if negative_terms:
            components[f"style_conflict_{style}"] = weights["style_conflict"]
            unmatched.append(f"风格冲突：{label}（依据：{negative_terms[0]}）")

    for style in filters.excluded_styles:
        label, positive_terms, _ = match_style(product, style)
        if positive_terms:
            components[f"excluded_style_{style}"] = weights["attribute_conflict"]
            unmatched.append(f"排除风格冲突：{label}（依据：{positive_terms[0]}）")

    for body_goal in filters.body_goals:
        label, positive_terms, negative_terms = match_body_goal(product, body_goal)
        if positive_terms:
            components[f"body_goal_{body_goal}"] = weights["body_goal_match"]
            matched.append(f"身材诉求：{label}（版型依据：{positive_terms[0]}）")
        if negative_terms:
            components[f"body_goal_conflict_{body_goal}"] = weights[
                "body_goal_conflict"
            ]
            unmatched.append(f"身材诉求冲突：{label}（版型依据：{negative_terms[0]}）")

    for scene in filters.occasions:
        business_occasions = BUSINESS_OCCASION_LABELS[scene]
        matched_business_occasions = [
            value
            for value in product.business_occasions
            if value in business_occasions
        ]
        if matched_business_occasions:
            components[f"occasion_{scene}"] = weights["business_occasion_exact"]
            matched.append(f"业务标注场景：{matched_business_occasions[0]}")
            continue
        label, positive_terms, negative_terms, preferred_category, blocked_category = (
            match_scene(product, scene)
        )
        if positive_terms:
            components[f"occasion_{scene}"] = weights["occasion_match"]
            matched.append(f"场景：{label}（依据：{positive_terms[0]}）")
        if preferred_category:
            components[f"occasion_category_{scene}"] = weights["occasion_category"]
            matched.append(f"场景品类：{label}")
        if negative_terms or blocked_category:
            components[f"occasion_conflict_{scene}"] = weights["occasion_conflict"]
            evidence = negative_terms[0] if negative_terms else product.category or "unknown"
            unmatched.append(f"场景冲突：{label}（依据：{evidence}）")

    product_text = product_style_text(product)
    for field in (
        "subcategory",
        "gender",
        "age_group",
        "fit",
        "pattern",
        "sleeve_length",
        "garment_length",
        "neckline",
    ):
        value = getattr(filters, field)
        business_values = (
            [product.business_subcategory]
            if field == "subcategory" and product.business_subcategory is not None
            else product.business_fits
            if field == "fit"
            else []
        )
        business_value = BUSINESS_ATTRIBUTE_LABELS.get(value.casefold(), value) if value else None
        if business_value is not None and business_value in business_values:
            components[f"attribute_{field}"] = weights["business_attribute_exact"]
            matched.append(f"业务标注属性：{business_value}")
            continue
        if value and value.casefold() in product_text:
            components[f"attribute_{field}"] = weights["attribute_match"]
            matched.append(f"属性：{value}")

    if product.color and product.color in filters.negative_colors:
        components["negative_soft"] = weights["negative_soft"]
        unmatched.append(f"软负向颜色：{product.color}")

    score = sum(components.values())
    scored = product.model_copy(
        update={
            "score": score,
            "component_scores": components,
            "matched_features": matched,
            "unmatched_features": unmatched,
        }
    )
    return scored.model_copy(
        update={"reason": build_product_explanation(scored, filters)}
    )


def product_passes_relevance_gate(
    product: ProductCard,
    filters: NormalizedFilters,
) -> bool:
    for scene in filters.occasions:
        if f"occasion_conflict_{scene}" in product.component_scores:
            return False
        if f"occasion_{scene}" not in product.component_scores:
            return False
    for style in filters.style_preferences:
        if f"style_conflict_{style}" in product.component_scores:
            return False
        if f"style_{style}" not in product.component_scores:
            return False
    for body_goal in filters.body_goals:
        if f"body_goal_conflict_{body_goal}" in product.component_scores:
            return False
        if f"body_goal_{body_goal}" not in product.component_scores:
            return False
    return not any(
        key.startswith("excluded_style_") for key in product.component_scores
    )


def rank_products(
    products: list[ProductCard],
    filters: NormalizedFilters,
    limit: int,
) -> list[ProductCard]:
    weights = load_weights()
    scored = [score_product(product, filters, weights) for product in products]
    scored.sort(
        key=lambda product: (
            -product.score,
            -product.rating,
            -product.rating_count,
            -product.stock_quantity,
            product.parent_asin,
        )
    )
    return scored[:limit]


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if maximum - minimum < 1e-12:
        return [0.5] * len(values)
    return [(value - minimum) / (maximum - minimum) for value in values]


def rank_products_hybrid(
    products: list[ProductCard],
    filters: NormalizedFilters,
    semantic_scores: list[float],
    limit: int,
    semantic_weight: float = 0.35,
) -> list[ProductCard]:
    if len(products) != len(semantic_scores):
        raise ValueError("products and semantic_scores must have equal length")
    if not 0 <= semantic_weight <= 1:
        raise ValueError("semantic_weight must be between 0 and 1")

    weights = load_weights()
    rule_scored = [score_product(product, filters, weights) for product in products]
    rule_raw = [product.score for product in rule_scored]
    semantic_normalized = _minmax(semantic_scores)
    rule_normalized = _minmax(rule_raw)
    ranked: list[ProductCard] = []
    for product, semantic_raw, semantic_score, rule_score in zip(
        rule_scored,
        semantic_scores,
        semantic_normalized,
        rule_normalized,
    ):
        final_score = semantic_weight * semantic_score + (1 - semantic_weight) * rule_score
        components = dict(product.component_scores)
        components.update(
            {
                "semantic_raw": semantic_raw,
                "semantic_normalized": semantic_score,
                "rule_raw": product.score,
                "rule_normalized": rule_score,
                "hybrid_final": final_score,
            }
        )
        ranked.append(
            product.model_copy(
                update={"score": final_score, "component_scores": components}
            )
        )
    ranked.sort(
        key=lambda product: (
            -product.score,
            -product.rating,
            -product.rating_count,
            product.parent_asin,
        )
    )
    relevant: list[ProductCard] = []
    seen_titles: set[str] = set()
    for product in ranked:
        if not product_passes_relevance_gate(product, filters):
            continue
        title_key = " ".join(product.title.casefold().split())
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        relevant.append(product)
        if len(relevant) == limit:
            break
    return relevant
