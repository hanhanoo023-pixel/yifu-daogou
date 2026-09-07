from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend import conversation_memory
from backend.agent_orchestrator import merge_workflow_state, plan_agent_action
from backend.app import app
from backend.llm_client import detect_rule_intent
from backend.schemas import (
    AgentActionType,
    AgentWorkflowState,
    IntentResult,
    IntentType,
    NormalizedFilters,
)


def intent_result(intent: IntentType) -> IntentResult:
    return IntentResult(
        intent=intent,
        slots=NormalizedFilters(),
        confidence=1.0,
        parser_source="RULE",
    )


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("上传我的人物照片", IntentType.UPLOAD_PERSON_IMAGE),
        ("选择一张人物照片", IntentType.SELECT_PERSON_IMAGE),
        ("我想试穿这件", IntentType.START_TRY_ON),
        ("把背景换成海边", IntentType.CHANGE_BACKGROUND),
        ("保存试穿效果图", IntentType.SAVE_RESULT),
        ("联系门店导购", IntentType.CONTACT_SALES),
        ("立即购买这件", IntentType.PURCHASE_PRODUCT),
        ("打开商品详情", IntentType.OPEN_PRODUCT_DETAIL),
        ("进入服装库", IntentType.NAVIGATE_APP),
    ],
)
def test_app_action_intents_are_rule_driven(
    message: str,
    expected: IntentType,
) -> None:
    assert detect_rule_intent(message) == expected


def test_try_on_requests_product_before_person_image() -> None:
    plan = plan_agent_action(
        intent_result=intent_result(IntentType.START_TRY_ON),
        workflow_state=AgentWorkflowState(),
        user_message="我想试穿",
    )

    assert plan.action is not None
    assert plan.action.type == AgentActionType.OPEN_PRODUCT_LIBRARY
    assert plan.workflow_state.pending_workflow == AgentActionType.START_TRY_ON


def test_try_on_requests_person_image_after_product_selection() -> None:
    plan = plan_agent_action(
        intent_result=intent_result(IntentType.START_TRY_ON),
        workflow_state=AgentWorkflowState(selected_product_ids=["P1"]),
        user_message="试穿这件",
    )

    assert plan.action is not None
    assert plan.action.type == AgentActionType.UPLOAD_PERSON_IMAGE
    assert plan.workflow_state.pending_workflow == AgentActionType.START_TRY_ON


def test_try_on_action_contains_selected_person_and_product() -> None:
    plan = plan_agent_action(
        intent_result=intent_result(IntentType.START_TRY_ON),
        workflow_state=AgentWorkflowState(
            person_image_url="https://images.example/person.jpg",
            selected_product_ids=["P1"],
        ),
        user_message="试穿这件",
    )

    assert plan.action is not None
    assert plan.action.type == AgentActionType.START_TRY_ON
    assert plan.action.payload == {
        "person_image_url": "https://images.example/person.jpg",
        "product_ids": ["P1"],
    }


def test_background_and_save_use_latest_try_on_result() -> None:
    state = AgentWorkflowState(
        selected_product_ids=["P1"],
        person_image_url="https://images.example/person.jpg",
        try_on_result_url="https://images.example/result.jpg",
    )
    background = plan_agent_action(
        intent_result=intent_result(IntentType.CHANGE_BACKGROUND),
        workflow_state=state,
        user_message="换成海边背景",
    )
    saved = plan_agent_action(
        intent_result=intent_result(IntentType.SAVE_RESULT),
        workflow_state=state.model_copy(
            update={"background_image_url": "https://images.example/beach.jpg"}
        ),
        user_message="保存结果",
    )

    assert background.action is not None
    assert background.action.type == AgentActionType.CHANGE_BACKGROUND
    assert background.action.payload["source_image_url"] == state.try_on_result_url
    assert saved.action is not None
    assert saved.action.type == AgentActionType.SAVE_RESULT
    assert saved.action.payload["result_url"] == "https://images.example/beach.jpg"


def test_request_workflow_state_updates_server_state() -> None:
    merged = merge_workflow_state(
        AgentWorkflowState(person_image_url="https://images.example/old.jpg"),
        AgentWorkflowState(
            current_page="try_on_result",
            person_image_url="https://images.example/new.jpg",
            try_on_result_url="https://images.example/result.jpg",
            last_completed_action=AgentActionType.START_TRY_ON,
        ),
        ["P1"],
    )

    assert merged.current_page == "try_on_result"
    assert merged.person_image_url == "https://images.example/new.jpg"
    assert merged.try_on_result_url == "https://images.example/result.jpg"
    assert merged.selected_product_ids == ["P1"]


def test_uploaded_person_image_resumes_pending_try_on() -> None:
    plan = plan_agent_action(
        intent_result=intent_result(IntentType.UPLOAD_PERSON_IMAGE),
        workflow_state=AgentWorkflowState(
            current_page="person_images",
            person_image_url="https://images.example/person.jpg",
            selected_product_ids=["P1"],
            pending_workflow=AgentActionType.START_TRY_ON,
            last_completed_action=AgentActionType.UPLOAD_PERSON_IMAGE,
        ),
        user_message="人物照片上传完成",
    )

    assert plan.action is not None
    assert plan.action.type == AgentActionType.START_TRY_ON
    assert plan.workflow_state.last_completed_action is None


def test_chat_returns_try_on_action_without_calling_llm(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        conversation_memory,
        "MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    monkeypatch.setattr(
        "backend.app.generate_advisor_reply",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("app action must not call advisor LLM")
        ),
    )
    response = TestClient(app).post(
        "/api/chat",
        json={
            "message": "试穿这件",
            "session_id": "session-agent-001",
            "profile_id": "profile-agent-001",
            "current_product_id": "P1",
            "selected_product_ids": ["P1"],
            "workflow_state": {
                "current_page": "shopping",
                "person_image_url": "https://images.example/person.jpg",
                "selected_product_ids": ["P1"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent_result"]["intent"] == "START_TRY_ON"
    assert payload["action"]["type"] == "START_TRY_ON"
    assert payload["workflow_state"]["pending_workflow"] == "START_TRY_ON"
