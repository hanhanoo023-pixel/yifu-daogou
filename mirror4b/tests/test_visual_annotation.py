from __future__ import annotations

import json
from pathlib import Path

from scripts.annotate_product_images import (
    DEFAULT_TAXONOMY,
    build_annotation_prompt,
    build_visual_description,
    load_taxonomy,
    request_annotation_with_retry,
    validate_annotation,
)
from scripts.audit_product_visual_labels import (
    build_audit_prompt,
    validate_audit,
)


def valid_annotation() -> dict[str, object]:
    return {
        "image_status": "valid",
        "invalid_reasons": [],
        "category": {
            "value": "top",
            "confidence": 0.99,
            "evidence": "图片主体是一件无袖上衣",
        },
        "subcategory": {
            "value": "tank_top",
            "confidence": 0.98,
            "evidence": "无袖背心轮廓",
        },
        "colors": [
            {
                "value": "red",
                "role": "major",
                "coverage": "major",
                "confidence": 0.96,
                "evidence": "衣身有大面积红色条纹",
            },
            {
                "value": "white",
                "role": "major",
                "coverage": "major",
                "confidence": 0.97,
                "evidence": "衣身有大面积白色条纹",
            },
            {
                "value": "navy",
                "role": "major",
                "coverage": "major",
                "confidence": 0.95,
                "evidence": "胸前旗帜区域为藏蓝色",
            },
        ],
        "color_depth": {
            "value": "medium",
            "confidence": 0.91,
            "evidence": "浅色与深色面积接近",
        },
        "patterns": [
            {"value": "flag", "confidence": 0.98, "evidence": "可见旗帜图案"},
            {"value": "stars", "confidence": 0.97, "evidence": "可见星星"},
            {"value": "striped", "confidence": 0.97, "evidence": "可见条纹"},
        ],
        "styles": [
            {"value": "streetwear", "confidence": 0.85, "evidence": "做旧流苏设计"}
        ],
        "fits": [
            {"value": "slim", "confidence": 0.81, "evidence": "贴合身体轮廓"}
        ],
        "design_details": [
            {"value": "fringe", "confidence": 0.97, "evidence": "下摆为流苏"},
            {"value": "distressed", "confidence": 0.91, "evidence": "图案有做旧效果"},
        ],
        "materials": [],
        "occasions": [],
        "seasons": [],
        "overall_confidence": 0.93,
    }


class FakeClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls = 0

    def request(self, **_: object) -> tuple[dict[str, object], dict[str, object]]:
        response = self.responses[self.calls]
        self.calls += 1
        return response, {
            "request_index": 1,
            "call_type": "visual_annotation",
            "call_type_index": self.calls,
            "model": "test-model",
            "prefix": "image_grounded_product_annotation",
            "elapsed_seconds": 0.1,
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_tokens_details": {"image_tokens": 4},
            },
        }


def response_with_text(value: object) -> dict[str, object]:
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        ]
    }


def test_visual_annotation_uses_image_only_and_builds_deterministic_description() -> None:
    taxonomy = load_taxonomy(DEFAULT_TAXONOMY)
    annotation = validate_annotation(valid_annotation(), taxonomy)
    prompt = build_annotation_prompt(taxonomy)
    description = build_visual_description(annotation, taxonomy)

    assert "只能依据当前图片" in prompt
    assert "忽略模特肤色" in prompt
    assert "标题" in prompt
    assert description == (
        "红色（主要配色）、白色（主要配色）、藏蓝色（主要配色），"
        "旗帜、星星、条纹，街头，修身，流苏、做旧，背心"
    )


def test_invalid_annotation_is_retried_and_all_raw_usage_is_preserved() -> None:
    taxonomy = load_taxonomy(DEFAULT_TAXONOMY)
    invalid = valid_annotation()
    invalid["colors"] = [
        {
            "value": "skin_tone",
            "role": "base",
            "coverage": "dominant",
            "confidence": 0.9,
            "evidence": "错误地使用了肤色",
        }
    ]
    client = FakeClient([response_with_text(invalid), response_with_text(valid_annotation())])

    annotation, usage_records, errors = request_annotation_with_retry(
        client,  # type: ignore[arg-type]
        image_url="https://example.com/product.jpg",
        taxonomy=taxonomy,
        model="test-model",
        image_detail="high",
        request_index=1,
        request_attempts=3,
        sleep=lambda _: None,
    )

    assert annotation["colors"][0]["value"] == "red"
    assert len(usage_records) == 2
    assert usage_records[0]["usage"] == {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "input_tokens_details": {"image_tokens": 4},
    }
    assert errors == ["attempt 1: ValueError: unsupported color: skin_tone"]


def test_independent_audit_requires_failure_reasons_for_retry() -> None:
    annotation = valid_annotation()
    prompt = build_audit_prompt(annotation, 0.75, 0.85)
    assert "重新查看原始商品图片" in prompt
    assert "不得因为候选已经给出就默认同意" in prompt

    audit = validate_audit(
        {
            "status": "RETRY",
            "overall_confidence": 0.62,
            "field_checks": [
                {"field": "colors", "status": "FAIL", "evidence": "藏蓝色来自背景"}
            ],
            "failure_reasons": ["颜色角色错误"],
            "retry_instructions": ["忽略背景并重新识别衣身颜色"],
        }
    )
    assert audit["status"] == "RETRY"
