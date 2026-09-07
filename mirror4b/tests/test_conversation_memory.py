from pathlib import Path

import pytest

from backend import conversation_memory
from backend.conversation_memory import (
    apply_remembered_preferences,
    delete_profile_preferences,
    delete_session,
    load_conversation_context,
    record_turn,
    remember_explicit_preferences,
)
from backend.dialogue_response import generate_advisor_reply, validate_advisor_reply
from backend.schemas import (
    AgentActionType,
    AgentWorkflowState,
    IntentResult,
    IntentType,
    NormalizedFilters,
    PendingAction,
    PendingActionType,
    RecommendationResponse,
    SlotOperation,
)


def use_temporary_memory_database(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "conversations.db"
    monkeypatch.setattr(conversation_memory, "MEMORY_DATABASE_PATH", path)
    return path


def test_three_layer_memory_records_turn_state_and_explicit_preferences(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = use_temporary_memory_database(monkeypatch, tmp_path)
    filters = NormalizedFilters(color="black", size="L")
    pending_action = PendingAction(
        action=PendingActionType.RECOMMEND_PRODUCT,
        filters=filters,
        source_assistant_message="要不要继续按这些条件筛选？",
    )
    workflow_state = AgentWorkflowState(
        current_page="try_on",
        person_image_url="https://images.example/person.jpg",
        selected_product_ids=["P1"],
        pending_workflow=AgentActionType.START_TRY_ON,
    )
    status = record_turn(
        session_id="session-123",
        profile_id="profile-123",
        user_message="我喜欢黑色L码",
        assistant_message="好呀，我记下了。",
        filters=filters,
        current_product_id="P1",
        selected_product_ids=["P1", "P2"],
        visible_product_ids=["P1", "P2"],
        pending_action=pending_action,
        workflow_state=workflow_state,
    )
    preferences = remember_explicit_preferences(
        profile_id="profile-123",
        operations=[
            SlotOperation(field="color", operation="SET", value="black"),
            SlotOperation(field="size", operation="SET", value="L"),
        ],
        source_message="记住我喜欢黑色L码",
    )
    context = load_conversation_context("session-123", "profile-123")

    assert database_path.exists()
    assert status.recent_turn_count == 1
    assert context.recent_messages == [
        {"role": "user", "content": "我喜欢黑色L码"},
        {"role": "assistant", "content": "好呀，我记下了。"},
    ]
    assert context.working_filters == filters
    assert context.current_product_id == "P1"
    assert context.selected_product_ids == ["P1", "P2"]
    assert context.visible_product_ids == ["P1", "P2"]
    assert context.pending_action == pending_action
    assert context.workflow_state == workflow_state
    assert preferences == {"color": "black", "size": "L"}
    assert context.remembered_preferences == preferences
    assert apply_remembered_preferences(None, preferences) == NormalizedFilters(
        color="black",
        size="L",
    )
    assert apply_remembered_preferences(
        NormalizedFilters(color="blue"),
        preferences,
    ) == NormalizedFilters(color="blue", size="L")

    merged_preferences = remember_explicit_preferences(
        profile_id="profile-123",
        operations=[
            SlotOperation(
                field="negative_colors",
                operation="ADD",
                value=["pink"],
            ),
            SlotOperation(
                field="negative_colors",
                operation="ADD",
                value=["red"],
            ),
        ],
        source_message="以后不要粉色和红色",
    )
    assert merged_preferences["negative_colors"] == ["pink", "red"]

    delete_profile_preferences("profile-123")
    assert load_conversation_context(
        "session-123",
        "profile-123",
    ).remembered_preferences == {}
    delete_session("session-123", "profile-123")
    assert load_conversation_context("session-123", "profile-123").turn_count == 0


def test_session_cannot_be_reused_by_another_profile(monkeypatch, tmp_path: Path) -> None:
    use_temporary_memory_database(monkeypatch, tmp_path)
    record_turn(
        session_id="session-123",
        profile_id="profile-123",
        user_message="你好",
        assistant_message="你好呀",
        filters=NormalizedFilters(),
        current_product_id=None,
        selected_product_ids=[],
        visible_product_ids=[],
        pending_action=None,
    )

    with pytest.raises(ValueError, match="does not belong"):
        load_conversation_context("session-123", "profile-999")


def test_advisor_reply_is_natural_and_raw_usage_is_logged(monkeypatch) -> None:
    raw_usage = {
        "prompt_tokens": 50,
        "completion_tokens": 20,
        "total_tokens": 70,
        "prompt_tokens_details": {"cached_tokens": 0},
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": '{"reply":"好呀～按你说的黑色和300元预算，我已经把更接近的款式放在右边了。你想先看哪一件？"}'
                        }
                    }
                ],
                "usage": raw_usage,
            }

    logged: dict[str, object] = {}
    monkeypatch.setattr("backend.dialogue_response.read_deepseek_api_key", lambda: "key")
    monkeypatch.setattr("backend.dialogue_response.httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr(
        "backend.dialogue_response.log_api_usage",
        lambda **kwargs: logged.update(kwargs),
    )
    response = RecommendationResponse(
        message="初步筛选得到 224 件商品，经相关性检查后展示前 12 件。",
        filters=NormalizedFilters(category="dress", color="black", max_price=300),
        total_matches=224,
        products=[],
        filter_stages=[],
        no_result_reason=None,
        intent_result=IntentResult(
            intent=IntentType.RECOMMEND_PRODUCT,
            slots=NormalizedFilters(category="dress", color="black", max_price=300),
            confidence=0.98,
            parser_source="LLM",
        ),
        currency_notice="demo",
    )

    reply = generate_advisor_reply(
        user_message="找一条300元以内的黑色连衣裙",
        base_response=response,
        recent_messages=[],
        remembered_preferences={},
    )

    assert "初步筛选" not in reply
    assert "我已经把更接近的款式放在右边了" in reply
    assert logged["call_type"] == "advisor_reply"
    assert logged["usage"] is raw_usage


def test_advisor_reply_rejects_unsupported_absolute_claim() -> None:
    response = RecommendationResponse(
        message="",
        filters=NormalizedFilters(),
        total_matches=0,
        products=[],
        filter_stages=[],
        no_result_reason=None,
        currency_notice="demo",
    )

    with pytest.raises(ValueError, match="prohibited claim"):
        validate_advisor_reply("这件一定适合你。", response)
