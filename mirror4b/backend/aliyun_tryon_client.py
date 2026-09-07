from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from backend.usage_logger import log_api_usage


MODEL_NAME = "aitryon"
TOP_GARMENT_CATEGORIES = {"top", "dress", "outerwear", "sweater"}
BOTTOM_GARMENT_CATEGORIES = {"pants", "shorts", "skirt"}
TERMINAL_TASK_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


@dataclass(frozen=True)
class ProviderTask:
    task_id: str
    status: str
    result_image_url: str | None
    error_message: str | None
    usage: dict[str, object]
    usage_record: dict[str, object]


def _api_key() -> str:
    value = os.environ["DASHSCOPE_API_KEY"].strip()
    if not value:
        raise ValueError("DASHSCOPE_API_KEY cannot be empty")
    return value


def _base_url() -> str:
    value = os.environ["DASHSCOPE_BASE_URL"].strip().rstrip("/")
    if not value:
        raise ValueError("DASHSCOPE_BASE_URL cannot be empty")
    return value


def build_try_on_payload(
    *,
    person_image_url: str,
    garment_image_url: str,
    category: str,
) -> dict[str, object]:
    input_payload: dict[str, str] = {"person_image_url": person_image_url}
    if category in TOP_GARMENT_CATEGORIES:
        input_payload["top_garment_url"] = garment_image_url
    elif category in BOTTOM_GARMENT_CATEGORIES:
        input_payload["bottom_garment_url"] = garment_image_url
    else:
        raise ValueError(f"unsupported try-on category: {category}")
    return {
        "model": MODEL_NAME,
        "input": input_payload,
        "parameters": {
            "resolution": -1,
            "restore_face": True,
        },
    }


def _provider_task(
    payload: dict[str, object],
    usage_record: dict[str, object],
) -> ProviderTask:
    output = payload["output"]
    if not isinstance(output, dict):
        raise TypeError("DashScope output must be an object")
    task_id = output["task_id"]
    task_status = output["task_status"]
    if not isinstance(task_id, str) or not isinstance(task_status, str):
        raise TypeError("DashScope task_id and task_status must be strings")
    result_image_url = output.get("image_url")
    if result_image_url is not None and not isinstance(result_image_url, str):
        raise TypeError("DashScope image_url must be a string")
    error_message = payload.get("message")
    if error_message is not None and not isinstance(error_message, str):
        raise TypeError("DashScope message must be a string")
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise TypeError("DashScope usage must be an object")
    return ProviderTask(
        task_id=task_id,
        status=task_status,
        result_image_url=result_image_url,
        error_message=error_message,
        usage=usage,
        usage_record=usage_record,
    )


def _log_provider_usage(
    *,
    payload: dict[str, object],
    request_index: int,
    call_type: str,
    started_at: float,
) -> dict[str, object]:
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise TypeError("DashScope usage must be an object")
    return log_api_usage(
        request_index=request_index,
        call_type=call_type,
        call_type_index=1,
        model=MODEL_NAME,
        prefix=call_type,
        elapsed_seconds=round(time.monotonic() - started_at, 3),
        usage=usage,
    )


async def submit_try_on_task(
    *,
    person_image_url: str,
    garment_image_url: str,
    category: str,
    client: httpx.AsyncClient | None = None,
) -> ProviderTask:
    payload = build_try_on_payload(
        person_image_url=person_image_url,
        garment_image_url=garment_image_url,
        category=category,
    )
    request_index = time.time_ns()
    started_at = time.monotonic()
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.post(
                f"{_base_url()}/services/aigc/image2image/image-synthesis",
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                },
                json=payload,
            )
    else:
        response = await client.post(
            f"{_base_url()}/services/aigc/image2image/image-synthesis",
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
            json=payload,
        )
    response.raise_for_status()
    response_payload = response.json()
    if not isinstance(response_payload, dict):
        raise TypeError("DashScope response must be an object")
    usage_record = _log_provider_usage(
        payload=response_payload,
        request_index=request_index,
        call_type="aitryon_submit",
        started_at=started_at,
    )
    return _provider_task(response_payload, usage_record)


async def query_try_on_task(
    task_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProviderTask:
    request_index = time.time_ns()
    started_at = time.monotonic()
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.get(
                f"{_base_url()}/tasks/{task_id}",
                headers={"Authorization": f"Bearer {_api_key()}"},
            )
    else:
        response = await client.get(
            f"{_base_url()}/tasks/{task_id}",
            headers={"Authorization": f"Bearer {_api_key()}"},
        )
    response.raise_for_status()
    response_payload = response.json()
    if not isinstance(response_payload, dict):
        raise TypeError("DashScope response must be an object")
    usage_record = _log_provider_usage(
        payload=response_payload,
        request_index=request_index,
        call_type="aitryon_poll",
        started_at=started_at,
    )
    return _provider_task(response_payload, usage_record)
