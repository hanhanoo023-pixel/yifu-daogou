from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.database import connect_read_only, query_embedding_rows, query_products
from backend.llm_client import parse_shopping_request, read_intent_api_settings
from backend.recommender import OCCASION_ALIASES, normalize_request, recommend
from backend.schemas import (
    IntentResult,
    IntentType,
    NormalizedFilters,
    ParsedShoppingRequest,
    PendingAction,
    RecommendationRequest,
)
from backend.style_matcher import load_scene_rules


client = TestClient(app)


def test_health_reports_imported_product_count() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "product_count": 255565}


def test_strictly_audited_fashion200k_product_count() -> None:
    connection = connect_read_only()
    count = connection.execute(
        """
        SELECT COUNT(*)
        FROM products
        WHERE parent_asin LIKE 'F200K_%'
          AND NOT EXISTS (
              SELECT 1 FROM product_moderation
              WHERE product_moderation.product_id = products.parent_asin
                AND product_moderation.status = 'FAIL'
          )
        """
    ).fetchone()[0]
    connection.close()

    assert count == 3294


def test_color_audit_failures_are_excluded_from_candidates() -> None:
    _, candidates, _ = query_products(
        NormalizedFilters(),
        only_in_stock=False,
        candidate_limit=10,
    )

    candidate_ids = {product.parent_asin for product in candidates}
    assert "F200K_000001307" not in candidate_ids


def test_strictly_audited_products_enter_the_candidate_pool() -> None:
    _, candidates, _ = query_products(
        NormalizedFilters(category="top", color="blue"),
        only_in_stock=True,
        candidate_limit=10,
    )

    assert any(product.parent_asin.startswith("F200K_") for product in candidates)


def test_clothing_scope_excludes_non_clothing_categories() -> None:
    _, candidates, _ = query_products(
        NormalizedFilters(product_scope="clothing"),
        only_in_stock=False,
        candidate_limit=50,
    )

    assert candidates
    assert all(
        product.category
        in {
            "top",
            "dress",
            "pants",
            "shorts",
            "skirt",
            "outerwear",
            "sweater",
            "swimwear",
            "underwear",
            "set",
        }
        for product in candidates
    )


def test_minimum_rating_filters_candidates() -> None:
    _, candidates, _ = query_products(
        NormalizedFilters(min_rating=4.5),
        only_in_stock=False,
        candidate_limit=50,
    )

    assert candidates
    assert all(product.rating >= 4.5 for product in candidates)


def test_screenshot_color_mismatches_are_absent_from_clean_catalog() -> None:
    connection = connect_read_only()
    present = {
        row[0]
        for row in connection.execute(
            """
            SELECT parent_asin FROM products
            WHERE parent_asin IN ('F200K_000164615', 'F200K_000201581')
            """
        )
    }
    connection.close()

    assert present == set()


def test_chinese_filters_are_normalized() -> None:
    filters = normalize_request(
        RecommendationRequest(
            category="上衣",
            color="黄色",
            size="l",
            season="夏天",
        )
    )

    assert filters == NormalizedFilters(
        category="top", color="yellow", size="L", season="summer"
    )


def test_work_occasion_is_normalized_to_commute() -> None:
    filters = normalize_request(RecommendationRequest(occasions=["work"]))

    assert filters.occasions == ["commute"]


def test_unknown_occasion_stays_only_in_raw_semantic_query() -> None:
    filters = normalize_request(
        RecommendationRequest(
            raw_query="参加一个很特别的私人活动",
            occasions=["unknown-scene"],
        )
    )

    assert filters.occasions == []


def test_recommender_scene_aliases_come_from_scene_configuration() -> None:
    scene_rules = load_scene_rules()

    assert set(OCCASION_ALIASES) == set(scene_rules)
    for scene, rule in scene_rules.items():
        expected_aliases = {
            scene,
            *(str(alias).casefold() for alias in rule["aliases"]),
        }
        assert OCCASION_ALIASES[scene] == expected_aliases


def test_light_color_depth_excludes_dark_compound_color_products() -> None:
    filters = normalize_request(RecommendationRequest(color_depth="light"))
    embedding_rows = set(query_embedding_rows(filters, only_in_stock=False))
    connection = connect_read_only()
    dark_rows = {
        int(row[0]) - 1
        for row in connection.execute(
            """
            SELECT id FROM products
            WHERE parent_asin IN ('B0C8BZJ2BW', 'B09KYVZXVM')
            """
        )
    }
    connection.close()

    assert filters.color_depth == "light"
    assert dark_rows
    assert dark_rows.isdisjoint(embedding_rows)


def test_fashion200k_business_colors_drive_hard_filtering() -> None:
    connection = connect_read_only()
    embedding_row = int(
        connection.execute(
            "SELECT id - 1 FROM products WHERE parent_asin='F200K_000059791'"
        ).fetchone()[0]
    )
    original_color = str(
        connection.execute(
            "SELECT color_norm FROM products WHERE parent_asin='F200K_000059791'"
        ).fetchone()[0]
    )
    connection.close()

    blue_rows = set(
        query_embedding_rows(NormalizedFilters(color="blue"), only_in_stock=False)
    )

    assert original_color == "white"
    assert embedding_row in blue_rows


def test_yellow_summer_large_top_recommendation() -> None:
    result = recommend(
        RecommendationRequest(
            category="上衣",
            color="黄色",
            size="L",
            season="夏季",
            only_in_stock=True,
            limit=12,
        )
    )

    assert result.total_matches > 0
    assert len(result.products) == 12
    assert result.no_result_reason is None
    assert all(product.category == "top" for product in result.products)
    assert all(product.color == "yellow" for product in result.products)
    assert all(product.size == "L" for product in result.products)
    assert all(
        product.season == "summer" or product.season_source == "synthetic_demo"
        for product in result.products
    )
    assert all(product.stock_quantity > 0 for product in result.products)


def test_no_result_explains_failed_filter() -> None:
    result = recommend(
        RecommendationRequest(
            category="手表",
            color="黄色",
            size="4XL",
            season="夏季",
        )
    )

    assert result.total_matches == 0
    assert result.products == []
    assert result.no_result_reason is not None
    assert result.message == result.no_result_reason


def test_zero_hard_filter_is_reported_before_scene_relevance() -> None:
    result = recommend(
        RecommendationRequest(
            raw_query="我想找一些适合夏天去海边玩的衣服",
            category="watch",
            product_scope="clothing",
            season="summer",
            occasions=["beach"],
            limit=6,
        )
    )

    assert result.total_matches == 0
    assert result.products == []
    assert result.no_result_reason == "没有找到同时符合商品范围“clothing”的商品。"


def test_generic_beach_clothing_scope_returns_recommendations() -> None:
    result = recommend(
        RecommendationRequest(
            raw_query="我想找一些适合夏天去海边玩的衣服",
            product_scope="clothing",
            season="summer",
            occasions=["beach"],
            limit=6,
        )
    )

    assert result.total_matches == 50695
    assert len(result.products) == 6
    assert all(product.category != "clothing" for product in result.products)
    assert result.no_result_reason is None


def test_recommendation_api_returns_product_cards() -> None:
    response = client.post(
        "/api/recommend",
        json={
            "category": "上衣",
            "color": "黄色",
            "size": "L",
            "season": "夏天",
            "only_in_stock": True,
            "limit": 3,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total_matches"] > 0
    assert len(body["products"]) == 3
    assert all(product["stock_quantity"] > 0 for product in body["products"])
    assert all("embedding_row" not in product for product in body["products"])
    assert all("semantic_raw" in product["component_scores"] for product in body["products"])
    assert all("hybrid_final" in product["component_scores"] for product in body["products"])
    assert all(product["safety"]["status"] == "PASS" for product in body["products"])
    assert all(product["reason_safe"] for product in body["products"])


def test_siliconflow_intent_response_is_parsed_and_raw_usage_is_logged(
    monkeypatch,
    tmp_path: Path,
) -> None:
    raw_usage = {
        "prompt_tokens": 120,
        "completion_tokens": 35,
        "total_tokens": 155,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 120,
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"REFINE_FILTERS","secondary_intents":[],"confidence":0.91,"clarify_needed":false,"clarify_question":null,"slot_operations":[{"field":"color","operation":"SET","value":"yellow"},{"field":"max_price","operation":"SET","value":300},{"field":"min_rating","operation":"SET","value":4.5},{"field":"price_currency","operation":"SET","value":"CNY"},{"field":"material","operation":"SET","value":"cotton"},{"field":"occasions","operation":"ADD","value":["beach"]},{"field":"excluded_colors","operation":"ADD","value":["pink"]},{"field":"excluded_materials","operation":"ADD","value":["wool"]},{"field":"negative_colors","operation":"ADD","value":["gray"]}],"requested_fields":[],"product_references":[],"warnings":[]}'
                        }
                    }
                ],
                "usage": raw_usage,
            }

    logged: dict[str, object] = {}

    sent_payload: dict[str, object] = {}
    sent_request: dict[str, object] = {}

    def fake_post(url, **kwargs) -> FakeResponse:
        sent_request["url"] = url
        sent_request["headers"] = kwargs["headers"]
        sent_payload.update(kwargs["json"])
        return FakeResponse()

    def fake_log_api_usage(**kwargs) -> dict[str, object]:
        logged.update(kwargs)
        return kwargs

    env_path = tmp_path / ".env"
    env_path.write_text(
        "STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1\n"
        "STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B\n"
        "STYLEMATE_INTENT_API_KEY=test-stylemate-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    monkeypatch.setattr("backend.llm_client.httpx.post", fake_post)
    monkeypatch.setattr("backend.llm_client.log_api_usage", fake_log_api_usage)

    result = parse_shopping_request(
        "换成黄色",
        limit=6,
        current_filters=NormalizedFilters(
            category="top",
            color="blue",
            size="L",
            season="summer",
        ),
    )

    assert result.intent_result.intent == IntentType.REFINE_FILTERS
    assert result.intent_result.confidence == 0.95
    assert result.intent_result.parser_source == "RULE_LLM_FUSION"
    assert result.intent_result.clarify_needed is False
    assert result.recommendation_request is not None
    assert result.recommendation_request == RecommendationRequest(
        raw_query="换成黄色",
        category="top",
        color="yellow",
        size="L",
        season="summer",
        max_price=300,
        min_rating=4.5,
        price_currency="CNY",
        material="cotton",
        occasions=["beach"],
        excluded_colors=["pink"],
        excluded_materials=["wool"],
        negative_colors=["gray"],
        only_in_stock=True,
        limit=6,
    )
    assert logged["usage"] is raw_usage
    assert logged["call_type"] == "intent_parse"
    assert logged["model"] == "Qwen/Qwen3.5-4B"
    assert sent_request["url"] == "https://api.siliconflow.cn/v1/chat/completions"
    assert sent_request["headers"]["Authorization"] == "Bearer test-stylemate-key"
    assert sent_payload["model"] == "Qwen/Qwen3.5-4B"
    assert sent_payload["enable_thinking"] is False
    system_content = sent_payload["messages"][0]["content"]
    assert "即使只有一个值也必须使用数组" in system_content
    assert '"field":"occasions","operation":"ADD","value":["beach"]' in system_content
    assert '"field":"negative_colors","operation":"ADD","value":["pink"]' in system_content
    user_content = __import__("json").loads(sent_payload["messages"][1]["content"])
    assert user_content["current_filters"]["category"] == "top"
    assert user_content["current_filters"]["color"] == "blue"
    assert user_content["current_filters"]["occasions"] == []
    assert user_content["current_product_id"] is None
    assert user_content["selected_product_ids"] == []
    assert user_content["visible_product_ids"] == []
    assert user_content["recent_messages"] == []
    assert user_content["remembered_preferences"] == {}
    assert user_content["new_message"] == "换成黄色"


def test_intent_api_does_not_fall_back_to_another_provider(
    monkeypatch,
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=test-key\n", encoding="utf-8")
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    for name in (
        "STYLEMATE_INTENT_BASE_URL",
        "STYLEMATE_INTENT_MODEL",
        "STYLEMATE_INTENT_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(ValueError, match="configuration is incomplete"):
        read_intent_api_settings()


def test_usage_logger_preserves_raw_usage_structure(monkeypatch, tmp_path: Path) -> None:
    from backend import usage_logger

    log_path = tmp_path / "api_usage.jsonl"
    raw_usage = {
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12,
        "nested": {"provider_field": 7},
    }
    monkeypatch.setattr(usage_logger, "USAGE_LOG_PATH", log_path)

    record = usage_logger.log_api_usage(
        request_index=1,
        call_type="intent_parse",
        call_type_index=1,
        model="deepseek-chat",
        prefix="shopping_intent_parse",
        elapsed_seconds=0.5,
        usage=raw_usage,
    )

    saved = __import__("json").loads(log_path.read_text(encoding="utf-8"))
    assert record["usage"] is raw_usage
    assert saved["usage"] == raw_usage


def test_chat_api_connects_intent_parser_to_recommendation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_parse_shopping_request(
        message: str,
        limit: int,
        current_filters: NormalizedFilters | None,
        current_product_id: str | None,
        visible_product_ids: list[str],
        recent_messages: list[dict[str, str]],
        remembered_preferences: dict[str, str | float | list[str]],
        selected_product_ids: list[str],
        pending_action: PendingAction | None,
    ) -> ParsedShoppingRequest:
        assert message == "换成黄色"
        assert current_filters == NormalizedFilters(
            category="top",
            color="blue",
            size="L",
            season="summer",
        )
        assert current_product_id is None
        assert visible_product_ids == []
        assert recent_messages == []
        assert remembered_preferences == {}
        assert selected_product_ids == []
        assert pending_action is None
        recommendation_request = RecommendationRequest(
            raw_query=message,
            category="top",
            color="yellow",
            size="L",
            season="summer",
            only_in_stock=True,
            limit=limit,
        )
        return ParsedShoppingRequest(
            intent_result=IntentResult(
                intent=IntentType.REFINE_FILTERS,
                slots=NormalizedFilters(
                    category="top",
                    color="yellow",
                    size="L",
                    season="summer",
                ),
                confidence=0.98,
                parser_source="RULE_LLM_FUSION",
            ),
            recommendation_request=recommendation_request,
        )

    monkeypatch.setattr("backend.app.parse_shopping_request", fake_parse_shopping_request)
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: "好呀～已经帮你换成黄色，新结果放在右边了。",
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "换成黄色",
            "session_id": "session-test-001",
            "profile_id": "profile-test-001",
            "current_filters": {
                "category": "top",
                "color": "blue",
                "size": "L",
                "season": "summer",
            },
            "limit": 4,
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["total_matches"] > 0
    assert len(body["products"]) == 4
    assert body["filters"]["category"] == "top"
    assert body["filters"]["color"] == "yellow"
    assert body["filters"]["size"] == "L"
    assert body["filters"]["season"] == "summer"
    assert body["intent_result"]["intent"] == "REFINE_FILTERS"
    assert body["memory_status"]["recent_turn_count"] == 1
    assert body["message"] == "好呀～已经帮你换成黄色，新结果放在右边了。"
