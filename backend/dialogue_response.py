from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

from backend.llm_client import (
    DEEPSEEK_API_URL,
    DEEPSEEK_MODEL,
    read_deepseek_api_key,
)
from backend.schemas import RecommendationResponse
from backend.usage_logger import log_api_usage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PERSONA_PATH = PROJECT_ROOT / "configs" / "advisor_persona.yaml"
PROHIBITED_PHRASES_PATH = PROJECT_ROOT / "configs" / "prohibited_phrases.yaml"


@lru_cache(maxsize=1)
def load_persona() -> dict[str, object]:
    return dict(yaml.safe_load(PERSONA_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def load_prohibited_patterns() -> list[str]:
    config = yaml.safe_load(PROHIBITED_PHRASES_PATH.read_text(encoding="utf-8"))
    return [str(item["pattern"]) for item in config["phrases"]]


def _product_facts(response: RecommendationResponse) -> list[dict[str, object]]:
    return [
        {
            "rank": index,
            "title": product.title,
            "brand": product.brand,
            "category": product.category,
            "color": product.color,
            "size": product.size,
            "size_source": product.size_source,
            "season": product.season,
            "season_source": product.season_source,
            "material": product.material,
            "price": product.price,
            "price_source": product.price_source,
            "rating": product.rating,
            "rating_count": product.rating_count,
            "stock_quantity": product.stock_quantity,
            "stock_source": product.stock_source,
            "features_text": product.features_text,
            "matched_features": product.matched_features[:6],
            "reason_safe": product.reason_safe,
        }
        for index, product in enumerate(response.products[:4], start=1)
    ]


def build_advisor_system_prompt(response_mode: str) -> str:
    common_rules = """你是服装导购 Mia，负责把系统结果转成自然、温和、专业的中文导购回复。
只输出回复正文，不要输出 JSON、Markdown、代码块或字段名。
先回应用户，再给一个具体下一步。可以自然使用“好呀～”“我帮你看了一下”，不要每句都用敬语或夸张赞美。
不要使用“初步筛选得到”“经相关性检查”“对比结果如下”等系统语言。
只能使用输入中的事实。不得编造新款、销量、热度、价格、材质、库存、尺码、穿着效果或用户身材。
不得说全网最低、只剩最后一件、保证显瘦、一定适合。
提到显高、显瘦、遮腹、肩部或腿型时，只能引用 matched_features 中的版型证据，并表述为“更利于/有助于/更贴近该诉求”，不能保证实际穿着效果。
评分和评论数只能表述为“商品库记录的评分较高”或“评论记录更多”，不能推断为销量、热卖程度或店内销售情况。
未提供带来源和日期的外部趋势事实时，不得声称某款是当下流行、小红书热门、淘宝热卖或店内畅销。
只有外部数据状态为未接入时，才说无法核实实时淘宝销量或小红书热度；普通商品库推荐不要提这件事。
可以复述用户的预算，但若提及某件商品的演示价格、尺码、季节或库存，必须同时明说“演示”。
必须在当前回复中完成已有结果的说明，禁止说“稍等”“等一下”“稍后”“我再帮你看看”等要求用户等待下一步的空话。
用户问“推荐哪件、选哪件”且系统已经给出一件商品时，第一句必须明确说“我推荐这件”，并直接说明理由，不得重新描述为一批筛选结果。
若系统要求澄清，直接问该问题。"""
    if response_mode == "COMPARISON":
        return common_rules + """
当前任务是商品比较，比较回复不受 persona 中“一至三句”的限制，使用四至八句并允许自然换行。
先用一句话概括这些商品的共同点和主要差异。
随后逐件介绍，每件使用由颜色、款式或品类组成的简短名称，例如“棕色系带长裙”，不要使用“第一件、第二件”等编号，也不要复制冗长英文标题。
每件只说有事实依据的风格定位、适合场景、明显优势和限制；没有依据就不要补充。
必须给出一个明确的首选和一个有条件的备选，并把理由绑定到用户已有的场景、风格、预算或商品库评分与评论记录。
最后只问一个真正影响选择的偏好问题。这个问题必须是二选一，例如更重视上镜氛围、活动方便、版型、颜色还是预算；不要泛泛地问“还想了解哪件”。
问题中的两个选项必须分别写明对应的候选序号，例如“你更看重第一件的活动方便舒适，还是第三件的上镜氛围感？”，确保用户只回复偏好时也能定位商品。
语气要像真人导购在帮助用户做决定，而不是生成数据报告。"""
    return common_rules + """
回复一至三句。
推荐列表回复只总结用户条件、匹配数量和下一步建议，不要逐件复述商品标题、价格或品类；用户明确询问某件时才说该商品细节。"""


def extract_advisor_reply(response_data: dict[str, Any]) -> str:
    choices = response_data["choices"]
    reply = str(choices[0]["message"]["content"]).strip()
    if not reply:
        raise ValueError("advisor reply cannot be empty")
    return reply


def validate_advisor_reply(reply: str, response: RecommendationResponse) -> None:
    for pattern in load_prohibited_patterns():
        if re.search(pattern, reply):
            raise ValueError(f"advisor reply contains prohibited claim: {pattern}")
    if re.search(r"(?:销量最高|销量第一|最畅销|全网爆款|全网热销)", reply):
        raise ValueError("advisor reply contains unsupported sales claim")
    if re.search(
        r"(?:热卖款|畅销款|爆款|卖得(?:最好|很好)|销量(?:很高|领先|靠前))",
        reply,
    ):
        raise ValueError("advisor reply contains unsupported sales performance claim")
    if re.search(
        r"(?:当下|最近|今年|当前).{0,10}(?:很流行|正流行|热门|爆火|很火)",
        reply,
    ):
        raise ValueError("advisor reply contains unsupported current trend claim")
    if re.search(r"(?:店里|平台|淘宝|小红书)(?:最近|刚刚)?(?:上新|新款)", reply):
        raise ValueError("advisor reply contains unsupported new-arrival claim")
    if any(product.stock_source == "synthetic_demo" for product in response.products):
        if "库存" in reply and "演示" not in reply:
            raise ValueError("advisor reply presents demo stock without disclosure")
    if any(product.price_source == "synthetic_demo" for product in response.products):
        if re.search(r"(?:价格|售价)(?:都|为|是|约|在)", reply) and "演示" not in reply:
            raise ValueError("advisor reply presents demo price without disclosure")


def generate_advisor_reply(
    *,
    user_message: str,
    base_response: RecommendationResponse,
    recent_messages: list[dict[str, str]],
    remembered_preferences: dict[str, str | float | list[str]],
) -> str:
    persona = load_persona()
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": build_advisor_system_prompt(base_response.response_mode),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "persona": persona,
                        "user_message": user_message,
                        "recent_messages": recent_messages[-8:],
                        "remembered_preferences": remembered_preferences,
                        "intent": (
                            base_response.intent_result.intent.value
                            if base_response.intent_result
                            else None
                        ),
                        "clarify_question": (
                            base_response.intent_result.clarify_question
                            if base_response.intent_result
                            else None
                        ),
                        "response_mode": base_response.response_mode,
                        "system_result": base_response.message,
                        "filters": base_response.filters.model_dump(mode="json"),
                        "total_matches": base_response.total_matches,
                        "shown_count": len(base_response.products),
                        "product_facts": _product_facts(base_response),
                        "external_data_status": (
                            "not_connected"
                            if base_response.intent_result
                            and base_response.intent_result.intent.value
                            in {
                                "ASK_TREND",
                                "SEARCH_EXTERNAL_PRODUCT",
                                "ASK_NEW_ARRIVAL",
                            }
                            else "not_applicable"
                        ),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.6,
        "max_tokens": 520 if base_response.response_mode == "COMPARISON" else 320,
    }
    started = time.perf_counter()
    response = httpx.post(
        DEEPSEEK_API_URL,
        headers={
            "Authorization": f"Bearer {read_deepseek_api_key()}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30.0,
    )
    response.raise_for_status()
    response_data = response.json()
    log_api_usage(
        request_index=time.time_ns(),
        call_type="advisor_reply",
        call_type_index=1,
        model=DEEPSEEK_MODEL,
        prefix="grounded_advisor_reply",
        elapsed_seconds=round(time.perf_counter() - started, 3),
        usage=response_data["usage"],
    )
    reply = extract_advisor_reply(response_data)
    validate_advisor_reply(reply, base_response)
    return reply
