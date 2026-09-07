from __future__ import annotations

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import re
from uuid import uuid4

import httpx
from PIL import Image


TRY_ON_STORAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "tryon"
PERSON_IMAGE_DIR = TRY_ON_STORAGE_DIR / "person"
RESULT_IMAGE_DIR = TRY_ON_STORAGE_DIR / "results"
BACKGROUND_IMAGE_DIR = TRY_ON_STORAGE_DIR / "backgrounds"
TASK_RECORD_DIR = TRY_ON_STORAGE_DIR / "tasks"
MIN_IMAGE_BYTES = 5 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_IMAGE_DIMENSION = 150
MAX_IMAGE_DIMENSION = 4096
SUPPORTED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
SUPPORTED_IMAGE_FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
}
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RESULT_FILENAME_PATTERN = re.compile(
    r"^[A-Za-z0-9_-]{1,128}\.(?:jpg|jpeg|png|webp)$"
)


def public_base_url() -> str:
    value = os.environ["STYLEMATE_PUBLIC_BASE_URL"].strip().rstrip("/")
    if not value.startswith("https://"):
        raise ValueError("STYLEMATE_PUBLIC_BASE_URL must use HTTPS")
    return value


def absolute_public_url(url: str) -> str:
    if url.startswith("https://"):
        return url
    if not url.startswith("/"):
        raise ValueError("image URL must be HTTPS or root-relative")
    return f"{public_base_url()}{url}"


def _validated_image_extension(
    content: bytes,
    declared_content_type: str | None,
) -> tuple[str, int, int]:
    if not MIN_IMAGE_BYTES <= len(content) <= MAX_IMAGE_BYTES:
        raise ValueError("image size must be between 5KB and 5MB")
    if declared_content_type not in SUPPORTED_CONTENT_TYPES:
        raise ValueError("image content type must be JPEG, PNG, or WebP")
    with Image.open(BytesIO(content)) as image:
        image_format = image.format
        width, height = image.size
        image.verify()
    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError("image format must be JPEG, PNG, or WebP")
    if not (
        MIN_IMAGE_DIMENSION <= width <= MAX_IMAGE_DIMENSION
        and MIN_IMAGE_DIMENSION <= height <= MAX_IMAGE_DIMENSION
    ):
        raise ValueError("image dimensions must be between 150 and 4096 pixels")
    return SUPPORTED_IMAGE_FORMATS[image_format], width, height


def save_person_image(
    content: bytes,
    declared_content_type: str | None,
) -> tuple[str, int, int]:
    extension, width, height = _validated_image_extension(
        content,
        declared_content_type,
    )
    PERSON_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{extension}"
    (PERSON_IMAGE_DIR / filename).write_bytes(content)
    return (
        f"{public_base_url()}/api/try-on-files/person/{filename}",
        width,
        height,
    )


def _validated_task_id(task_id: str) -> str:
    if TASK_ID_PATTERN.fullmatch(task_id) is None:
        raise ValueError("invalid task id")
    return task_id


def save_task_record(task_id: str, record: dict[str, object]) -> None:
    safe_task_id = _validated_task_id(task_id)
    TASK_RECORD_DIR.mkdir(parents=True, exist_ok=True)
    path = TASK_RECORD_DIR / f"{safe_task_id}.json"
    path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def task_record_exists(task_id: str) -> bool:
    safe_task_id = _validated_task_id(task_id)
    return (TASK_RECORD_DIR / f"{safe_task_id}.json").exists()


def load_task_record(task_id: str) -> dict[str, object]:
    safe_task_id = _validated_task_id(task_id)
    path = TASK_RECORD_DIR / f"{safe_task_id}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("try-on task record must be an object")
    return value


def result_public_url(task_id: str) -> str | None:
    safe_task_id = _validated_task_id(task_id)
    matches = list(RESULT_IMAGE_DIR.glob(f"{safe_task_id}.*"))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("try-on task has multiple result images")
    return f"{public_base_url()}/api/try-on-files/results/{matches[0].name}"


async def save_remote_result_image(
    task_id: str,
    remote_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.get(remote_url)
    else:
        response = await client.get(remote_url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    extension, _, _ = _validated_image_extension(response.content, content_type)
    RESULT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_task_id = _validated_task_id(task_id)
    filename = f"{safe_task_id}{extension}"
    (RESULT_IMAGE_DIR / filename).write_bytes(response.content)
    return f"{public_base_url()}/api/try-on-files/results/{filename}"


def background_result_public_url(task_id: str) -> str | None:
    safe_task_id = _validated_task_id(task_id)
    matches = list(BACKGROUND_IMAGE_DIR.glob(f"{safe_task_id}.*"))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("background task has multiple result images")
    return f"{public_base_url()}/api/try-on-files/backgrounds/{matches[0].name}"


def background_source_data_url(image_url: str) -> str:
    locations = (
        (
            f"{public_base_url()}/api/try-on-files/results/",
            RESULT_IMAGE_DIR,
        ),
        (
            f"{public_base_url()}/api/try-on-files/backgrounds/",
            BACKGROUND_IMAGE_DIR,
        ),
    )
    for prefix, directory in locations:
        if image_url.startswith(prefix):
            filename = image_url.removeprefix(prefix)
            if RESULT_FILENAME_PATTERN.fullmatch(filename) is None:
                raise ValueError("invalid StyleMate result filename")
            content = (directory / filename).read_bytes()
            with Image.open(BytesIO(content)) as image:
                rgb_image = image.convert("RGB")
                output = BytesIO()
                rgb_image.save(output, format="JPEG", quality=90, optimize=True)
            encoded = base64.b64encode(output.getvalue()).decode("ascii")
            return f"data:image/jpeg;base64,{encoded}"
    raise ValueError("only StyleMate result images can be used")


async def save_remote_background_image(
    task_id: str,
    remote_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str:
    if client is None:
        async with httpx.AsyncClient(timeout=60.0) as owned_client:
            response = await owned_client.get(remote_url)
    else:
        response = await client.get(remote_url)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0]
    extension, _, _ = _validated_image_extension(response.content, content_type)
    BACKGROUND_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    safe_task_id = _validated_task_id(task_id)
    filename = f"{safe_task_id}{extension}"
    (BACKGROUND_IMAGE_DIR / filename).write_bytes(response.content)
    return f"{public_base_url()}/api/try-on-files/backgrounds/{filename}"
