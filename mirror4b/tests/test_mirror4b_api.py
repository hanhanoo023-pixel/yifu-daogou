from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from backend import conversation_memory, demo_commerce
from backend.mirror4b_adapter import (
    build_product_catalog,
    recommendation_response_from_agent,
)
from backend.mirror4b_client import chat_with_agent, sync_products
from backend.schemas import (
    AgentWorkflowState,
    Mirror4BAgentChatRequest,
    Mirror4BAgentChatResponse,
    Mirror4BProduct,
    NormalizedFilters,
)


PRODUCT_PAYLOAD = {
    "sku": "M4B-001",
    "name": "通勤西装",
    "main_image_url": "https://images.example/blazer.jpg",
    "category_data": {"category": "西装"},
    "feature_data": {"material": "羊毛混纺"},
    "sales_data": {"price": 699.0, "total_stock": 6, "status": "在售"},
    "id": 101,
    "merchant_id": 7,
    "sync_status": True,
    "stocks": [{"id": 1, "product_id": 101, "size": "M", "quantity": 6}],
}


AGENT_PAYLOAD = {
    "status": "SUCCESS",
    "message": {
        "text": "为你推荐一件通勤西装。",
        "action": None,
        "intent": "recommend_clothes",
        "sales_stage": "recommendation",
        "recommended_products": [PRODUCT_PAYLOAD],
        "recommended_outfits": None,
        "recommendation_method": "deterministic",
        "debug_note": None,
        "quick_replies": ["试穿这件"],
    },
    "usage": {
        "prompt_tokens": 18,
        "completion_tokens": 12,
        "provider_detail": {"cached": False},
    },
}


@pytest.fixture(autouse=True)
def configure_mirror4b_currency(monkeypatch) -> None:
    monkeypatch.setenv("MIRROR4B_CURRENCY", "CNY")


def test_currency_defaults_to_cny_when_environment_is_missing(monkeypatch) -> None:
    from backend.mirror4b_adapter import mirror4b_currency

    monkeypatch.delenv("MIRROR4B_CURRENCY", raising=False)

    assert mirror4b_currency() == "CNY"
    assert NormalizedFilters().price_currency == "CNY"


def test_mirror4b_client_uses_server_side_token_and_preserves_usage(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MIRROR4B_BASE_URL", "https://mirror4b.example")
    monkeypatch.setenv("MIRROR4B_MERCHANT_TOKEN", "merchant-test-token")
    calls: list[dict[str, object]] = []
    logged: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers["Authorization"],
                "json": json.loads(request.content) if request.content else None,
            }
        )
        return httpx.Response(200, json=AGENT_PAYLOAD)

    monkeypatch.setattr(
        "backend.mirror4b_client.log_api_usage",
        lambda **kwargs: logged.append(kwargs) or kwargs,
    )

    async def scenario() -> Mirror4BAgentChatResponse:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await chat_with_agent(
                Mirror4BAgentChatRequest(text="推荐通勤西装"),
                client=client,
            )

    result = asyncio.run(scenario())

    assert calls == [
        {
            "method": "POST",
            "url": "https://mirror4b.example/mirror4b/chat/agent",
            "authorization": "Bearer merchant-test-token",
            "json": {
                "text": "推荐通勤西装",
                "excluded_product_ids": [],
                "context": {
                    "has_person_photo": False,
                    "has_result_image": False,
                    "has_running_task": False,
                    "recommended_products": [],
                    "recommended_outfits": [],
                },
            },
        }
    ]
    assert result.message.intent == "recommend_clothes"
    assert result.usage == AGENT_PAYLOAD["usage"]
    assert logged[0]["usage"] == AGENT_PAYLOAD["usage"]


def test_product_sync_validates_products_and_reports_missing_usage(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("MIRROR4B_BASE_URL", "https://mirror4b.example")
    monkeypatch.setenv("MIRROR4B_MERCHANT_TOKEN", "merchant-test-token")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer merchant-test-token"
        return httpx.Response(200, json=[PRODUCT_PAYLOAD])

    async def scenario() -> list[Mirror4BProduct]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await sync_products(client=client)

    products = asyncio.run(scenario())
    usage_log = json.loads(capsys.readouterr().out)

    assert products[0].id == 101
    assert products[0].sales_data["price"] == 699.0
    assert usage_log["call_type"] == "mirror4b_sync_products"
    assert usage_log["usage"] is None
    assert usage_log["usage_status"] == "missing_from_upstream_response"


def test_mirror4b_product_and_agent_response_map_to_existing_ui_contract() -> None:
    catalog = build_product_catalog(
        [Mirror4BProduct.model_validate(PRODUCT_PAYLOAD)]
    )
    response = recommendation_response_from_agent(
        response=Mirror4BAgentChatResponse.model_validate(AGENT_PAYLOAD),
        catalog=catalog,
        filters=NormalizedFilters(),
        workflow_state=AgentWorkflowState(),
        selected_product_ids=[],
    )

    product = response.products[0]
    assert product.parent_asin == "101"
    assert product.title == "通勤西装"
    assert product.price == 699.0
    assert product.currency == "CNY"
    assert product.stock_quantity == 6
    assert product.business_sizes == ["M"]
    assert response.response_mode == "RECOMMENDATIONS"
    assert response.currency_notice == ""


def test_mirror4b_proxy_endpoints_delegate_without_exposing_token(
    monkeypatch,
) -> None:
    from backend.app import app

    async def fake_sync_products() -> list[Mirror4BProduct]:
        return [Mirror4BProduct.model_validate(PRODUCT_PAYLOAD)]

    async def fake_chat(
        request: Mirror4BAgentChatRequest,
    ) -> Mirror4BAgentChatResponse:
        assert request.text == "推荐通勤西装"
        return Mirror4BAgentChatResponse.model_validate(AGENT_PAYLOAD)

    monkeypatch.setattr("backend.app.mirror4b_sync_products", fake_sync_products)
    monkeypatch.setattr("backend.app.mirror4b_chat_with_agent", fake_chat)
    client = TestClient(app)

    health = client.get("/api/health")
    detail = client.get("/api/products/101")
    products = client.get("/api/mirror4b/products")
    agent = client.post(
        "/api/mirror4b/chat/agent",
        json={"text": "推荐通勤西装"},
    )

    assert health.status_code == 200
    assert health.json()["product_count"] == 1
    assert detail.status_code == 200
    assert detail.json()["product"]["stock_quantity"] == 6
    assert "Mirror4B" in detail.json()["data_notice"]
    assert products.status_code == 200
    assert products.json()[0]["sku"] == "M4B-001"
    assert agent.status_code == 200
    assert agent.json()["message"]["intent"] == "recommend_clothes"
    assert "merchant-test-token" not in products.text
    assert "merchant-test-token" not in agent.text


def test_stylemate_runtime_chat_uses_mirror4b_catalog_and_agent(
    monkeypatch,
    tmp_path,
) -> None:
    from backend.app import app

    monkeypatch.setattr(
        conversation_memory,
        "MEMORY_DATABASE_PATH",
        tmp_path / "conversations.db",
    )
    product = Mirror4BProduct.model_validate(PRODUCT_PAYLOAD)

    async def fake_sync_products() -> list[Mirror4BProduct]:
        return [product]

    async def fake_chat(
        request: Mirror4BAgentChatRequest,
    ) -> Mirror4BAgentChatResponse:
        assert request.text == "推荐一件通勤西装"
        assert request.current_page == "/chat"
        assert request.context.has_person_photo is False
        return Mirror4BAgentChatResponse.model_validate(AGENT_PAYLOAD)

    monkeypatch.setattr("backend.app.mirror4b_sync_products", fake_sync_products)
    monkeypatch.setattr("backend.app.mirror4b_chat_with_agent", fake_chat)
    response = TestClient(app).post(
        "/api/chat",
        json={
            "message": "推荐一件通勤西装",
            "session_id": "session-mirror4b-001",
            "profile_id": "profile-mirror4b-001",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["response_mode"] == "RECOMMENDATIONS"
    assert payload["products"][0]["parent_asin"] == "101"
    assert payload["products"][0]["stock_source"] == "mirror4b_sales_data"
    assert payload["memory_status"]["recent_turn_count"] == 1


def test_cart_uses_mirror4b_size_price_and_stock(monkeypatch, tmp_path) -> None:
    from backend.app import app

    monkeypatch.setattr(
        demo_commerce,
        "DEMO_COMMERCE_DIR",
        tmp_path / "demo-commerce",
    )
    product = Mirror4BProduct.model_validate(PRODUCT_PAYLOAD)

    async def fake_sync_products() -> list[Mirror4BProduct]:
        return [product]

    monkeypatch.setattr("backend.app.mirror4b_sync_products", fake_sync_products)
    response = TestClient(app).post(
        "/api/cart/profile-mirror4b-001/items",
        json={"product_id": "101", "selected_size": "M", "quantity": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["product"]["price"] == 699.0
    assert payload["items"][0]["product"]["stock_quantity"] == 6
    assert payload["items"][0]["selected_size"] == "M"
    assert payload["items"][0]["quantity"] == 2
    assert payload["total"] == 1398.0
    assert payload["currency"] == "CNY"
    assert "Mirror4B" in payload["demo_notice"]
