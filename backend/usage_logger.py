from __future__ import annotations

import json
from pathlib import Path


USAGE_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "api_usage.jsonl"


def log_api_usage(
    *,
    request_index: int,
    call_type: str,
    call_type_index: int,
    model: str,
    prefix: str,
    elapsed_seconds: float,
    usage: dict[str, object],
) -> dict[str, object]:
    record = {
        "request_index": request_index,
        "call_type": call_type,
        "call_type_index": call_type_index,
        "model": model,
        "prefix": prefix,
        "elapsed_seconds": elapsed_seconds,
        "usage": usage,
    }
    serialized = json.dumps(record, ensure_ascii=False)
    print(serialized, flush=True)
    USAGE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(serialized + "\n")
    return record
