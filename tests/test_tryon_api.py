from __future__ import annotations

import asyncio
from io import BytesIO
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from backend import image_storage, person_gallery
from backend.aliyun_tryon_client import (
    ProviderTask,
    build_try_on_payload,
    query_try_on_task,
    submit_try_on_task,
)
from backend.app import app
from backend.schemas import ProductCard


def product(category: str = "top") -> ProductCard:
    return ProductCard(
        parent_asin="P1",
        title="Blue cotton top",
        brand="StyleMate",
        category=category,
        color="blue",
        size="L",
        season="summer",
        material="cotton",
        price=29.0,
        image_url="https://images.example/garment.jpg",
        rating=4.8,
        rating_count=20,
        stock_quantity=5,
        price_source="catalog",
        size_source="catalog",
        color_source="catalog",
        season_source="catalog",
        stock_source="catalog",
    )


def test_build_try_on_payload_maps_supported_garment_slots() -> None:
    top = build_try_on_payload(
        person_image_url="https://style.example/person.jpg",
        garment_image_url="https://style.example/top.jpg",
        category="dress",
    )
    bottom = build_try_on_payload(
        person_image_url="https://style.example/person.jpg",
        garment_image_url="https://style.example/pants.jpg",
        category="pants",
    )

    assert top["model"] == "aitryon"
    assert top["input"] == {
        "person_image_url": "https://style.example/person.jpg",
        "top_garment_url": "https://style.example/top.jpg",
    }
    assert bottom["input"] == {
        "person_image_url": "https://style.example/person.jpg",
        "bottom_garment_url": "https://style.example/pants.jpg",
    }


def test_submit_and_poll_preserve_raw_usage(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://workspace.example/api/v1")
    calls: list[dict[str, object]] = []
    logged: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "authorization": request.headers["Authorization"],
                "async_header": request.headers.get("X-DashScope-Async"),
                "json": json.loads(request.content) if request.content else None,
            }
        )
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "output": {"task_id": "task-1", "task_status": "PENDING"},
                    "usage": {"image_count": 0, "provider_detail": {"queued": 1}},
                },
            )
        return httpx.Response(
            200,
            json={
                "output": {
                    "task_id": "task-1",
                    "task_status": "SUCCEEDED",
                    "image_url": "https://result.example/task-1.png",
                },
                "usage": {"image_count": 1, "provider_detail": {"queued": 0}},
            },
        )

    monkeypatch.setattr(
        "backend.aliyun_tryon_client.log_api_usage",
        lambda **kwargs: logged.append(kwargs) or kwargs,
    )

    async def scenario() -> tuple[ProviderTask, ProviderTask]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            submitted = await submit_try_on_task(
                person_image_url="https://style.example/person.jpg",
                garment_image_url="https://style.example/top.jpg",
                category="top",
                client=client,
            )
            polled = await query_try_on_task("task-1", client=client)
            return submitted, polled

    submitted, polled = asyncio.run(scenario())

    assert submitted.status == "PENDING"
    assert polled.status == "SUCCEEDED"
    assert polled.result_image_url == "https://result.example/task-1.png"
    assert calls[0]["url"] == (
        "https://workspace.example/api/v1/services/aigc/"
        "image2image/image-synthesis"
    )
    assert calls[0]["authorization"] == "Bearer test-key"
    assert calls[0]["async_header"] == "enable"
    assert calls[0]["json"]["model"] == "aitryon"
    assert logged[0]["usage"] == {
        "image_count": 0,
        "provider_detail": {"queued": 1},
    }
    assert logged[1]["usage"] == {
        "image_count": 1,
        "provider_detail": {"queued": 0},
    }


def test_person_image_upload_validates_and_stores_image(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(image_storage, "PERSON_IMAGE_DIR", tmp_path / "person")
    monkeypatch.setattr(person_gallery, "PERSON_IMAGE_DIR", tmp_path / "person")
    monkeypatch.setattr(
        person_gallery,
        "PERSON_GALLERY_DIR",
        tmp_path / "person-galleries",
    )
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    image = Image.effect_noise((300, 300), 100).convert("RGB")
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=90)

    response = TestClient(app).post(
        "/api/try-on/person-image?profile_id=profile-person-001",
        content=buffer.getvalue(),
        headers={"Content-Type": "image/jpeg"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["image_url"].startswith(
        "https://style.example/api/try-on-files/person/"
    )
    assert payload["width"] == 300
    assert payload["height"] == 300
    assert len(list((tmp_path / "person").iterdir())) == 1

    listed = TestClient(app).get("/api/person-images/profile-person-001")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    saved = listed.json()["items"][0]
    assert saved["image_url"] == payload["image_url"]

    deleted = TestClient(app).delete(
        f"/api/person-images/profile-person-001/{saved['id']}"
    )
    assert deleted.status_code == 200
    assert list((tmp_path / "person").iterdir()) == []
    assert TestClient(app).get(
        "/api/person-images/profile-person-001"
    ).json()["items"] == []


def test_try_on_endpoints_store_every_api_usage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    monkeypatch.setattr(image_storage, "TASK_RECORD_DIR", tmp_path / "tasks")
    monkeypatch.setattr(image_storage, "RESULT_IMAGE_DIR", tmp_path / "results")
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())

    submit_usage = {"image_count": 0, "queue": {"position": 1}}
    poll_usage = {"image_count": 1, "queue": {"position": 0}}

    async def fake_submit_try_on_task(**kwargs) -> ProviderTask:
        assert kwargs == {
            "person_image_url": "https://style.example/api/try-on-files/person/person.jpg",
            "garment_image_url": "https://images.example/garment.jpg",
            "category": "top",
        }
        return ProviderTask(
            task_id="task-1",
            status="PENDING",
            result_image_url=None,
            error_message=None,
            usage=submit_usage,
            usage_record={"call_type": "aitryon_submit", "usage": submit_usage},
        )

    async def fake_query_try_on_task(task_id: str) -> ProviderTask:
        assert task_id == "task-1"
        return ProviderTask(
            task_id=task_id,
            status="SUCCEEDED",
            result_image_url="https://result.example/task-1.png",
            error_message=None,
            usage=poll_usage,
            usage_record={"call_type": "aitryon_poll", "usage": poll_usage},
        )

    async def fake_save_remote_result_image(
        task_id: str,
        remote_url: str,
    ) -> str:
        assert task_id == "task-1"
        assert remote_url == "https://result.example/task-1.png"
        return "https://style.example/api/try-on-files/results/task-1.png"

    monkeypatch.setattr("backend.app.submit_try_on_task", fake_submit_try_on_task)
    monkeypatch.setattr("backend.app.query_try_on_task", fake_query_try_on_task)
    monkeypatch.setattr(
        "backend.app.save_remote_result_image",
        fake_save_remote_result_image,
    )
    client = TestClient(app)
    created = client.post(
        "/api/try-on/tasks",
        json={
            "session_id": "session-1",
            "profile_id": "profile-1",
            "product_id": "P1",
            "person_image_url": "https://style.example/api/try-on-files/person/person.jpg",
        },
    )
    completed = client.get(
        "/api/try-on/tasks/task-1",
        params={"session_id": "session-1", "profile_id": "profile-1"},
    )

    assert created.status_code == 200
    assert created.json()["status"] == "PENDING"
    assert completed.status_code == 200
    assert completed.json()["status"] == "SUCCEEDED"
    assert completed.json()["result_url"].endswith("/task-1.png")
    record = image_storage.load_task_record("task-1")
    assert record["api_calls"] == [
        {"call_type": "aitryon_submit", "usage": submit_usage},
        {"call_type": "aitryon_poll", "usage": poll_usage},
    ]


def test_try_on_rejects_unsupported_product_category(monkeypatch) -> None:
    monkeypatch.setenv("STYLEMATE_PUBLIC_BASE_URL", "https://style.example")
    monkeypatch.setattr(
        "backend.app.get_product_by_id",
        lambda product_id: product("shoes"),
    )
    response = TestClient(app).post(
        "/api/try-on/tasks",
        json={
            "session_id": "session-1",
            "profile_id": "profile-1",
            "product_id": "P1",
            "person_image_url": "https://style.example/api/try-on-files/person/person.jpg",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "基础版 AI 试衣仅支持上装、下装和连衣裙"
