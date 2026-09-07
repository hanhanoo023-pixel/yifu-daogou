from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

from backend.usage_logger import log_api_usage


MODEL_NAME = "wan2.7-image"


@dataclass(frozen=True)
class ProviderBackgroundTask:
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


def build_background_payload(
    *,
    base_image_url: str,
    ref_prompt: str,
) -> dict[str, object]:
    return {
        "model": MODEL_NAME,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": base_image_url},
                        {
                            "text": (
                                f"只替换图片背景为：{ref_prompt}。"
                                "完整保留人物、服装、姿态和主体细节。"
                            )
                        },
                    ],
                }
            ]
        },
        "parameters": {
            "n": 1,
            "size": "1K",
            "watermark": False,
        },
    }


def _log_usage(
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


def _provider_task(
    payload: dict[str, object],
    usage_record: dict[str, object],
) -> ProviderBackgroundTask:
    output = payload["output"]
    if not isinstance(output, dict):
        raise TypeError("DashScope output must be an object")
    task_id = output["task_id"]
    task_status = output["task_status"]
    if not isinstance(task_id, str) or not isinstance(task_status, str):
        raise TypeError("DashScope task_id and task_status must be strings")
    result_image_url = None
    if task_status == "SUCCEEDED":
        choices = output["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise TypeError("DashScope choices must contain one result")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise TypeError("DashScope choice must be an object")
        message = first_choice["message"]
        if not isinstance(message, dict):
            raise TypeError("DashScope message must be an object")
        content = message["content"]
        if not isinstance(content, list) or len(content) != 1:
            raise TypeError("DashScope content must contain one image")
        image = content[0]
        if not isinstance(image, dict):
            raise TypeError("DashScope image content must be an object")
        result_image_url = image["image"]
        if not isinstance(result_image_url, str):
            raise TypeError("DashScope result image must be a string")
    error_message = payload.get("message")
    if error_message is not None and not isinstance(error_message, str):
        raise TypeError("DashScope message must be a string")
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        raise TypeError("DashScope usage must be an object")
    return ProviderBackgroundTask(
        task_id=task_id,
        status=task_status,
        result_image_url=result_image_url,
        error_message=error_message,
        usage=usage,
        usage_record=usage_record,
    )


async def submit_background_task(
    *,
    base_image_url: str,
    ref_prompt: str,
    client: httpx.AsyncClient | None = None,
) -> ProviderBackgroundTask:
    payload = build_background_payload(
        base_image_url=base_image_url,
        ref_prompt=ref_prompt,
    )
    request_index = time.time_ns()
    started_at = time.monotonic()
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.post(
                f"{_base_url()}/services/aigc/image-generation/generation",
                headers={
                    "Authorization": f"Bearer {_api_key()}",
                    "Content-Type": "application/json",
                    "X-DashScope-Async": "enable",
                },
                json=payload,
            )
    else:
        response = await client.post(
            f"{_base_url()}/services/aigc/image-generation/generation",
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
    usage_record = _log_usage(
        payload=response_payload,
        request_index=request_index,
        call_type="background_submit",
        started_at=started_at,
    )
    return _provider_task(response_payload, usage_record)


async def query_background_task(
    task_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> ProviderBackgroundTask:
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
    usage_record = _log_usage(
        payload=response_payload,
        request_index=request_index,
        call_type="background_poll",
        started_at=started_at,
    )
    return _provider_task(response_payload, usage_record)
