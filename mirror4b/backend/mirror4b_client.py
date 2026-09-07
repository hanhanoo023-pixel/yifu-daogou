from __future__ import annotations

import json
import os
import time

import httpx

from backend.schemas import (
    Mirror4BAgentChatRequest,
    Mirror4BAgentChatResponse,
    Mirror4BProduct,
)
from backend.usage_logger import log_api_usage


MODEL_NAME = "mirror4b-agent"


def _base_url() -> str:
    value = os.environ["MIRROR4B_BASE_URL"].strip().rstrip("/")
    if not value:
        raise ValueError("MIRROR4B_BASE_URL cannot be empty")
    return value


def _merchant_token() -> str:
    value = os.environ["MIRROR4B_MERCHANT_TOKEN"].strip()
    if not value:
        raise ValueError("MIRROR4B_MERCHANT_TOKEN cannot be empty")
    return value


def _authorization_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_merchant_token()}"}


def _log_response_usage(
    *,
    payload: object,
    request_index: int,
    call_type: str,
    started_at: float,
) -> None:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    elapsed_seconds = round(time.monotonic() - started_at, 3)
    if usage is None:
        print(
            json.dumps(
                {
                    "request_index": request_index,
                    "call_type": call_type,
                    "call_type_index": 1,
                    "model": MODEL_NAME,
                    "prefix": call_type,
                    "elapsed_seconds": elapsed_seconds,
                    "usage": None,
                    "usage_status": "missing_from_upstream_response",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return
    if not isinstance(usage, dict):
        raise TypeError("Mirror4B usage must be an object")
    log_api_usage(
        request_index=request_index,
        call_type=call_type,
        call_type_index=1,
        model=MODEL_NAME,
        prefix=call_type,
        elapsed_seconds=elapsed_seconds,
        usage=usage,
    )


async def sync_products(
    *,
    client: httpx.AsyncClient | None = None,
) -> list[Mirror4BProduct]:
    request_index = time.time_ns()
    started_at = time.monotonic()
    if client is None:
        async with httpx.AsyncClient(timeout=30.0) as owned_client:
            response = await owned_client.get(
                f"{_base_url()}/mirror4b/sync/products",
                headers=_authorization_headers(),
            )
    else:
        response = await client.get(
            f"{_base_url()}/mirror4b/sync/products",
            headers=_authorization_headers(),
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise TypeError("Mirror4B product sync response must be an array")
    _log_response_usage(
        payload=payload,
        request_index=request_index,
        call_type="mirror4b_sync_products",
        started_at=started_at,
    )
    return [Mirror4BProduct.model_validate(item) for item in payload]


async def chat_with_agent(
    request: Mirror4BAgentChatRequest,
    *,
    client: httpx.AsyncClient | None = None,
) -> Mirror4BAgentChatResponse:
    request_index = time.time_ns()
    started_at = time.monotonic()
    request_payload = request.model_dump(exclude_none=True)
    headers = {
        **_authorization_headers(),
        "Content-Type": "application/json",
    }
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.post(
                f"{_base_url()}/mirror4b/chat/agent",
                headers=headers,
                json=request_payload,
            )
    else:
        response = await client.post(
            f"{_base_url()}/mirror4b/chat/agent",
            headers=headers,
            json=request_payload,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Mirror4B Agent response must be an object")
    _log_response_usage(
        payload=payload,
        request_index=request_index,
        call_type="mirror4b_agent_chat",
        started_at=started_at,
    )
    return Mirror4BAgentChatResponse.model_validate(payload)
