from __future__ import annotations

import re

from backend.schemas import NormalizedFilters, ProductCard


CATEGORY_LABELS = {
    "top": "上衣",
    "dress": "连衣裙",
    "pants": "长裤",
    "shorts": "短裤",
    "skirt": "半身裙",
    "outerwear": "外套",
    "sweater": "毛衣或卫衣",
    "swimwear": "泳装",
    "underwear": "内衣",
    "shoes": "鞋履",
    "bag": "箱包",
    "jewelry": "首饰",
    "watch": "手表",
    "accessory": "配饰",
    "set": "套装",
    "costume": "服装道具",
}

COLOR_LABELS = {
    "yellow": "黄色",
    "black": "黑色",
    "white": "白色",
    "blue": "蓝色",
    "red": "红色",
    "green": "绿色",
    "pink": "粉色",
    "purple": "紫色",
    "brown": "棕色",
    "gray": "灰色",
    "beige": "米色",
    "orange": "橙色",
    "silver": "银色",
    "gold": "金色",
    "multicolor": "多色",
}

MATERIAL_MARKERS = (
    "cotton",
    "棉",
    "wool",
    "羊毛",
    "羊绒",
    "polyester",
    "聚酯",
    "leather",
    "皮革",
    "真皮",
    "silk",
    "真丝",
    "丝绸",
    "lace",
    "蕾丝",
    "denim",
    "牛仔",
    "linen",
    "亚麻",
)


def _clean_feature_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip(" ;；")
    if not cleaned:
        return None
    return cleaned[:60]


def _credible_material(value: str | None) -> bool:
    if value is None:
        return False
    lowered = value.casefold()
    return any(marker.casefold() in lowered for marker in MATERIAL_MARKERS)


def build_product_explanation(
    product: ProductCard,
    filters: NormalizedFilters,
) -> str:
    facts: list[str] = []
    requirements: list[str] = []

    if product.category:
        category = CATEGORY_LABELS.get(product.category, product.category)
        facts.append(f"商品库记录的品类为{category}")
        if filters.category == product.category:
            requirements.append(category)
    if product.color:
        color = COLOR_LABELS.get(product.color, product.color)
        facts.append(f"颜色为{color}")
        if filters.color == product.color:
            requirements.append(color)
    if filters.brand and product.brand:
        facts.append(f"品牌为{product.brand}")
        requirements.append(product.brand)
    if _credible_material(product.material):
        facts.append(f"材质字段为{product.material}")
        if filters.material and filters.material.casefold() in product.material.casefold():
            requirements.append(product.material)

    feature_text = (
        _clean_feature_text(product.features_text)
        if product.category_source == "fashion200k_category"
        else None
    )
    if feature_text:
        facts.append(f"商品特征记录为{feature_text}")

    style_matches = [
        feature
        for feature in product.matched_features
        if feature.startswith("风格：")
    ]
    if style_matches:
        facts.append(style_matches[0])
        requirements.append(style_matches[0].split("（", maxsplit=1)[0].removeprefix("风格：") + "风格")

    occasion_matches = [
        feature
        for feature in product.matched_features
        if feature.startswith("场景：") or feature.startswith("场景品类：")
    ]
    if occasion_matches:
        facts.append(occasion_matches[0])
        requirements.append(
            occasion_matches[0].split("（", maxsplit=1)[0]
            .removeprefix("场景：")
            .removeprefix("场景品类：")
        )

    body_goal_matches = [
        feature
        for feature in product.matched_features
        if feature.startswith("身材诉求：")
    ]
    if body_goal_matches:
        facts.append(body_goal_matches[0])
        requirements.append(
            body_goal_matches[0].split("（", maxsplit=1)[0].removeprefix("身材诉求：")
        )

    reason = f"推荐「{product.title}」。" + "，".join(facts[:4]) + "。"
    if requirements:
        reason += "这些信息与你提出的" + "、".join(requirements) + "条件相符。"

    synthetic_fields: list[str] = []
    if product.size_source == "synthetic_demo":
        synthetic_fields.append("尺码")
    if product.price_source == "synthetic_demo":
        synthetic_fields.append("价格")
    if product.season_source == "synthetic_demo":
        synthetic_fields.append("季节")
    if product.stock_source == "synthetic_demo":
        synthetic_fields.append("库存")
    if synthetic_fields:
        reason += "其中" + "、".join(synthetic_fields) + "为演示数据，仅用于流程展示。"
    return reason
