from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from backend import image_storage, saved_results
from backend.aliyun_background_client import (
    ProviderBackgroundTask,
    build_background_payload,
    query_background_task,
    submit_background_task,
)
from backend.app import app


def test_background_payload_uses_beijing_background_model() -> None:
    payload = build_background_payload(
        base_image_url="https://style.example/result.png",
        ref_prompt="海边日落背景",
    )

    assert payload == {
        "model": "wan2.7-image",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"image": "https://style.example/result.png"},
                        {
                            "text": (
                                "只替换图片背景为：海边日落背景。"
                                "完整保留人物、服装、姿态和主体细节。"
                            )
                        },
                    ],
                }
            ]
        },
        "parameters": {"n": 1, "size": "1K", "watermark": False},
    }


def test_background_source_is_encoded_from_saved_image(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    monkeypatch.setattr(image_storage, "RESULT_IMAGE_DIR", tmp_path / "results")
    image_storage.RESULT_IMAGE_DIR.mkdir()
    Image.new("RGB", (300, 400), color="blue").save(
        image_storage.RESULT_IMAGE_DIR / "task-1.png"
    )

    result = image_storage.background_source_data_url(
        "https://style.example/api/try-on-files/results/task-1.png"
    )

    assert result.startswith("data:image/jpeg;base64,")


def test_background_submit_and_poll_preserve_raw_usage(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://workspace.example/api/v1")
    calls: list[dict[str, object]] = []
    logged: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "json": json.loads(request.content) if request.content else None,
            }
        )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_id": "background-1", "task_status": "PENDING"},
                    "usage": {"image_count": 0, "detail": {"queued": 1}},
                },
            )
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "background-1",
                    "task_status": "SUCCEEDED",
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {
                                        "image": "https://result.example/background.png"
                                    }
                                ]
                            }
                        }
                    ],
                },
                "usage": {"image_count": 1, "detail": {"queued": 0}},
            },
        )

    monkeypatch.setattr(
        "backend.aliyun_background_client.log_api_usage",
        lambda **kwargs: logged.append(kwargs) or kwargs,
    )

    async def scenario() -> tuple[ProviderBackgroundTask, ProviderBackgroundTask]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            submitted = await submit_background_task(
                base_image_url="https://style.example/result.png",
                ref_prompt="海边日落背景",
                client=client,
            )
            polled = await query_background_task("background-1", client=client)
            return submitted, polled

    submitted, polled = asyncio.run(scenario())

    assert submitted.status == "PENDING"
    assert polled.status == "SUCCEEDED"
    assert polled.result_image_url == "https://result.example/background.png"
    assert calls[0]["url"] == (
        "https://workspace.example/api/v1/services/aigc/"
        "image-generation/generation"
    )
    assert calls[0]["json"]["model"] == "wan2.7-image"
    assert logged[0]["usage"] == {
        "image_count": 0,
        "detail": {"queued": 1},
    }
    assert logged[1]["usage"] == {
        "image_count": 1,
        "detail": {"queued": 0},
    }


def test_background_endpoints_store_every_api_usage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    monkeypatch.setattr(image_storage, "TASK_RECORD_DIR", tmp_path / "tasks")
    monkeypatch.setattr(image_storage, "BACKGROUND_IMAGE_DIR", tmp_path / "backgrounds")
    submit_usage = {"image_count": 0, "queue": {"position": 1}}
    poll_usage = {"image_count": 1, "queue": {"position": 0}}

    async def fake_submit_background_task(**kwargs) -> ProviderBackgroundTask:
        assert kwargs == {
            "base_image_url": "data:image/png;base64,dGVzdA==",
            "ref_prompt": "海边日落背景",
        }
        return ProviderBackgroundTask(
            task_id="background-1",
            status="PENDING",
            result_image_url=None,
            error_message=None,
            usage=submit_usage,
            usage_record={"call_type": "background_submit", "usage": submit_usage},
        )

    async def fake_query_background_task(task_id: str) -> ProviderBackgroundTask:
        assert task_id == "background-1"
        return ProviderBackgroundTask(
            task_id=task_id,
            status="SUCCEEDED",
            result_image_url="https://result.example/background.png",
            error_message=None,
            usage=poll_usage,
            usage_record={"call_type": "background_poll", "usage": poll_usage},
        )

    async def fake_save_remote_background_image(
        task_id: str,
        remote_url: str,
    ) -> str:
        assert task_id == "background-1"
        assert remote_url == "https://result.example/background.png"
        return "https://style.example/api/try-on-files/backgrounds/background-1.png"

    monkeypatch.setattr(
        "backend.app.submit_background_task",
        fake_submit_background_task,
    )
    monkeypatch.setattr(
        "backend.app.query_background_task",
        fake_query_background_task,
    )
    monkeypatch.setattr(
        "backend.app.save_remote_background_image",
        fake_save_remote_background_image,
    )
    monkeypatch.setattr(
        "backend.app.background_source_data_url",
        lambda image_url: "data:image/png;base64,dGVzdA==",
    )
    client = TestClient(app)
    created = client.post(
        "/api/background/tasks",
        json={
            "session_id": "session-background",
            "profile_id": "profile-background",
            "base_image_url": "https://style.example/api/try-on-files/results/tryon.png",
            "ref_prompt": "海边日落背景",
        },
    )
    completed = client.get(
        "/api/background/tasks/background-1",
        params={
            "session_id": "session-background",
            "profile_id": "profile-background",
        },
    )

    assert created.status_code == 200
    assert created.json()["status"] == "PENDING"
    assert completed.status_code == 200
    assert completed.json()["status"] == "SUCCEEDED"
    record = image_storage.load_task_record("background-1")
    assert record["task_type"] == "background"
    assert record["api_calls"] == [
        {"call_type": "background_submit", "usage": submit_usage},
        {"call_type": "background_poll", "usage": poll_usage},
    ]


def test_saved_result_is_persisted_and_listed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    monkeypatch.setattr(saved_results, "SAVED_RESULT_DIR", tmp_path / "saved")
    client = TestClient(app)
    created = client.post(
        "/api/saved-results",
        json={
            "session_id": "session-saved",
            "profile_id": "profile-saved",
            "image_url": "https://style.example/api/try-on-files/backgrounds/result.png",
            "source_type": "background",
            "product_ids": ["P1"],
            "prompt": "海边日落背景",
        },
    )
    listed = client.get("/api/saved-results/profile-saved")

    assert created.status_code == 200
    assert created.json()["source_type"] == "background"
    assert listed.status_code == 200
    assert listed.json()["items"] == [created.json()]
