from __future__ import annotations

import re

from backend.currency import load_currency_config, usd_to_cny
from backend.product_explanation import build_product_explanation
from backend.safety_guard import check_product_reason
from backend.schemas import IntentType, NormalizedFilters, ProductCard
from backend.style_matcher import load_scene_rules, load_style_rules, match_scene, match_style


GARMENT_DETAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("长款", re.compile(r"\b(?:maxi|long)\b", re.IGNORECASE)),
    ("短款", re.compile(r"\b(?:mini|short)\b", re.IGNORECASE)),
    ("宽松剪裁", re.compile(r"\b(?:loose|oversized|relaxed)\b", re.IGNORECASE)),
    ("修身剪裁", re.compile(r"\b(?:bodycon|fitted|slim)\b", re.IGNORECASE)),
    ("无袖", re.compile(r"\bsleeveless\b", re.IGNORECASE)),
    ("长袖", re.compile(r"\blong[ -]?sleeve\b", re.IGNORECASE)),
    ("短袖", re.compile(r"\bshort[ -]?sleeve\b", re.IGNORECASE)),
    ("V领", re.compile(r"\bv[ -]?neck\b", re.IGNORECASE)),
    ("圆领", re.compile(r"\bcrew[ -]?neck\b", re.IGNORECASE)),
    ("露肩", re.compile(r"\boff[ -]?shoulder\b", re.IGNORECASE)),
    ("系带设计", re.compile(r"\blace[ -]?up\b", re.IGNORECASE)),
    ("带口袋", re.compile(r"\bpockets?\b", re.IGNORECASE)),
)

COLOR_PAIRING_SUGGESTIONS = {
    "brown": "鞋包可以优先考虑米色、白色或金色系",
    "yellow": "鞋包可以优先考虑白色、米色或牛仔蓝",
    "white": "鞋包可以用棕色、蓝色或金色增加层次",
    "black": "鞋包可保持黑白同色，也可以用金属色做点缀",
    "blue": "鞋包可以优先考虑白色、米色或棕色",
    "green": "鞋包可以优先考虑米色、白色或棕色",
    "red": "其余单品可以保持黑色、白色或米色，突出主体颜色",
    "multicolor": "其余单品可选取图案中的一种颜色，避免颜色过多",
}


def _garment_details(product: ProductCard) -> list[str]:
    source_text = " ".join(
        value for value in (product.title, product.features_text) if value
    )
    return [
        label
        for label, pattern in GARMENT_DETAIL_PATTERNS
        if pattern.search(source_text)
    ]


def _outfit_advice(product: ProductCard, filters: NormalizedFilters) -> str:
    category_advice = {
        "dress": "可以搭配线条简洁的鞋履和小体积包，避免配饰抢走裙装重点",
        "top": "可以搭配纯色下装，并从上衣颜色中选择一种颜色呼应鞋包",
        "skirt": "可以搭配轮廓简洁的上衣，让上下装只保留一个视觉重点",
        "pants": "可以搭配短款或利落上衣，并用鞋包统一整体颜色",
        "shorts": "可以搭配简洁上衣和平底鞋，保持轻松休闲的方向",
        "outerwear": "内搭和下装可保持简洁，让外套成为整体重点",
        "shoes": "可以从鞋子的颜色出发选择同色配饰，让整套穿搭更连贯",
        "bag": "可以让鞋子或首饰与包的颜色形成呼应",
    }.get(product.category, "其余单品可以保持简洁，并用鞋包呼应这件商品的颜色")
    scene_advice = (
        "海边场景可以优先搭配平底凉鞋、轻便包和少量配饰"
        if "beach" in filters.occasions
        else None
    )
    color_advice = COLOR_PAIRING_SUGGESTIONS.get(product.color or "")
    suggestions = [
        advice for advice in (category_advice, scene_advice, color_advice) if advice
    ]
    return (
        f"围绕「{product.title}」的搭配建议：{'；'.join(suggestions)}。"
        "这些是基于商品品类、颜色和当前场景给出的搭配方向。"
    )


def answer_product_question(
    product: ProductCard,
    intent: IntentType,
    message: str,
    filters: NormalizedFilters,
) -> str:
    if intent == IntentType.ASK_PRICE:
        if product.price is None:
            return f"「{product.title}」的商品资料没有价格信息。"
        cny_price = usd_to_cny(product.price)
        source_label = "演示价格" if product.price_source == "synthetic_demo" else "商品库价格"
        return (
            f"「{product.title}」的{source_label}为 ${product.price:.2f}，"
            f"约合 ¥{cny_price:.2f}。{load_currency_config()['notice']}"
        )

    if intent == IntentType.ASK_MATERIAL:
        if product.material is None:
            return f"「{product.title}」的商品资料没有明确材质，不能推测。"
        return f"「{product.title}」的商品资料标注材质为：{product.material}。"

    if intent == IntentType.ASK_SIZE:
        if product.size is None:
            return f"「{product.title}」的商品资料没有明确尺码，不能推测。"
        source = "演示尺码" if product.size_source == "synthetic_demo" else "商品库尺码"
        return f"「{product.title}」的{source}为 {product.size}。"

    if intent == IntentType.ASK_COLOR:
        if product.color is None:
            return f"「{product.title}」的商品资料没有明确颜色，不能推测。"
        return f"「{product.title}」的商品库颜色字段为 {product.color}。"

    if intent == IntentType.ASK_BRAND:
        if product.brand is None:
            return f"「{product.title}」的商品资料没有明确品牌，不能推测。"
        return f"「{product.title}」的商品库品牌为 {product.brand}。"

    if intent == IntentType.ASK_CARE:
        return f"「{product.title}」的商品资料没有可靠洗护说明，不能推测清洗方式。"

    if intent == IntentType.ASK_STYLE:
        matches: list[str] = []
        for style, rule in load_style_rules().items():
            _, positive, negative = match_style(product, style)
            if positive and not negative:
                matches.append(f"{rule['label']}（依据：{positive[0]}）")
        details = _garment_details(product)
        if not matches and not details:
            return f"「{product.title}」缺少足够的商品字段，不能可靠判断风格。"
        parts: list[str] = []
        if details:
            parts.append(f"商品标题或特征可确认的版型细节有：{'、'.join(details)}")
        if matches:
            parts.append(f"可匹配的风格为：{'、'.join(matches)}")
        return f"「{product.title}」" + "；".join(parts) + "。"

    if intent == IntentType.ASK_OCCASION:
        matches: list[str] = []
        for scene, rule in load_scene_rules().items():
            _, positive, negative, preferred_category, blocked_category = match_scene(
                product, scene
            )
            if (positive or preferred_category) and not negative and not blocked_category:
                evidence = positive[0] if positive else f"品类 {product.category}"
                matches.append(f"{rule['label']}（依据：{evidence}）")
        if not matches:
            return f"「{product.title}」缺少足够的商品字段，不能可靠判断适用场景。"
        return f"基于商品库字段，「{product.title}」可能适合：{'、'.join(matches[:3])}。"

    if intent == IntentType.ASK_STOCK:
        requested_size_match = re.search(
            r"(?<![A-Za-z0-9])(?:尺码)?(XS|S|M|L|XL|2XL|3XL|4XL)\s*码?",
            message,
            re.IGNORECASE,
        )
        requested_size = (
            requested_size_match.group(1).upper() if requested_size_match else None
        )
        source_label = "演示库存" if product.stock_source == "synthetic_demo" else "库存记录"
        if requested_size and product.size != requested_size:
            known_size = product.size or "未知"
            return (
                f"「{product.title}」当前商品资料只标注了 {known_size} 码，"
                f"不能确认 {requested_size} 码库存。"
            )
        size_text = f"{product.size}码" if product.size else "该商品"
        return (
            f"「{product.title}」的{source_label}显示{size_text}有 "
            f"{product.stock_quantity} 件。演示库存不代表实时可售库存。"
        )

    if intent == IntentType.OUTFIT_ADVICE:
        return _outfit_advice(product, filters)

    if intent in {IntentType.EXPLAIN_PRODUCT, IntentType.VIEW_PRODUCT}:
        original = build_product_explanation(product, filters)
        safety = check_product_reason(product, original)
        return safety.safe_text or "该商品的推荐理由未通过安全检查，已停止展示。"

    raise ValueError(f"Unsupported product question intent: {intent}")


def compare_products(
    products: list[ProductCard],
    filters: NormalizedFilters,
) -> str:
    if len(products) < 2:
        raise ValueError("at least two products are required for comparison")
    lines = ["导购比较依据（以下均来自当前商品库，不代表实时平台销量或流行趋势）："]
    for index, product in enumerate(products, start=1):
        evidence_items: list[str] = []
        for scene in filters.occasions:
            label, positive, negative, preferred, blocked = match_scene(product, scene)
            if (positive or preferred) and not negative and not blocked:
                source = positive[0] if positive else f"品类 {product.category}"
                evidence_items.append(f"{label}依据：{source}")
        for style in filters.style_preferences:
            label, positive, negative = match_style(product, style)
            if positive and not negative:
                evidence_items.append(f"{label}风格依据：{positive[0]}")
        evidence = "；".join(evidence_items) or "没有明确场景或风格证据"
        if product.price is None:
            price = "价格未知"
        else:
            price_label = (
                "演示价格"
                if product.price_source == "synthetic_demo"
                else "商品库价格"
            )
            price = f"{price_label} ${product.price:.2f}"
        size_label = (
            "演示尺码" if product.size_source == "synthetic_demo" else "商品库尺码"
        )
        season_label = (
            "演示季节"
            if product.season_source == "synthetic_demo"
            else "商品库季节"
        )
        lines.append(
            f"候选{index}「{product.title}」：品类 {product.category or '未知'}，"
            f"颜色 {product.color or '未知'}，{size_label} {product.size or '未知'}，"
            f"{season_label} {product.season or '未知'}，材质 {product.material or '未知'}，"
            f"{price}，商品库评分 {product.rating:.1f}，"
            f"评论记录 {product.rating_count} 条；{evidence}。"
        )
    if filters.occasions:
        lines.append(f"用户当前场景：{'、'.join(filters.occasions)}。")
    if filters.style_preferences:
        lines.append(f"用户当前风格偏好：{'、'.join(filters.style_preferences)}。")
    if filters.max_price is not None:
        lines.append(
            f"用户当前预算上限：{filters.max_price:.2f} {filters.price_currency}。"
        )

    highest_rated = max(products, key=lambda item: (item.rating, item.rating_count))
    most_reviewed = max(products, key=lambda item: item.rating_count)
    lines.append(
        f"商品库客观比较：评分最高的是「{highest_rated.title}」"
        f"（{highest_rated.rating:.1f}）；评论记录最多的是「{most_reviewed.title}」"
        f"（{most_reviewed.rating_count} 条）。"
    )
    priced_products = [product for product in products if product.price is not None]
    if priced_products:
        lowest_priced = min(priced_products, key=lambda item: item.price or 0.0)
        source = (
            "演示价格"
            if lowest_priced.price_source == "synthetic_demo"
            else "商品库价格"
        )
        lines.append(
            f"价格最低的是「{lowest_priced.title}」"
            f"（{source} ${lowest_priced.price:.2f}）。"
        )
    lines.append(
        "比较时应分别说明定位和取舍，给出首选与备选，再询问一个影响决策的偏好；"
        "不得把评分或评论数说成销量、热卖程度或店内销售情况。"
    )
    lines.append("标记为演示的价格、尺码、季节和库存不代表实时商品信息。")
    return "\n".join(lines)
