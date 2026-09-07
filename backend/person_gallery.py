from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from uuid import uuid4

from backend.image_storage import PERSON_IMAGE_DIR
from backend.schemas import PersonImageRecord


PERSON_GALLERY_DIR = PERSON_IMAGE_DIR.parent / "person-galleries"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
IMAGE_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")
IMAGE_FILENAME_PATTERN = re.compile(r"^[a-f0-9]{32}\.(?:jpg|jpeg|png|webp)$")


def _profile_path(profile_id: str) -> Path:
    if PROFILE_ID_PATTERN.fullmatch(profile_id) is None:
        raise ValueError("invalid profile id")
    return PERSON_GALLERY_DIR / f"{profile_id}.json"


def _write_gallery(profile_id: str, items: list[PersonImageRecord]) -> None:
    path = _profile_path(profile_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in items],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def list_person_images(profile_id: str) -> list[PersonImageRecord]:
    path = _profile_path(profile_id)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("person gallery file must contain a list")
    return [PersonImageRecord.model_validate(item) for item in payload]


def record_person_image(
    profile_id: str,
    image_url: str,
    width: int,
    height: int,
) -> PersonImageRecord:
    item = PersonImageRecord(
        id=uuid4().hex,
        profile_id=profile_id,
        image_url=image_url,
        width=width,
        height=height,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    items = list_person_images(profile_id)
    items.insert(0, item)
    _write_gallery(profile_id, items)
    return item


def delete_person_image(profile_id: str, image_id: str) -> bool:
    if IMAGE_ID_PATTERN.fullmatch(image_id) is None:
        raise ValueError("invalid person image id")
    items = list_person_images(profile_id)
    target = next((item for item in items if item.id == image_id), None)
    if target is None:
        return False
    filename = Path(urlparse(target.image_url).path).name
    if IMAGE_FILENAME_PATTERN.fullmatch(filename) is None:
        raise ValueError("invalid person image filename")
    (PERSON_IMAGE_DIR / filename).unlink()
    _write_gallery(
        profile_id,
        [item for item in items if item.id != image_id],
    )
    return True
