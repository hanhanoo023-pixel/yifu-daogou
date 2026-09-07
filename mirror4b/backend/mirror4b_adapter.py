from __future__ import annotations

from collections.abc import Iterable
import os
from typing import Any, Literal

from backend.schemas import (
    AgentAction,
    AgentActionType,
    AgentWorkflowState,
    Mirror4BAgentActionResponse,
    Mirror4BAgentChatResponse,
    Mirror4BProduct,
    NormalizedFilters,
    ProductCard,
    RecommendationResponse,
)


CATEGORY_MAP = {
    "上衣": "top",
    "衬衫": "top",
    "T恤": "top",
    "连衣裙": "dress",
    "长裤": "pants",
    "裤子": "pants",
    "短裤": "shorts",
    "半身裙": "skirt",
    "外套": "outerwear",
    "夹克": "outerwear",
    "大衣": "outerwear",
    "毛衣": "sweater",
    "卫衣": "sweater",
    "鞋": "shoes",
    "鞋子": "shoes",
    "包": "bag",
    "包包": "bag",
    "首饰": "jewelry",
    "手表": "watch",
    "配饰": "accessory",
    "套装": "set",
}

AGENT_ACTION_MAP = {
    "call_store_staff": (AgentActionType.CONTACT_SALES, "一键召唤服务员"),
    "request_person_photo": (AgentActionType.SELECT_PERSON_IMAGE, "选择人物照片"),
    "browse_clothes": (AgentActionType.OPEN_PRODUCT_LIBRARY, "查看推荐商品"),
    "inspect_product": (AgentActionType.OPEN_PRODUCT_DETAIL, "查看商品详情"),
    "try_on": (AgentActionType.START_TRY_ON, "开始 AI 试穿"),
    "compare_try_on": (AgentActionType.START_TRY_ON, "开始 AI 试穿"),
    "change_background": (AgentActionType.CHANGE_BACKGROUND, "更换背景"),
    "choose_background": (AgentActionType.CHANGE_BACKGROUND, "选择背景"),
    "save_result": (AgentActionType.SAVE_RESULT, "保存试穿结果"),
    "purchase_interest": (AgentActionType.PURCHASE_PRODUCT, "加入购物车"),
}

NAVIGATION_ROUTE_MAP = {
    "/scan-upload": "person_images",
    "/shooting": "person_images",
    "/chat": "shopping",
    "/landing": "shopping",
    "/home": "shopping",
}


def mirror4b_currency() -> Literal["USD", "CNY"]:
    value = os.getenv("MIRROR4B_CURRENCY", "CNY").strip().upper()
    if value not in {"USD", "CNY"}:
        raise ValueError("MIRROR4B_CURRENCY must be USD or CNY")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return str(value) if value not in (None, "") else None


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("Mirror4B list field must be an array")
    return [str(item) for item in value]


def mirror4b_product_to_card(product: Mirror4BProduct) -> ProductCard:
    category_value = _optional_text(product.category_data, "category")
    category = CATEGORY_MAP.get(category_value or "", category_value)
    price_value = product.sales_data["price"]
    stock_value = product.sales_data["total_stock"]
    sizes = [str(stock["size"]) for stock in product.stocks]
    unique_sizes = list(dict.fromkeys(sizes))
    material = _optional_text(product.feature_data, "material")
    style_tags = _string_list(product.feature_data.get("style_tags"))
    scenarios = _string_list(product.feature_data.get("applicable_scenarios"))
    fit = _optional_text(product.feature_data, "fit")
    description = _optional_text(product.feature_data, "description")
    selling_points = _optional_text(product.feature_data, "selling_points")

    return ProductCard(
        parent_asin=str(product.id),
        title=product.name,
        brand=_optional_text(product.category_data, "brand"),
        category=category,
        color=_optional_text(product.category_data, "color"),
        size=unique_sizes[0] if unique_sizes else None,
        season=_optional_text(product.feature_data, "season"),
        material=material,
        price=float(price_value) if price_value is not None else None,
        currency=mirror4b_currency(),
        image_url=product.main_image_url,
        rating=0,
        rating_count=0,
        stock_quantity=int(stock_value),
        price_source="mirror4b_sales_data",
        size_source="mirror4b_stocks",
        color_source="mirror4b_category_data",
        season_source="mirror4b_feature_data",
        stock_source="mirror4b_sales_data",
        category_source="mirror4b_category_data",
        business_category=category,
        business_subcategory=_optional_text(product.category_data, "subcategory"),
        business_colors=(
            [_optional_text(product.category_data, "color")]
            if _optional_text(product.category_data, "color") is not None
            else []
        ),
        business_styles=style_tags,
        business_occasions=scenarios,
        business_fits=[fit] if fit is not None else [],
        business_materials=[material] if material is not None else [],
        business_sizes=unique_sizes,
        features_text=selling_points,
        description_text=description,
        matched_features=style_tags + scenarios,
        reason=selling_points or "",
        reason_original=selling_points or "",
        reason_safe=selling_points or "",
    )


def build_product_catalog(
    products: Iterable[Mirror4BProduct],
) -> dict[str, ProductCard]:
    catalog: dict[str, ProductCard] = {}
    for product in products:
        key = str(product.id)
        if key in catalog:
            raise ValueError(f"duplicate Mirror4B product id: {key}")
        catalog[key] = mirror4b_product_to_card(product)
    return catalog


def _string_product_id(parameters: dict[str, Any]) -> str | None:
    value = parameters.get("product_id")
    return str(value) if value is not None else None


def mirror4b_action_to_stylemate(
    action: Mirror4BAgentActionResponse | None,
) -> AgentAction | None:
    if action is None or action.payload is None:
        return None
    if action.type in {"NAVIGATE", "NAVIGATE_GO"}:
        route_name = action.payload.route_name
        mapped_route = NAVIGATION_ROUTE_MAP.get(route_name or "", route_name)
        return AgentAction(
            type=AgentActionType.NAVIGATE_APP,
            label="打开对应功能",
            payload={"route_name": mapped_route},
        )
    if action.type != "AGENT_INTENT" or action.payload.intent is None:
        raise ValueError(f"unsupported Mirror4B action type: {action.type}")
    action_type, label = AGENT_ACTION_MAP[action.payload.intent]
    parameters = dict(action.payload.parameters)
    product_id = _string_product_id(parameters)
    if product_id is not None:
        parameters["product_id"] = product_id
    return AgentAction(type=action_type, label=label, payload=parameters)


def _recommended_product_ids(response: Mirror4BAgentChatResponse) -> list[str]:
    rows = response.message.recommended_products or []
    return [str(row["id"]) for row in rows]


def recommendation_response_from_agent(
    *,
    response: Mirror4BAgentChatResponse,
    catalog: dict[str, ProductCard],
    filters: NormalizedFilters,
    workflow_state: AgentWorkflowState,
    selected_product_ids: list[str],
) -> RecommendationResponse:
    recommended_ids = _recommended_product_ids(response)
    products = [catalog[product_id] for product_id in recommended_ids]
    action = mirror4b_action_to_stylemate(response.message.action)
    action_product_id = (
        str(action.payload["product_id"])
        if action is not None and "product_id" in action.payload
        else None
    )
    next_selected_ids = (
        [action_product_id]
        if action_product_id is not None
        else selected_product_ids
    )
    next_workflow_state = workflow_state.model_copy(
        update={"selected_product_ids": next_selected_ids}
    )
    return RecommendationResponse(
        message=response.message.text,
        filters=filters,
        total_matches=len(products),
        products=products,
        filter_stages=[],
        no_result_reason=None,
        response_mode="RECOMMENDATIONS" if products else "MESSAGE",
        selected_product_id=(
            next_selected_ids[0] if len(next_selected_ids) == 1 else None
        ),
        selected_product_ids=next_selected_ids,
        currency_notice="",
        action=action,
        workflow_state=next_workflow_state,
    )
