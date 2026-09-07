from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from backend.c_recommender import (
    ProjectedE5Encoder,
    SemanticIndex,
    rank_products_hybrid,
)
from backend.currency import load_currency_config
from backend.database import (
    DATABASE_PATH,
    get_products_by_embedding_rows,
    query_embedding_rows,
    query_products,
)
from backend.safety_guard import apply_safety_guard
from backend.schemas import (
    FilterStage,
    IntentResult,
    NormalizedFilters,
    RecommendationRequest,
    RecommendationResponse,
)
from backend.style_matcher import load_scene_rules


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_MODEL_PATH = PROJECT_ROOT / "models" / "c_recommender" / "multilingual-e5-small"
PROJECTION_PATH = PROJECT_ROOT / "models" / "c_recommender" / "public_semantic_two_tower.pt"
EMBEDDINGS_PATH = (
    PROJECT_ROOT
    / "data"
    / "experiments"
    / "semantic_hybrid_clean_v1"
    / "product_embeddings.npy"
)
PRODUCT_IDS_PATH = (
    PROJECT_ROOT
    / "data"
    / "experiments"
    / "semantic_hybrid_clean_v1"
    / "product_ids.txt"
)
EMBEDDINGS_METADATA_PATH = (
    PROJECT_ROOT
    / "data"
    / "experiments"
    / "semantic_hybrid_clean_v1"
    / "product_embeddings.meta.json"
)


@lru_cache(maxsize=1)
def get_semantic_index() -> SemanticIndex:
    encoder = ProjectedE5Encoder(
        base_model_path=BASE_MODEL_PATH,
        projection_path=PROJECTION_PATH,
        device="cpu",
    )
    return SemanticIndex(
        embeddings_path=EMBEDDINGS_PATH,
        metadata_path=EMBEDDINGS_METADATA_PATH,
        product_ids_path=PRODUCT_IDS_PATH,
        database_path=DATABASE_PATH,
        projection_path=PROJECTION_PATH,
        encoder=encoder,
    )


CATEGORY_ALIASES = {
    "top": {"top", "tops", "上衣", "衬衫", "t恤", "t-shirt", "shirt", "blouse", "tee"},
    "dress": {"dress", "dresses", "连衣裙", "裙装"},
    "pants": {"pants", "trousers", "jeans", "裤子", "长裤", "牛仔裤"},
    "shorts": {"shorts", "短裤"},
    "skirt": {"skirt", "skirts", "半身裙"},
    "outerwear": {"outerwear", "coat", "jacket", "外套", "夹克", "大衣"},
    "sweater": {"sweater", "hoodie", "sweatshirt", "毛衣", "卫衣"},
    "swimwear": {"swimwear", "swimsuit", "泳装", "泳衣"},
    "underwear": {"underwear", "lingerie", "内衣"},
    "shoes": {"shoes", "shoe", "鞋", "鞋子"},
    "bag": {"bag", "bags", "包", "包包"},
    "jewelry": {"jewelry", "首饰", "珠宝"},
    "watch": {"watch", "watches", "手表"},
    "accessory": {"accessory", "accessories", "配饰"},
    "set": {"set", "sets", "套装"},
    "costume": {"costume", "cosplay", "服装道具"},
}

COLOR_ALIASES = {
    "yellow": {"yellow", "黄色", "黄", "姜黄", "柠檬黄"},
    "black": {"black", "黑色", "黑"},
    "white": {"white", "白色", "白"},
    "blue": {"blue", "navy", "蓝色", "蓝", "藏青"},
    "red": {"red", "红色", "红"},
    "green": {"green", "绿色", "绿"},
    "pink": {"pink", "粉色", "粉"},
    "purple": {"purple", "紫色", "紫"},
    "brown": {"brown", "棕色", "棕", "咖色"},
    "gray": {"gray", "grey", "灰色", "灰"},
    "beige": {"beige", "米色", "卡其色", "卡其"},
    "orange": {"orange", "橙色", "橙"},
    "silver": {"silver", "银色", "银"},
    "gold": {"gold", "金色", "金"},
    "multicolor": {"multicolor", "彩色", "多色"},
}

SEASON_ALIASES = {
    "summer": {"summer", "夏季", "夏天"},
    "winter": {"winter", "冬季", "冬天"},
    "spring": {"spring", "春季", "春天"},
    "autumn": {"autumn", "fall", "秋季", "秋天"},
}

SIZE_ALIASES = {
    "XS": {"xs", "x-small", "extra small"},
    "S": {"s", "small"},
    "M": {"m", "medium"},
    "L": {"l", "large"},
    "XL": {"xl", "x-large", "extra large"},
    "2XL": {"2xl", "xxl", "xx-large"},
    "3XL": {"3xl", "xxxl", "xxx-large"},
    "4XL": {"4xl", "xxxxl", "xxxx-large"},
}

MATERIAL_ALIASES = {
    "cotton": {"cotton", "棉", "棉质", "纯棉"},
    "wool": {"wool", "羊毛", "羊绒"},
    "polyester": {"polyester", "聚酯", "聚酯纤维"},
    "leather": {"leather", "皮革", "真皮"},
    "silk": {"silk", "真丝", "丝绸"},
    "lace": {"lace", "蕾丝"},
    "denim": {"denim", "牛仔"},
    "linen": {"linen", "亚麻"},
}

STYLE_ALIASES = {
    "gentle": {"gentle", "温柔", "甜美", "浪漫"},
    "casual": {"casual", "休闲", "日常"},
    "minimalist": {"minimalist", "minimal", "简约", "极简"},
    "formal": {"formal", "正式", "商务"},
    "sporty": {"sporty", "sport", "运动"},
    "retro": {"retro", "vintage", "复古"},
    "streetwear": {"streetwear", "street", "街头"},
    "elegant": {"elegant", "优雅"},
}

BODY_GOAL_ALIASES = {
    "elongate": {"elongate", "显高", "拉长比例", "拉长身形"},
    "streamline": {"streamline", "显瘦", "修饰身形", "线条利落"},
    "tummy_coverage": {"tummy_coverage", "遮腹", "遮肚子", "遮小肚子"},
    "shoulder_balance": {"shoulder_balance", "肩宽", "修饰肩部", "平衡肩部"},
    "leg_balance": {"leg_balance", "腿型", "修饰腿型", "腿不直"},
}

OCCASION_ALIASES = {
    scene: {
        scene,
        *(str(alias).strip().casefold() for alias in rule["aliases"]),
    }
    for scene, rule in load_scene_rules().items()
}


def normalize_alias(value: str | None, aliases: dict[str, set[str]]) -> str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    for normalized, values in aliases.items():
        if lowered in values:
            return normalized
    return lowered


def normalize_size(value: str | None) -> str | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    for normalized, values in SIZE_ALIASES.items():
        if lowered in values:
            return normalized
    return value.strip().upper()


def normalize_values(values: list[str], aliases: dict[str, set[str]]) -> list[str]:
    normalized = [normalize_alias(value, aliases) for value in values]
    return list(dict.fromkeys(value for value in normalized if value is not None))


def normalize_request(request: RecommendationRequest) -> NormalizedFilters:
    normalized_occasions = normalize_values(request.occasions, OCCASION_ALIASES)
    filters = NormalizedFilters(
        category=normalize_alias(request.category, CATEGORY_ALIASES),
        product_scope=request.product_scope.casefold() if request.product_scope else None,
        subcategory=request.subcategory.casefold() if request.subcategory else None,
        gender=request.gender.casefold() if request.gender else None,
        age_group=request.age_group.casefold() if request.age_group else None,
        color=normalize_alias(request.color, COLOR_ALIASES),
        color_depth=request.color_depth,
        size=normalize_size(request.size),
        season=normalize_alias(request.season, SEASON_ALIASES),
        min_price=request.min_price,
        max_price=request.max_price,
        min_rating=request.min_rating,
        price_currency=request.price_currency,
        brand=request.brand.strip() if request.brand else None,
        material=normalize_alias(request.material, MATERIAL_ALIASES),
        fit=request.fit.casefold() if request.fit else None,
        pattern=request.pattern.casefold() if request.pattern else None,
        sleeve_length=request.sleeve_length.casefold() if request.sleeve_length else None,
        garment_length=request.garment_length.casefold() if request.garment_length else None,
        neckline=request.neckline.casefold() if request.neckline else None,
        occasions=[
            value for value in normalized_occasions if value in OCCASION_ALIASES
        ],
        style_preferences=normalize_values(
            request.style_preferences,
            STYLE_ALIASES,
        ),
        body_goals=normalize_values(request.body_goals, BODY_GOAL_ALIASES),
        weather=list(dict.fromkeys(value.casefold() for value in request.weather)),
        excluded_categories=normalize_values(
            request.excluded_categories,
            CATEGORY_ALIASES,
        ),
        excluded_colors=normalize_values(request.excluded_colors, COLOR_ALIASES),
        excluded_materials=normalize_values(request.excluded_materials, MATERIAL_ALIASES),
        excluded_brands=list(dict.fromkeys(request.excluded_brands)),
        excluded_styles=normalize_values(request.excluded_styles, STYLE_ALIASES),
        negative_colors=normalize_values(request.negative_colors, COLOR_ALIASES),
        excluded_product_ids=list(dict.fromkeys(request.excluded_product_ids)),
    )
    scalar_values = {
        "category": (filters.category, set(CATEGORY_ALIASES)),
        "product_scope": (
            filters.product_scope,
            {"clothing", "footwear", "bags", "jewelry", "accessories", "all"},
        ),
        "color": (filters.color, set(COLOR_ALIASES)),
        "season": (filters.season, set(SEASON_ALIASES)),
        "material": (filters.material, set(MATERIAL_ALIASES)),
    }
    for field, (value, allowed) in scalar_values.items():
        if value is not None and value not in allowed:
            raise ValueError(f"unsupported {field}: {value}")
    list_values = {
        "occasions": (filters.occasions, set(OCCASION_ALIASES)),
        "style_preferences": (filters.style_preferences, set(STYLE_ALIASES)),
        "body_goals": (filters.body_goals, set(BODY_GOAL_ALIASES)),
        "excluded_categories": (filters.excluded_categories, set(CATEGORY_ALIASES)),
        "excluded_colors": (filters.excluded_colors, set(COLOR_ALIASES)),
        "excluded_materials": (filters.excluded_materials, set(MATERIAL_ALIASES)),
        "excluded_styles": (filters.excluded_styles, set(STYLE_ALIASES)),
        "negative_colors": (filters.negative_colors, set(COLOR_ALIASES)),
    }
    for field, (values, allowed) in list_values.items():
        unsupported = [value for value in values if value not in allowed]
        if unsupported:
            raise ValueError(f"unsupported {field}: {unsupported}")
    return filters


def no_result_message(filters: NormalizedFilters, stages: list[FilterStage]) -> str:
    values = {
        "category": filters.category,
        "color": filters.color,
        "color_depth": filters.color_depth,
        "size": filters.size,
        "season": filters.season,
    }
    labels = {
        "category": "品类",
        "product_scope": "商品范围",
        "color": "颜色",
        "color_depth": "颜色深浅",
        "size": "尺码",
        "season": "季节",
        "min_price": "最低价格",
        "max_price": "最高价格",
        "min_rating": "最低评分",
        "brand": "品牌",
        "material": "材质",
        "excluded_colors": "排除颜色",
        "excluded_categories": "排除品类",
        "excluded_materials": "排除材质",
        "excluded_brands": "排除品牌",
        "excluded_product_ids": "排除商品",
    }
    for stage in stages:
        if stage.count == 0:
            if stage.name == "only_in_stock":
                return "存在符合其他条件的商品，但当前库存为 0。"
            value = values.get(stage.name, stage.value)
            return f"没有找到同时符合{labels[stage.name]}“{value}”的商品。"
    return "没有找到符合条件的商品。"


def recommend(
    request: RecommendationRequest,
    intent_result: IntentResult | None = None,
) -> RecommendationResponse:
    filters = normalize_request(request)
    total_matches, _, stages = query_products(
        filters=filters,
        only_in_stock=request.only_in_stock,
        candidate_limit=1,
    )
    embedding_rows = query_embedding_rows(filters, request.only_in_stock)
    if embedding_rows:
        positive_filters = {
            "category": filters.category,
            "product_scope": filters.product_scope,
            "subcategory": filters.subcategory,
            "gender": filters.gender,
            "age_group": filters.age_group,
            "color": filters.color,
            "size": filters.size,
            "season": filters.season,
            "min_price": filters.min_price,
            "max_price": filters.max_price,
            "min_rating": filters.min_rating,
            "price_currency": filters.price_currency,
            "brand": filters.brand,
            "material": filters.material,
            "fit": filters.fit,
            "pattern": filters.pattern,
            "sleeve_length": filters.sleeve_length,
            "garment_length": filters.garment_length,
            "neckline": filters.neckline,
            "occasions": filters.occasions,
            "style_preferences": filters.style_preferences,
            "body_goals": filters.body_goals,
            "weather": filters.weather,
        }
        semantic_top = get_semantic_index().top_rows(
            raw_query=request.raw_query or "",
            filters=positive_filters,
            embedding_rows=embedding_rows,
            limit=min(900, max(600, request.limit * 50)),
        )
        candidate_rows = [row for row, _ in semantic_top]
        candidates = get_products_by_embedding_rows(candidate_rows)
        semantic_scores = [score for _, score in semantic_top]
        products = rank_products_hybrid(
            candidates,
            filters,
            semantic_scores,
            request.limit,
        )
        products = [apply_safety_guard(product) for product in products]
    else:
        products = []
    if products:
        reason = None
    elif total_matches == 0:
        reason = no_result_message(filters, stages)
    elif filters.occasions or filters.style_preferences:
        reason = "没有找到具有足够场景或风格证据的商品，我不会用不相关商品凑数。"
    else:
        reason = no_result_message(filters, stages)
    message = (
        f"初步筛选得到 {total_matches} 件商品，经相关性检查后展示前 {len(products)} 件。"
        if products
        else reason
    )
    return RecommendationResponse(
        message=message,
        filters=filters,
        total_matches=total_matches,
        products=products,
        filter_stages=stages,
        no_result_reason=reason,
        intent_result=intent_result,
        currency_notice=str(load_currency_config()["notice"]),
    )
