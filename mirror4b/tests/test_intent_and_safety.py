import pytest
from fastapi.testclient import TestClient

from backend.app import app
from backend.conversation_memory import record_turn
from backend.dialogue_response import build_advisor_system_prompt, extract_advisor_reply
from backend.llm_client import (
    LLM_ENUM_ALIASES,
    SYSTEM_PROMPT,
    UNSUPPORTED_OCCASION_MESSAGE,
    UNSUPPORTED_OCCASION_WARNING,
    VALID_OCCASION_VALUES,
    apply_slot_operations,
    detect_conflicts,
    detect_price_currency,
    detect_rule_intent,
    detect_rule_intents,
    detect_rule_slot_operations,
    detect_text_conflicts,
    infer_pending_action,
    merge_slot_operations,
    normalize_intent_response_lists,
    offered_product_options,
    parse_llm_slot_operations,
    parse_shopping_request,
    resolve_contextual_follow_up_intents,
    resolve_contextual_comparison_preference,
    resolve_current_product_recommendation,
    resolve_contextual_product_option,
    resolve_contextual_recommendation_confirmation,
    resolve_pending_action,
    resolve_product_references,
)
from backend.product_explanation import build_product_explanation
from backend.product_query import answer_product_question, compare_products
from backend.safety_guard import check_product_reason
from backend.style_matcher import load_scene_rules
from backend.schemas import (
    IntentResult,
    IntentType,
    NormalizedFilters,
    ParsedShoppingRequest,
    PendingActionType,
    ProductCard,
    RecommendationRequest,
    RecommendationResponse,
    SlotOperation,
)


client = TestClient(app)


def product() -> ProductCard:
    return ProductCard(
        parent_asin="TEST001",
        title="Blue cotton shirt",
        brand="Test Brand",
        category="top",
        color="blue",
        size="M",
        season="summer",
        material="Cotton",
        price=29.99,
        image_url="https://example.com/product.jpg",
        rating=4.5,
        rating_count=100,
        stock_quantity=10,
        price_source="synthetic_demo",
        size_source="synthetic_demo",
        color_source="explicit_amazon",
        season_source="synthetic_demo",
        stock_source="synthetic_demo",
        category_source="fashion200k_category",
        features_text="甜美",
        matched_features=["品类：top", "颜色：blue", "材质：Cotton"],
    )


def test_body_goal_terms_become_structured_filters() -> None:
    filters = apply_slot_operations(
        NormalizedFilters(occasions=["commute"]),
        detect_rule_slot_operations("想要显高显瘦，还要遮肚子"),
    )

    assert filters.body_goals == ["elongate", "streamline", "tummy_coverage"]
    assert filters.occasions == ["commute"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("这件多少钱", IntentType.ASK_PRICE),
        ("有L码吗", IntentType.ASK_STOCK),
        ("这是什么面料", IntentType.ASK_MATERIAL),
        ("换一件", IntentType.REQUEST_ALTERNATIVE),
        ("为什么推荐这件", IntentType.EXPLAIN_PRODUCT),
        ("帮我订机票", IntentType.OUT_OF_SCOPE),
        ("我想看看衬衫", IntentType.BROWSE_PRODUCT),
        ("推荐一件上衣", IntentType.RECOMMEND_PRODUCT),
        ("第一件和第三件哪个好", IntentType.COMPARE_PRODUCTS),
        ("这件怎么洗", IntentType.ASK_CARE),
        ("清空所有条件", IntentType.CLEAR_FILTERS),
        ("换成蓝色", IntentType.REFINE_FILTERS),
        ("谢谢", IntentType.THANKS),
        ("黑色连衣裙怎么搭", IntentType.OUTFIT_ADVICE),
        ("小红书最近流行什么风格", IntentType.ASK_TREND),
        ("去淘宝找秋天外套", IntentType.SEARCH_EXTERNAL_PRODUCT),
        ("有什么最新款", IntentType.ASK_NEW_ARRIVAL),
        ("记住我喜欢黑色", IntentType.PREFERENCE_UPDATE),
        ("你记得我什么", IntentType.MEMORY_QUERY),
        ("忘掉我的偏好", IntentType.MEMORY_DELETE),
        ("你叫什么", IntentType.SMALL_TALK),
    ],
)
def test_rule_intent_detection(message: str, expected: IntentType) -> None:
    assert detect_rule_intent(message) == expected


def test_multi_intent_and_product_references_are_detected() -> None:
    assert detect_rule_intents("第一件和第三件哪个好，多少钱")[:2] == [
        IntentType.COMPARE_PRODUCTS,
        IntentType.ASK_PRICE,
    ]
    assert resolve_product_references(
        "第一件和第三件哪个好",
        ["A", "B", "C"],
        None,
    ) == ["A", "C"]
    assert resolve_product_references(
        "这几件哪个好",
        ["A", "B", "C"],
        None,
        ["A", "C"],
    ) == ["A", "C"]


def test_short_confirmation_resolves_previous_product_options() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": "你想再了解版型细节，还是让我帮你搭配一下场合穿法？",
        }
    ]

    assert resolve_contextual_follow_up_intents(
        "都要",
        recent_messages,
        "TEST001",
    ) == [IntentType.ASK_STYLE, IntentType.OUTFIT_ADVICE]
    assert resolve_contextual_follow_up_intents("都要", recent_messages, None) == []


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("裤子", ("pants", "clothing")),
        ("鞋子", ("shoes", "footwear")),
    ],
)
def test_short_product_choice_resolves_previous_assistant_question(
    message: str,
    expected: tuple[str, str],
) -> None:
    recent_messages = [
        {"role": "assistant", "content": "接下来想看裤子还是鞋子？"},
    ]

    assert resolve_contextual_product_option(message, recent_messages) == expected
    assert resolve_contextual_product_option("包包", recent_messages) is None


def test_comparison_preference_resolves_to_selected_product() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": (
                "粉色款适合拍出明快、俏皮感，白色长裙更偏随性飘逸。"
                "你更看重的是上镜可爱感，还是穿着的舒适随性呢？"
            ),
        }
    ]
    selected_product_ids = ["PINK_DRESS", "WHITE_DRESS"]

    assert resolve_contextual_comparison_preference(
        "上镜的活泼感",
        recent_messages,
        selected_product_ids,
    ) == "PINK_DRESS"
    assert resolve_contextual_comparison_preference(
        "舒适随性的感觉",
        recent_messages,
        selected_product_ids,
    ) == "WHITE_DRESS"


def test_comparison_preference_uses_recent_comparison_after_failed_follow_up() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": (
                "你更在意的是上镜的活泼感，"
                "还是长裙那种随性飘逸的感觉呢？"
            ),
        },
        {"role": "user", "content": "上镜的活泼感"},
        {"role": "assistant", "content": "我再帮你挑出来看看，你稍等一下。"},
    ]

    assert resolve_contextual_comparison_preference(
        "上镜的活泼感",
        recent_messages,
        ["PINK_DRESS", "WHITE_DRESS"],
    ) == "PINK_DRESS"


def test_comparison_preference_matches_product_description_not_only_question() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": (
                "这两件都是白色系夏季长裙，但风格不同。\n\n"
                "白色蕾丝系带长裙偏精致甜美，上镜氛围更强。\n\n"
                "白色棉麻长裙宽松透气，更注重舒适和预算。\n\n"
                "你更在意的是海边的上镜氛围，还是穿着的舒适方便和预算？"
            ),
        }
    ]

    assert resolve_contextual_comparison_preference(
        "蕾丝那种精致感",
        recent_messages,
        ["LACE_DRESS", "LINEN_DRESS"],
    ) == "LACE_DRESS"


def test_three_product_comparison_preference_uses_ranked_options() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": (
                "第一件是休闲印花T恤，第二件是长袖卫衣，第三件是透视短款吊带。"
                "你更看重的是第一件的海边活动方便舒适，"
                "还是第三件的上镜氛围感？"
            ),
        }
    ]
    selected_product_ids = ["PRINTED_TEE", "SWEATSHIRT", "SHEER_CAMI"]

    assert resolve_contextual_comparison_preference(
        "海边活动方便舒适",
        recent_messages,
        selected_product_ids,
    ) == "PRINTED_TEE"
    assert resolve_contextual_comparison_preference(
        "上镜氛围感",
        recent_messages,
        selected_product_ids,
    ) == "SHEER_CAMI"
    assert resolve_contextual_comparison_preference(
        "第三件",
        recent_messages,
        selected_product_ids,
    ) == "SHEER_CAMI"


def test_four_product_comparison_preference_uses_ranked_options() -> None:
    recent_messages = [
        {
            "role": "assistant",
            "content": (
                "你更在意第二件的预算优势，"
                "还是第四件的复古风格？"
            ),
        }
    ]

    assert resolve_contextual_comparison_preference(
        "预算优势",
        recent_messages,
        ["P1", "P2", "P3", "P4"],
    ) == "P2"
    assert resolve_contextual_comparison_preference(
        "复古风格",
        recent_messages,
        ["P1", "P2", "P3", "P4"],
    ) == "P4"


@pytest.mark.parametrize(
    "message",
    ["你推荐哪件", "你更推荐哪一款？", "那我应该选哪个"],
)
def test_recommendation_question_resolves_current_product(message: str) -> None:
    assert resolve_current_product_recommendation(message, "PINK_DRESS") == "PINK_DRESS"
    assert resolve_current_product_recommendation(message, None) is None


def test_pending_product_options_only_capture_the_two_offered_categories() -> None:
    assistant_message = (
        "这件蓝色棉质背心很适合夏天海边，"
        "你想先看搭配的裤子还是鞋子呢？"
    )
    filters = NormalizedFilters(
        category="top",
        color="blue",
        season="summer",
        occasions=["beach"],
    )

    assert offered_product_options(assistant_message) == [
        ("pants", "clothing"),
        ("shoes", "footwear"),
    ]
    pending_action = infer_pending_action(assistant_message, filters)
    assert pending_action is not None
    assert [option.category for option in pending_action.options] == [
        "pants",
        "shoes",
    ]


def test_short_product_choice_creates_new_recommendation_and_keeps_scene() -> None:
    filters = NormalizedFilters(
        category="top",
        product_scope="clothing",
        subcategory="tank top",
        color="yellow",
        size="M",
        material="cotton",
        season="summer",
        occasions=["beach"],
    )
    assistant_message = "这件黄色背心很适合海边，你想继续看裤子还是鞋子？"
    parsed = parse_shopping_request(
        "裤子",
        12,
        filters,
        "TOP001",
        ["TOP001"],
        [],
        pending_action=infer_pending_action(assistant_message, filters),
    )

    assert parsed.intent_result.intent == IntentType.RECOMMEND_PRODUCT
    assert parsed.intent_result.parser_source == "RULE"
    assert parsed.recommendation_request is not None
    assert parsed.recommendation_request.category == "pants"
    assert parsed.recommendation_request.product_scope == "clothing"
    assert parsed.recommendation_request.season == "summer"
    assert parsed.recommendation_request.occasions == ["beach"]
    assert parsed.recommendation_request.subcategory is None
    assert parsed.recommendation_request.color is None
    assert parsed.recommendation_request.size is None
    assert parsed.recommendation_request.material is None


@pytest.mark.parametrize("message", ["可以", "要", "好的", "试试"])
def test_short_confirmation_accepts_previous_recommendation_offer(
    message: str,
) -> None:
    assistant_message = (
        "这件的材质和库存是演示信息。"
        "要不要我按‘300元以内＋黑色＋适合秋季’再帮你筛一遍？"
    )

    assert resolve_contextual_recommendation_confirmation(
        message,
        [{"role": "assistant", "content": assistant_message}],
    ) == assistant_message


def test_other_product_offer_excludes_current_product() -> None:
    assistant_message = (
        "要不要我再帮你看看有没有其他蓝色上衣可以搭配参考？"
    )
    filters = NormalizedFilters(
        category="top",
        color="blue",
        size="XL",
        occasions=["school"],
    )

    pending_action = infer_pending_action(
        assistant_message,
        filters,
        "TOP001",
    )

    assert pending_action is not None
    assert pending_action.action == PendingActionType.RECOMMEND_PRODUCT
    assert pending_action.filters.excluded_product_ids == ["TOP001"]
    parsed = resolve_pending_action("要", pending_action, limit=12)
    assert parsed is not None
    assert parsed.intent_result.intent == IntentType.RECOMMEND_PRODUCT
    assert parsed.recommendation_request is not None
    assert parsed.recommendation_request.excluded_product_ids == ["TOP001"]
    assert parsed.recommendation_request.category == "top"
    assert parsed.recommendation_request.color == "blue"
    assert parsed.recommendation_request.size == "XL"
    assert parsed.recommendation_request.occasions == ["school"]


def test_slot_operations_update_and_clear_multiturn_state() -> None:
    filters = apply_slot_operations(
        NormalizedFilters(color="black", season="winter"),
        [
            SlotOperation(field="color", operation="SET", value="white"),
            SlotOperation(field="season", operation="CLEAR", value=None),
            SlotOperation(field="occasions", operation="ADD", value=["beach"]),
        ],
    )

    assert filters.color == "white"
    assert filters.season is None
    assert filters.occasions == ["beach"]


def test_beach_clothing_query_gets_scene_scope_and_season_operations() -> None:
    operations = detect_rule_slot_operations("我想找一些适合夏天去海边玩的衣服")
    filters = apply_slot_operations(None, operations)

    assert filters.season == "summer"
    assert filters.product_scope == "clothing"
    assert filters.category is None
    assert filters.occasions == ["beach"]


def test_workwear_follow_up_replaces_previous_occasion() -> None:
    operations = detect_rule_slot_operations("要上班穿的")
    filters = apply_slot_operations(
        NormalizedFilters(occasions=["date"]),
        operations,
    )

    assert filters.occasions == ["commute"]


def test_scene_configuration_drives_prompt_validation_and_aliases() -> None:
    scene_rules = load_scene_rules()

    assert set(scene_rules) == VALID_OCCASION_VALUES == {
        "daily",
        "school",
        "commute",
        "interview",
        "formal",
        "date",
        "party",
        "dining",
        "wedding",
        "ceremony",
        "vacation",
        "beach",
        "shopping",
        "sports",
        "outdoor",
        "home",
        "sleep",
        "photo",
        "performance",
        "festival",
    }
    assert "school" in SYSTEM_PROMPT
    assert "party" in SYSTEM_PROMPT
    for scene, rule in scene_rules.items():
        for alias in rule["aliases"]:
            assert LLM_ENUM_ALIASES["occasions"][str(alias).casefold()] == scene


@pytest.mark.parametrize(
    ("message", "scene"),
    [
        ("平时穿", "daily"),
        ("要上学穿的", "school"),
        ("要上班穿的", "commute"),
        ("面试穿", "interview"),
        ("开会穿", "formal"),
        ("约会穿", "date"),
        ("参加年会", "party"),
        ("朋友聚餐", "dining"),
        ("参加婚礼", "wedding"),
        ("毕业典礼", "ceremony"),
        ("出去旅行", "vacation"),
        ("去海边", "beach"),
        ("周末逛街", "shopping"),
        ("去健身", "sports"),
        ("户外徒步", "outdoor"),
        ("居家穿", "home"),
        ("睡觉穿", "sleep"),
        ("拍写真", "photo"),
        ("上台演出", "performance"),
        ("参加圣诞活动", "festival"),
    ],
)
def test_common_clothing_scenes_are_detected(message: str, scene: str) -> None:
    operations = detect_rule_slot_operations(message)

    assert any(
        operation.field == "occasions" and scene in operation.value
        for operation in operations
    )


def test_scene_follow_ups_replace_previous_scene_unless_explicitly_additive() -> None:
    filters = NormalizedFilters(occasions=["date"])

    filters = apply_slot_operations(filters, detect_rule_slot_operations("要上班穿的"))
    assert filters.occasions == ["commute"]

    filters = apply_slot_operations(filters, detect_rule_slot_operations("要上学穿的"))
    assert filters.occasions == ["school"]

    filters = apply_slot_operations(
        filters,
        detect_rule_slot_operations("还要能参加聚会"),
    )
    assert filters.occasions == ["school", "party"]


def test_unknown_llm_scene_is_ignored_instead_of_reaching_rule_index() -> None:
    operations = parse_llm_slot_operations(
        [
            {
                "field": "occasions",
                "operation": "SET",
                "value": ["unknown-scene"],
            }
        ]
    )

    assert operations == []


def test_unknown_clothing_scene_returns_friendly_http_200(
    monkeypatch,
    tmp_path,
) -> None:
    raw_usage = {
        "prompt_tokens": 1900,
        "completion_tokens": 120,
        "total_tokens": 2020,
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"RECOMMEND_PRODUCT","secondary_intents":[],"confidence":0.91,"clarify_needed":false,"clarify_question":null,"slot_operations":[{"field":"occasions","operation":"SET","value":["formal"]}],"requested_fields":[],"product_references":[],"warnings":[]}'
                        }
                    }
                ],
                "usage": raw_usage,
            }

    env_path = tmp_path / ".env"
    env_path.write_text(
        "STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1\n"
        "STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B\n"
        "STYLEMATE_INTENT_API_KEY=test-stylemate-key\n",
        encoding="utf-8",
    )
    logged: dict[str, object] = {}
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    monkeypatch.setattr("backend.llm_client.httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "backend.llm_client.log_api_usage",
        lambda **kwargs: logged.update(kwargs),
    )
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )

    def unexpected_advisor_call(**kwargs):
        raise AssertionError("unsupported scene reply must be deterministic")

    monkeypatch.setattr("backend.app.generate_advisor_reply", unexpected_advisor_call)

    response = client.post(
        "/api/chat",
        json={
            "message": "想找参加葬礼时穿的衣服",
            "session_id": "unknown-scene-session-001",
            "profile_id": "unknown-scene-profile-001",
            "current_filters": {"occasions": ["commute"]},
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["message"] == UNSUPPORTED_OCCASION_MESSAGE
    assert body["products"] == []
    assert body["filters"]["occasions"] == ["commute"]
    assert body["intent_result"]["clarify_needed"] is True
    assert body["intent_result"]["fulfillment_status"] == "NEED_CLARIFICATION"
    assert body["intent_result"]["warnings"] == [UNSUPPORTED_OCCASION_WARNING]
    assert logged["usage"] is raw_usage


def test_llm_aggregate_category_is_converted_to_product_scope() -> None:
    operations = parse_llm_slot_operations(
        [{"field": "category", "operation": "SET", "value": "clothing"}]
    )
    filters = apply_slot_operations(
        NormalizedFilters(category="top"),
        operations,
    )

    assert filters.category is None
    assert filters.product_scope == "clothing"


def test_unknown_llm_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported product category"):
        parse_llm_slot_operations(
            [{"field": "category", "operation": "SET", "value": "garment"}]
        )


def test_llm_work_occasion_is_normalized_to_commute() -> None:
    operations = parse_llm_slot_operations(
        [{"field": "occasions", "operation": "SET", "value": ["work"]}]
    )

    assert operations == [
        SlotOperation(field="occasions", operation="SET", value=["commute"])
    ]


def test_workwear_follow_up_replaces_date_with_commute(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"REFINE_FILTERS","secondary_intents":[],"confidence":0.93,"clarify_needed":false,"clarify_question":null,"slot_operations":[{"field":"occasions","operation":"SET","value":["work"]}],"requested_fields":[],"product_references":[],"warnings":[]}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 2289,
                    "completion_tokens": 104,
                    "total_tokens": 2393,
                },
            }

    env_path = tmp_path / ".env"
    env_path.write_text(
        "STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1\n"
        "STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B\n"
        "STYLEMATE_INTENT_API_KEY=test-stylemate-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    monkeypatch.setattr("backend.llm_client.httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr("backend.llm_client.log_api_usage", lambda **kwargs: kwargs)

    parsed = parse_shopping_request(
        "要上班穿的",
        limit=6,
        current_filters=NormalizedFilters(
            category="top",
            color="blue",
            size="XL",
            occasions=["date"],
            excluded_colors=["pink"],
        ),
    )

    assert parsed.recommendation_request is not None
    assert parsed.recommendation_request.occasions == ["commute"]
    assert parsed.recommendation_request.category == "top"
    assert parsed.recommendation_request.color == "blue"
    assert parsed.recommendation_request.size == "XL"
    assert parsed.recommendation_request.excluded_colors == ["pink"]


def test_unknown_llm_style_is_rejected_before_ranking() -> None:
    with pytest.raises(ValueError, match="unsupported style_preferences"):
        parse_llm_slot_operations(
            [
                {
                    "field": "style_preferences",
                    "operation": "ADD",
                    "value": ["energetic"],
                }
            ]
        )


def test_null_intent_response_lists_are_normalized_to_empty_lists() -> None:
    parsed = normalize_intent_response_lists(
        {
            "secondary_intents": None,
            "slot_operations": None,
            "requested_fields": None,
            "product_references": None,
            "warnings": None,
        }
    )

    assert parsed == {
        "secondary_intents": [],
        "slot_operations": [],
        "requested_fields": [],
        "product_references": [],
        "warnings": [],
    }


def test_non_array_intent_response_field_is_rejected() -> None:
    with pytest.raises(TypeError, match="warnings must be a list"):
        normalize_intent_response_lists(
            {
                "secondary_intents": [],
                "slot_operations": [],
                "requested_fields": [],
                "product_references": [],
                "warnings": "none",
            }
        )


def test_camera_friendly_style_request_accepts_null_warnings(
    monkeypatch,
    tmp_path,
) -> None:
    raw_usage = {
        "prompt_tokens": 2335,
        "completion_tokens": 126,
        "total_tokens": 2461,
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"REFINE_FILTERS","secondary_intents":[],"confidence":0.9,"clarify_needed":false,"clarify_question":null,"slot_operations":[],"requested_fields":[],"product_references":[],"warnings":null}'
                        }
                    }
                ],
                "usage": raw_usage,
            }

    logged: dict[str, object] = {}
    env_path = tmp_path / ".env"
    env_path.write_text(
        "STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1\n"
        "STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B\n"
        "STYLEMATE_INTENT_API_KEY=test-stylemate-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    monkeypatch.setattr("backend.llm_client.httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "backend.llm_client.log_api_usage",
        lambda **kwargs: logged.update(kwargs),
    )

    parsed = parse_shopping_request(
        "上镜的活泼感",
        limit=6,
        current_filters=NormalizedFilters(
            category="dress",
            product_scope="clothing",
            color_depth="light",
            size="L",
            season="summer",
        ),
    )

    assert parsed.intent_result.intent == IntentType.REFINE_FILTERS
    assert parsed.intent_result.warnings == []
    assert parsed.recommendation_request is not None
    assert parsed.recommendation_request.category == "dress"
    assert parsed.recommendation_request.color_depth == "light"
    assert parsed.recommendation_request.size == "L"
    assert parsed.recommendation_request.season == "summer"
    assert logged["usage"] is raw_usage


def test_beach_clothing_query_clears_llm_aggregate_category(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"primary_intent":"RECOMMEND_PRODUCT","secondary_intents":[],"confidence":0.92,"clarify_needed":false,"clarify_question":null,"slot_operations":[{"field":"category","operation":"SET","value":"clothing"}],"requested_fields":[],"product_references":[],"warnings":[]}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 40,
                    "total_tokens": 140,
                },
            }

    env_path = tmp_path / ".env"
    env_path.write_text(
        "STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1\n"
        "STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B\n"
        "STYLEMATE_INTENT_API_KEY=test-stylemate-key\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.llm_client.DEEPSEEK_ENV_PATH", env_path)
    monkeypatch.setattr("backend.llm_client.httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr("backend.llm_client.log_api_usage", lambda **kwargs: kwargs)

    parsed = parse_shopping_request(
        "我想找一些适合夏天去海边玩的衣服",
        limit=6,
    )

    assert parsed.recommendation_request is not None
    assert parsed.recommendation_request.category is None
    assert parsed.recommendation_request.product_scope == "clothing"
    assert parsed.recommendation_request.season == "summer"
    assert parsed.recommendation_request.occasions == ["beach"]


def test_light_color_request_sets_depth_and_clears_old_color_guesses() -> None:
    operations = detect_rule_slot_operations("浅色")
    filters = apply_slot_operations(
        NormalizedFilters(
            color="blue",
            negative_colors=["black", "brown", "blue"],
            season="summer",
            occasions=["beach"],
        ),
        operations,
    )

    assert filters.color_depth == "light"
    assert filters.color is None
    assert filters.negative_colors == []
    assert filters.season == "summer"
    assert filters.occasions == ["beach"]


def test_duplicate_rule_and_llm_operations_are_removed() -> None:
    operation = SlotOperation(field="occasions", operation="ADD", value=["beach"])

    assert merge_slot_operations([operation], [operation]) == [operation]


@pytest.mark.parametrize(
    "message",
    ["找价格300元以内的上衣", "推荐棉质上衣", "推荐有库存的蓝色衬衫"],
)
def test_filter_phrases_are_not_misclassified_as_fact_questions(message: str) -> None:
    assert detect_rule_intent(message) == IntentType.RECOMMEND_PRODUCT


def test_filter_conflicts_are_reported() -> None:
    conflicts = detect_conflicts(
        NormalizedFilters(
            color="black",
            material="silk",
            brand="Brand A",
            min_price=500,
            max_price=300,
            excluded_colors=["black"],
            excluded_materials=["silk"],
            excluded_brands=["brand a"],
        )
    )

    assert conflicts == [
        "最低价格高于最高价格",
        "同时要求并排除了颜色 black",
        "同时要求并排除了材质 silk",
        "同时要求并排除了品牌 Brand A",
    ]


def test_text_conflict_is_preserved_when_llm_resolves_one_side() -> None:
    assert detect_text_conflicts("我想要黑色，但不要黑色") == [
        "同时要求并排除了颜色 black"
    ]


def test_missing_price_currency_uses_current_catalog_currency() -> None:
    assert detect_price_currency("这件多少钱", None, "USD") == "USD"
    assert detect_price_currency("换成蓝色", None, "CNY") == "CNY"
    assert detect_price_currency("300元以内", "USD", "USD") == "CNY"


def test_fact_question_without_product_context_requests_clarification(
    monkeypatch,
    tmp_path,
) -> None:
    parsed = ParsedShoppingRequest(
        intent_result=IntentResult(
            intent=IntentType.ASK_PRICE,
            slots=NormalizedFilters(),
            confidence=0.98,
            parser_source="RULE_LLM_FUSION",
            clarify_needed=True,
            clarify_question="请告诉我你想询问的具体商品名称或商品ID。",
        ),
        recommendation_request=None,
    )
    monkeypatch.setattr("backend.app.parse_shopping_request", lambda *args: parsed)
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "这件多少钱",
            "session_id": "session-test-002",
            "profile_id": "profile-test-002",
        },
    )

    assert response.status_code == 200
    assert response.json()["products"] == []
    assert response.json()["intent_result"]["intent"] == "ASK_PRICE"
    assert response.json()["intent_result"]["clarify_needed"] is True
    assert response.json()["message"] == parsed.intent_result.clarify_question


def test_selected_product_price_answer_discloses_demo_currency() -> None:
    answer = answer_product_question(
        product(),
        IntentType.ASK_PRICE,
        "这件多少钱",
        NormalizedFilters(),
    )

    assert "演示价格为 $29.99" in answer
    assert "约合 ¥215.93" in answer
    assert "不代表实时汇率" in answer


def test_selected_product_stock_answer_does_not_invent_other_size() -> None:
    answer = answer_product_question(
        product(),
        IntentType.ASK_STOCK,
        "这件有L码吗",
        NormalizedFilters(),
    )

    assert "只标注了 M 码" in answer
    assert "不能确认 L 码库存" in answer


def test_chat_answers_question_for_selected_product(monkeypatch, tmp_path) -> None:
    parsed = ParsedShoppingRequest(
        intent_result=IntentResult(
            intent=IntentType.ASK_MATERIAL,
            slots=NormalizedFilters(),
            confidence=0.98,
            parser_source="RULE_LLM_FUSION",
            clarify_needed=False,
            product_references=["TEST001"],
        ),
        recommendation_request=None,
    )
    monkeypatch.setattr("backend.app.parse_shopping_request", lambda *args: parsed)
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "这件是什么材质",
            "session_id": "session-test-003",
            "profile_id": "profile-test-003",
            "current_product_id": "TEST001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["response_mode"] == "PRODUCT_ANSWER"
    assert body["selected_product_id"] == "TEST001"
    assert body["selected_product_ids"] == ["TEST001"]
    assert [item["parent_asin"] for item in body["products"]] == ["TEST001"]
    assert body["message"] == "「Blue cotton shirt」的商品资料标注材质为：Cotton。"


def test_structured_product_selection_skips_intent_parser(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "backend.app.parse_shopping_request",
        lambda *args: (_ for _ in ()).throw(AssertionError("intent parser must not run")),
    )
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "想了解这件商品",
            "session_id": "session-select-001",
            "profile_id": "profile-select-001",
            "selected_product_ids": ["TEST001"],
            "selection_action": "EXPLAIN",
            "visible_product_ids": ["TEST001", "TEST002"],
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "EXPLAIN_PRODUCT"
    assert body["selected_product_ids"] == ["TEST001"]
    assert body["response_mode"] == "PRODUCT_ANSWER"


def test_structured_multi_selection_compares_products(monkeypatch, tmp_path) -> None:
    products = {
        "TEST001": product(),
        "TEST002": product().model_copy(
            update={"parent_asin": "TEST002", "title": "Black cotton shirt"}
        ),
    }
    monkeypatch.setattr("backend.app.get_product_by_id", products.__getitem__)
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "请帮我比较选中的两件商品",
            "session_id": "session-select-002",
            "profile_id": "profile-select-002",
            "selected_product_ids": ["TEST001", "TEST002"],
            "selection_action": "COMPARE",
            "visible_product_ids": ["TEST001", "TEST002"],
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "COMPARE_PRODUCTS"
    assert body["selected_product_ids"] == ["TEST001", "TEST002"]
    assert body["selected_product_id"] is None
    assert body["response_mode"] == "COMPARISON"
    assert len(body["products"]) == 2
    assert "商品库客观比较" in body["message"]
    assert "不得把评分或评论数说成销量" in body["message"]


def test_contextual_follow_up_answers_all_offered_product_topics(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    record_turn(
        session_id="session-follow-up-001",
        profile_id="profile-follow-up-001",
        user_message="想了解这件商品",
        assistant_message="你想再了解版型细节，还是让我帮你搭配一下场合穿法？",
        filters=NormalizedFilters(category="top", occasions=["beach"]),
        current_product_id="TEST001",
        selected_product_ids=["TEST001"],
        visible_product_ids=["TEST001"],
        pending_action=None,
    )
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "都要",
            "session_id": "session-follow-up-001",
            "profile_id": "profile-follow-up-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "ASK_STYLE"
    assert body["intent_result"]["secondary_intents"] == ["OUTFIT_ADVICE"]
    assert body["selected_product_id"] == "TEST001"
    assert "可匹配的风格" in body["message"]
    assert "搭配建议" in body["message"]


def test_comparison_preference_selects_first_product_without_new_search(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    comparison_reply = (
        "粉色灯笼袖连衣裙适合拍出明快、俏皮感；"
        "白色长裙更偏随性飘逸。"
        "你更在意的是上镜的活泼感，还是长裙那种随性飘逸的感觉呢？"
    )
    filters = NormalizedFilters(
        category="dress",
        product_scope="clothing",
        color_depth="light",
        size="L",
        season="summer",
        occasions=["beach"],
    )
    record_turn(
        session_id="session-comparison-choice-001",
        profile_id="profile-comparison-choice-001",
        user_message="请帮我比较选中的两件商品",
        assistant_message=comparison_reply,
        filters=filters,
        current_product_id=None,
        selected_product_ids=["TEST001", "TEST002"],
        visible_product_ids=["TEST001", "TEST002"],
        pending_action=None,
    )
    products = {
        "TEST001": product().model_copy(
            update={"title": "Pink playful dress", "category": "dress", "color": "pink"}
        ),
        "TEST002": product().model_copy(
            update={"parent_asin": "TEST002", "title": "White boho dress", "category": "dress", "color": "white"}
        ),
    }
    monkeypatch.setattr("backend.app.get_product_by_id", products.__getitem__)
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "上镜的活泼感",
            "session_id": "session-comparison-choice-001",
            "profile_id": "profile-comparison-choice-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "VIEW_PRODUCT"
    assert body["response_mode"] == "PRODUCT_ANSWER"
    assert body["selected_product_id"] == "TEST001"
    assert [item["parent_asin"] for item in body["products"]] == ["TEST001"]
    assert "Pink playful dress" in body["message"]
    assert "稍等" not in body["message"]

    recommendation_response = client.post(
        "/api/chat",
        json={
            "message": "你推荐哪件",
            "session_id": "session-comparison-choice-001",
            "profile_id": "profile-comparison-choice-001",
        },
    )

    recommendation_body = recommendation_response.json()
    assert recommendation_response.status_code == 200
    assert recommendation_body["intent_result"]["intent"] == "EXPLAIN_PRODUCT"
    assert recommendation_body["response_mode"] == "PRODUCT_ANSWER"
    assert recommendation_body["selected_product_id"] == "TEST001"
    assert [item["parent_asin"] for item in recommendation_body["products"]] == [
        "TEST001"
    ]
    assert "Pink playful dress" in recommendation_body["message"]


def test_three_product_comparison_preference_selects_product_without_new_search(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    comparison_reply = (
        "第一件是休闲印花T恤，第二件是长袖卫衣，第三件是透视短款吊带。"
        "综合来看，我推荐第一件，第三件作为备选。"
        "你更看重第一件的海边活动方便舒适，还是第三件的上镜氛围感？"
    )
    filters = NormalizedFilters(
        category="top",
        product_scope="clothing",
        color="blue",
        season="summer",
        occasions=["beach"],
    )
    product_ids = ["TEE001", "SWEAT001", "CAMI001"]
    record_turn(
        session_id="session-three-comparison-001",
        profile_id="profile-three-comparison-001",
        user_message="请帮我比较选中的三件商品",
        assistant_message=comparison_reply,
        filters=filters,
        current_product_id=None,
        selected_product_ids=product_ids,
        visible_product_ids=product_ids,
        pending_action=None,
    )
    products = {
        "TEE001": product().model_copy(
            update={"parent_asin": "TEE001", "title": "Blue printed T-shirt"}
        ),
        "SWEAT001": product().model_copy(
            update={"parent_asin": "SWEAT001", "title": "Blue sweatshirt"}
        ),
        "CAMI001": product().model_copy(
            update={"parent_asin": "CAMI001", "title": "Blue sheer camisole"}
        ),
    }
    monkeypatch.setattr("backend.app.get_product_by_id", products.__getitem__)
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "海边活动方便舒适",
            "session_id": "session-three-comparison-001",
            "profile_id": "profile-three-comparison-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "VIEW_PRODUCT"
    assert body["response_mode"] == "PRODUCT_ANSWER"
    assert body["selected_product_id"] == "TEE001"
    assert [item["parent_asin"] for item in body["products"]] == ["TEE001"]
    assert "Blue printed T-shirt" in body["message"]


def test_contextual_pants_choice_returns_pants_without_repeating_question(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    assistant_message = (
        "这件黄色背心很适合夏天海边，"
        "接下来想搭配裤子还是鞋子？"
    )
    filters = NormalizedFilters(
        category="top",
        product_scope="clothing",
        subcategory="tank top",
        color="yellow",
        season="summer",
        occasions=["beach"],
    )
    record_turn(
        session_id="session-product-choice-001",
        profile_id="profile-product-choice-001",
        user_message="推荐一件适合夏天海边的黄色背心",
        assistant_message=assistant_message,
        filters=filters,
        current_product_id="TOP001",
        selected_product_ids=["TOP001"],
        visible_product_ids=["TOP001"],
        pending_action=infer_pending_action(assistant_message, filters),
    )
    pants = product().model_copy(
        update={
            "parent_asin": "PANTS001",
            "title": "Linen beach pants",
            "category": "pants",
            "color": "white",
        }
    )

    def recommend_pants(request, intent_result):
        assert request.category == "pants"
        assert request.product_scope == "clothing"
        assert request.season == "summer"
        assert request.occasions == ["beach"]
        assert request.color is None
        return RecommendationResponse(
            message="为你找到适合夏季海边场景的裤子。",
            filters=NormalizedFilters(
                category="pants",
                product_scope="clothing",
                season="summer",
                occasions=["beach"],
            ),
            total_matches=1,
            products=[pants],
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            currency_notice="测试币种说明",
        )

    monkeypatch.setattr("backend.app.recommend", recommend_pants)
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "裤子",
            "session_id": "session-product-choice-001",
            "profile_id": "profile-product-choice-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "RECOMMEND_PRODUCT"
    assert body["filters"]["category"] == "pants"
    assert body["filters"]["season"] == "summer"
    assert body["filters"]["occasions"] == ["beach"]
    assert body["filters"]["color"] is None
    assert [item["category"] for item in body["products"]] == ["pants"]
    assert body["message"] == "为你找到适合夏季海边场景的裤子。"
    assert "裤子还是鞋子" not in body["message"]


def test_contextual_other_product_confirmation_returns_a_new_product(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    assistant_message = (
        "这件「blue relaxed fit t-shirt」是蓝色宽松版型的休闲上衣，"
        "上学穿很合适。要不要我再帮你看看有没有其他蓝色上衣可以搭配参考？"
    )
    filters = NormalizedFilters(
        category="top",
        product_scope="clothing",
        color="blue",
        size="XL",
        occasions=["school"],
    )
    record_turn(
        session_id="session-other-top-001",
        profile_id="profile-other-top-001",
        user_message="了解 blue relaxed fit t-shirt",
        assistant_message=assistant_message,
        filters=filters,
        current_product_id="TOP001",
        selected_product_ids=["TOP001"],
        visible_product_ids=["TOP001"],
        pending_action=infer_pending_action(
            assistant_message,
            filters,
            "TOP001",
        ),
    )
    other_top = product().model_copy(
        update={
            "parent_asin": "TOP002",
            "title": "Blue campus blouse",
            "size": "XL",
        }
    )

    def recommend_other_top(request, intent_result):
        assert request.category == "top"
        assert request.color == "blue"
        assert request.size == "XL"
        assert request.occasions == ["school"]
        assert request.excluded_product_ids == ["TOP001"]
        return RecommendationResponse(
            message="已为你换成其他蓝色上衣。",
            filters=NormalizedFilters(
                category="top",
                product_scope="clothing",
                color="blue",
                size="XL",
                occasions=["school"],
                excluded_product_ids=["TOP001"],
            ),
            total_matches=1,
            products=[other_top],
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            currency_notice="测试币种说明",
        )

    monkeypatch.setattr("backend.app.recommend", recommend_other_top)
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "要",
            "session_id": "session-other-top-001",
            "profile_id": "profile-other-top-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "RECOMMEND_PRODUCT"
    assert body["response_mode"] == "RECOMMENDATIONS"
    assert body["filters"]["excluded_product_ids"] == ["TOP001"]
    assert [item["parent_asin"] for item in body["products"]] == ["TOP002"]
    assert body["message"] == "已为你换成其他蓝色上衣。"
    assert "blue relaxed fit t-shirt" not in body["message"]


def test_named_recommendation_product_is_remembered_for_short_detail_confirmation(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    filters = NormalizedFilters(
        category="top",
        product_scope="clothing",
        color="blue",
        size="XL",
        occasions=["school"],
    )
    named_product = product().model_copy(
        update={
            "parent_asin": "TOP-NAMED-001",
            "title": "multicolor basic denim shirt",
            "size": "XL",
            "material": "Denim",
        }
    )
    original_parse = parse_shopping_request

    def parse_request(message, limit, *args, **kwargs):
        if message == "推荐蓝色XL码上衣，上学穿":
            return ParsedShoppingRequest(
                intent_result=IntentResult(
                    intent=IntentType.RECOMMEND_PRODUCT,
                    slots=filters,
                    confidence=1.0,
                    parser_source="RULE",
                ),
                recommendation_request=RecommendationRequest(
                    raw_query=message,
                    **filters.model_dump(),
                    limit=limit,
                ),
            )
        return original_parse(message, limit, *args, **kwargs)

    def recommend_named_product(request, intent_result):
        return RecommendationResponse(
            message="为你找到蓝色上衣。",
            filters=filters,
            total_matches=1,
            products=[named_product],
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            currency_notice="测试币种说明",
        )

    assistant_message = (
        "好呀～这件「multicolor basic denim shirt」是牛仔材质、简约风格，"
        "日常上学穿很合适。要不要我帮你看看这件牛仔衬衫的具体搭配建议？"
    )
    monkeypatch.setattr("backend.app.parse_shopping_request", parse_request)
    monkeypatch.setattr("backend.app.recommend", recommend_named_product)
    monkeypatch.setattr(
        "backend.app.get_product_by_id",
        lambda product_id: named_product if product_id == "TOP-NAMED-001" else None,
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: (
            assistant_message
            if kwargs["base_response"].response_mode == "RECOMMENDATIONS"
            else kwargs["base_response"].message
        ),
    )

    first_response = client.post(
        "/api/chat",
        json={
            "message": "推荐蓝色XL码上衣，上学穿",
            "session_id": "session-named-product-001",
            "profile_id": "profile-named-product-001",
            "selected_product_ids": [],
        },
    )
    assert first_response.status_code == 200
    assert first_response.json()["selected_product_id"] is None

    follow_up_response = client.post(
        "/api/chat",
        json={
            "message": "要",
            "session_id": "session-named-product-001",
            "profile_id": "profile-named-product-001",
            "current_filters": filters.model_dump(mode="json"),
            "current_product_id": None,
            "selected_product_ids": [],
            "visible_product_ids": ["TOP-NAMED-001"],
        },
    )

    body = follow_up_response.json()
    assert follow_up_response.status_code == 200
    assert body["intent_result"]["intent"] == "OUTFIT_ADVICE"
    assert body["response_mode"] == "PRODUCT_ANSWER"
    assert body["selected_product_id"] == "TOP-NAMED-001"
    assert [item["parent_asin"] for item in body["products"]] == [
        "TOP-NAMED-001"
    ]
    assert "搭配" in body["message"]


def test_contextual_confirmation_returns_recommendations_instead_of_product_answer(
    monkeypatch,
    tmp_path,
) -> None:
    memory_path = tmp_path / "conversations.db"
    monkeypatch.setattr(
        "backend.conversation_memory.MEMORY_DATABASE_PATH",
        memory_path,
    )
    assistant_question = (
        "这件连衣裙的材质、价格和库存都是演示信息。"
        "要不要我按‘300元以内＋黑色＋适合秋季’再帮你筛一遍？"
    )
    filters = NormalizedFilters(
        category="dress",
        product_scope="clothing",
        color="black",
        season="autumn",
        max_price=300,
        price_currency="CNY",
    )
    record_turn(
        session_id="session-recommend-confirmation-001",
        profile_id="profile-recommend-confirmation-001",
        user_message="想了解这件连衣裙",
        assistant_message=assistant_question,
        filters=filters,
        current_product_id="DRESS001",
        selected_product_ids=["DRESS001"],
        visible_product_ids=["DRESS001"],
        pending_action=infer_pending_action(assistant_question, filters),
    )
    dress = product().model_copy(
        update={
            "parent_asin": "DRESS002",
            "title": "Black autumn dress",
            "category": "dress",
            "color": "black",
            "season": "autumn",
        }
    )

    def recommend_dresses(request, intent_result):
        assert request.category == "dress"
        assert request.color == "black"
        assert request.season == "autumn"
        assert request.max_price == 300
        assert request.price_currency == "CNY"
        assert request.raw_query == assistant_question
        return RecommendationResponse(
            message="已按300元以内、黑色、秋季重新筛选连衣裙。",
            filters=NormalizedFilters(
                category="dress",
                product_scope="clothing",
                color="black",
                season="autumn",
                max_price=300,
                price_currency="CNY",
            ),
            total_matches=1,
            products=[dress],
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            currency_notice="测试币种说明",
        )

    monkeypatch.setattr("backend.app.recommend", recommend_dresses)
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: kwargs["base_response"].message,
    )

    response = client.post(
        "/api/chat",
        json={
            "message": "可以",
            "session_id": "session-recommend-confirmation-001",
            "profile_id": "profile-recommend-confirmation-001",
        },
    )

    body = response.json()
    assert response.status_code == 200
    assert body["intent_result"]["intent"] == "RECOMMEND_PRODUCT"
    assert body["response_mode"] == "RECOMMENDATIONS"
    assert [item["parent_asin"] for item in body["products"]] == ["DRESS002"]
    assert body["message"] == "已按300元以内、黑色、秋季重新筛选连衣裙。"
    assert "要不要" not in body["message"]


def test_comparison_advisor_prompt_requires_human_sales_guidance() -> None:
    prompt = build_advisor_system_prompt("COMPARISON")

    assert "只输出回复正文" in prompt
    assert "不要输出 JSON" in prompt
    assert "逐件介绍" in prompt
    assert "简短名称" in prompt
    assert "明确的首选" in prompt
    assert "有条件的备选" in prompt
    assert "真正影响选择的偏好问题" in prompt
    assert "第一件的活动方便舒适" in prompt
    assert "第三件的上镜氛围感" in prompt
    assert "不能推断为销量" in prompt
    assert "禁止说“稍等”" in prompt
    assert "四至八句" in prompt


def test_product_answer_prompt_requires_direct_recommendation() -> None:
    prompt = build_advisor_system_prompt("PRODUCT_ANSWER")

    assert "第一句必须明确说“我推荐这件”" in prompt
    assert "不得重新描述为一批筛选结果" in prompt


def test_advisor_reply_uses_plain_text_without_json_parsing() -> None:
    reply = "棕色系带长裙更贴合海边场景。你更在意氛围还是活动方便？"

    assert extract_advisor_reply(
        {"choices": [{"message": {"content": reply}}]}
    ) == reply


def test_comparison_evidence_labels_demo_values_and_catalog_signals() -> None:
    first = product()
    second = product().model_copy(
        update={
            "parent_asin": "TEST002",
            "title": "White linen beach dress",
            "rating": 4.7,
            "rating_count": 250,
            "price": 39.99,
        }
    )

    comparison = compare_products(
        [first, second],
        NormalizedFilters(occasions=["beach"], style_preferences=["gentle"]),
    )

    assert "演示价格 $29.99" in comparison
    assert "演示尺码 M" in comparison
    assert "演示季节 summer" in comparison
    assert "商品库评分 4.7" in comparison
    assert "评论记录最多的是「White linen beach dress」（250 条）" in comparison
    assert "不代表实时平台销量或流行趋势" in comparison


def test_safety_guard_passes_grounded_demo_disclosure() -> None:
    result = check_product_reason(
        product(),
        "品类：top；颜色：blue；材质：Cotton；价格为演示值；库存为演示值。",
    )

    assert result.status == "PASS"
    assert result.supported_claim_ratio == 1.0
    assert all(claim.supported for claim in result.claims)


def test_product_explanation_uses_natural_grounded_reasons_and_claims() -> None:
    item = product()
    reason = build_product_explanation(
        item,
        NormalizedFilters(category="top", color="blue", size="M"),
    )
    result = check_product_reason(item, reason)

    assert "商品库记录的品类为上衣" in reason
    assert "颜色为蓝色" in reason
    assert "商品特征记录为甜美" in reason
    assert "上衣、蓝色条件相符" in reason
    assert "尺码、价格、季节、库存为演示数据" in reason
    assert result.status == "PASS"
    assert result.supported_claim_ratio == 1.0
    assert {claim.type for claim in result.claims} >= {
        "TITLE",
        "CATEGORY",
        "COLOR",
        "MATERIAL",
        "FEATURE",
        "DEMO_SOURCE",
    }


def test_product_explanation_omits_unrecognized_material_values() -> None:
    item = product().model_copy(
        update={
            "material": "Sand.salt.surf.sun",
            "category_source": "amazon_category",
        }
    )

    reason = build_product_explanation(item, NormalizedFilters(category="top"))

    assert "材质字段" not in reason
    assert "商品特征记录" not in reason


def test_safety_guard_rewrites_absolute_effect_claim() -> None:
    result = check_product_reason(product(), "这件绝对显瘦。")

    assert result.status == "REWRITE"
    assert result.safe_text is not None
    assert "价格、季节、库存为演示数据" in result.safe_text
    assert result.violations[0].code == "ABSOLUTE_EFFECT_CLAIM"


def test_safety_guard_blocks_multiple_high_risk_claims() -> None:
    result = check_product_reason(
        product(),
        "这件100%真丝，是全网最低价，只剩最后一件。",
    )

    assert result.status == "BLOCK"
    assert result.blocked is True
    assert result.safe_text is None
    assert {violation.code for violation in result.violations} >= {
        "UNSUPPORTED_MATERIAL",
        "UNSUPPORTED_LOWEST_PRICE",
        "UNSUPPORTED_SCARCITY",
    }
