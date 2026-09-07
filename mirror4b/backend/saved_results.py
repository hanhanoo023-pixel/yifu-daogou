from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from uuid import uuid4

from backend.image_storage import TRY_ON_STORAGE_DIR, public_base_url
from backend.schemas import SavedResult, SavedResultCreateRequest


SAVED_RESULT_DIR = TRY_ON_STORAGE_DIR / "saved"
PROFILE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _profile_path(profile_id: str) -> Path:
    if PROFILE_ID_PATTERN.fullmatch(profile_id) is None:
        raise ValueError("invalid profile id")
    return SAVED_RESULT_DIR / f"{profile_id}.json"


def list_saved_results(profile_id: str) -> list[SavedResult]:
    path = _profile_path(profile_id)
    if not path.exists():
        return []
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise TypeError("saved result file must contain a list")
    return [SavedResult.model_validate(value) for value in values]


def save_result(request: SavedResultCreateRequest) -> SavedResult:
    expected_prefix = f"{public_base_url()}/api/try-on-files/"
    if not request.image_url.startswith(expected_prefix):
        raise ValueError("only StyleMate result images can be saved")
    item = SavedResult(
        id=uuid4().hex,
        session_id=request.session_id,
        profile_id=request.profile_id,
        image_url=request.image_url,
        source_type=request.source_type,
        product_ids=request.product_ids,
        prompt=request.prompt,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    items = list_saved_results(request.profile_id)
    items.insert(0, item)
    SAVED_RESULT_DIR.mkdir(parents=True, exist_ok=True)
    _profile_path(request.profile_id).write_text(
        json.dumps(
            [value.model_dump() for value in items[:50]],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return item
