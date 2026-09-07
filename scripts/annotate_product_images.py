from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY = PROJECT_ROOT / "configs" / "visual_annotation_taxonomy.yaml"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5.6-terra"

ANNOTATION_FIELDS = (
    "image_status",
    "invalid_reasons",
    "category",
    "subcategory",
    "colors",
    "color_depth",
    "patterns",
    "styles",
    "fits",
    "design_details",
    "materials",
    "occasions",
    "seasons",
    "overall_confidence",
)


class VisionAttemptsExhausted(RuntimeError):
    def __init__(
        self,
        errors: list[str],
        usage_records: list[dict[str, Any]],
    ) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.usage_records = usage_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Annotate product images without exposing existing product labels to the model."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--usage-output", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--image-detail", choices=("high", "original"), default="high")
    parser.add_argument("--request-attempts", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def load_taxonomy(path: Path) -> dict[str, Any]:
    taxonomy = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(taxonomy, dict):
        raise ValueError("visual taxonomy must be a mapping")
    required = {
        "categories",
        "colors",
        "color_roles",
        "color_coverages",
        "color_depths",
        "patterns",
        "styles",
        "fits",
        "design_details",
        "materials",
        "occasions",
        "seasons",
        "quality",
    }
    missing = sorted(required - taxonomy.keys())
    if missing:
        raise ValueError(f"visual taxonomy missing keys: {missing}")
    return taxonomy


def category_values(taxonomy: dict[str, Any]) -> list[str]:
    return sorted(str(value) for value in taxonomy["categories"])


def subcategory_values(taxonomy: dict[str, Any]) -> list[str]:
    return sorted(
        str(subcategory)
        for category in taxonomy["categories"].values()
        for subcategory in category["subcategories"]
    )


def enum_values(taxonomy: dict[str, Any], field: str) -> list[str]:
    values = taxonomy[field]
    if not isinstance(values, dict):
        raise ValueError(f"taxonomy field {field} must be a mapping")
    return sorted(str(value) for value in values)


def evidence_schema(values: list[str], *, nullable: bool = False) -> dict[str, Any]:
    value_schema: dict[str, Any] = {"type": "string", "enum": values}
    if nullable:
        value_schema = {"anyOf": [value_schema, {"type": "null"}]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": value_schema,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string"},
        },
        "required": ["value", "confidence", "evidence"],
    }


def tag_schema(values: list[str]) -> dict[str, Any]:
    return {
        "type": "array",
        "items": evidence_schema(values),
    }


def annotation_schema(taxonomy: dict[str, Any]) -> dict[str, Any]:
    color_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "string", "enum": enum_values(taxonomy, "colors")},
            "role": {
                "type": "string",
                "enum": enum_values(taxonomy, "color_roles"),
            },
            "coverage": {
                "type": "string",
                "enum": enum_values(taxonomy, "color_coverages"),
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string"},
        },
        "required": ["value", "role", "coverage", "confidence", "evidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "image_status": {"type": "string", "enum": ["valid", "invalid"]},
            "invalid_reasons": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": list(taxonomy["quality"]["invalid_image_reasons"]),
                },
            },
            "category": evidence_schema(category_values(taxonomy), nullable=True),
            "subcategory": evidence_schema(subcategory_values(taxonomy), nullable=True),
            "colors": {"type": "array", "items": color_item},
            "color_depth": evidence_schema(
                enum_values(taxonomy, "color_depths"), nullable=True
            ),
            "patterns": tag_schema(enum_values(taxonomy, "patterns")),
            "styles": tag_schema(enum_values(taxonomy, "styles")),
            "fits": tag_schema(enum_values(taxonomy, "fits")),
            "design_details": tag_schema(
                enum_values(taxonomy, "design_details")
            ),
            "materials": tag_schema(enum_values(taxonomy, "materials")),
            "occasions": tag_schema(enum_values(taxonomy, "occasions")),
            "seasons": tag_schema(enum_values(taxonomy, "seasons")),
            "overall_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
        },
        "required": list(ANNOTATION_FIELDS),
    }


def taxonomy_prompt_values(taxonomy: dict[str, Any]) -> dict[str, Any]:
    return {
        "categories": {
            category: list(details["subcategories"])
            for category, details in taxonomy["categories"].items()
        },
        "colors": enum_values(taxonomy, "colors"),
        "color_roles": enum_values(taxonomy, "color_roles"),
        "color_coverages": enum_values(taxonomy, "color_coverages"),
        "color_depths": enum_values(taxonomy, "color_depths"),
        "patterns": enum_values(taxonomy, "patterns"),
        "styles": enum_values(taxonomy, "styles"),
        "fits": enum_values(taxonomy, "fits"),
        "design_details": enum_values(taxonomy, "design_details"),
        "materials": enum_values(taxonomy, "materials"),
        "occasions": enum_values(taxonomy, "occasions"),
        "seasons": enum_values(taxonomy, "seasons"),
        "invalid_image_reasons": list(
            taxonomy["quality"]["invalid_image_reasons"]
        ),
    }


def build_annotation_prompt(
    taxonomy: dict[str, Any],
    retry_feedback: list[str] | None = None,
) -> str:
    prompt = """你是服饰商品图片标注员。只能依据当前图片中服饰商品本身可见的证据标注，不能根据标题、品牌、历史标签或常识猜测。

强制规则：
1. 忽略模特肤色、头发、背景、摄影道具和非目标配饰的颜色。
2. 区分底色、主要配色和点缀色；透明网纱标为 transparent，不能标成肤色。
3. 没有可见证据的图案、风格、版型、设计、材质、场景和季节必须返回空数组。
4. 材质只有在视觉特征足够明确时才允许标注；不确定时为空。
5. 图片不是单一明确服饰商品、严重遮挡或不可辨认时将 image_status 设为 invalid。
6. 只能使用给定枚举，证据必须描述图片中可见区域。
7. 不要输出视觉描述或解释，只返回符合 JSON Schema 的对象。

允许值：
""" + json.dumps(taxonomy_prompt_values(taxonomy), ensure_ascii=False, sort_keys=True)
    if retry_feedback:
        prompt += (
            "\n\n上一次独立审核指出以下问题。请重新查看原图并重新完成全部字段，"
            "不要机械接受审核结论：\n"
            + json.dumps(retry_feedback, ensure_ascii=False)
        )
    return prompt


def extract_output_text(response: dict[str, Any]) -> str:
    for item in response["output"]:
        if item.get("type") != "message":
            continue
        for content in item["content"]:
            if content.get("type") == "output_text":
                return str(content["text"])
    raise ValueError("Responses API result contains no output_text")


def validate_evidence_item(
    item: object,
    allowed_values: set[str],
    *,
    allow_null: bool = False,
) -> None:
    if not isinstance(item, dict) or set(item) != {"value", "confidence", "evidence"}:
        raise ValueError("invalid evidence item structure")
    value = item["value"]
    if value is None and allow_null:
        pass
    elif value not in allowed_values:
        raise ValueError(f"unsupported annotation value: {value}")
    confidence = item["confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("annotation confidence must be between 0 and 1")
    if not isinstance(item["evidence"], str):
        raise ValueError("annotation evidence must be a string")


def validate_tag_list(items: object, allowed_values: set[str]) -> None:
    if not isinstance(items, list):
        raise ValueError("annotation tags must be a list")
    seen: set[str] = set()
    for item in items:
        validate_evidence_item(item, allowed_values)
        value = str(item["value"])
        if value in seen:
            raise ValueError(f"duplicate annotation value: {value}")
        seen.add(value)


def validate_annotation(annotation: object, taxonomy: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(annotation, dict):
        raise ValueError("annotation must be an object")
    allowed_fields = set(ANNOTATION_FIELDS) | {"visual_description"}
    if not set(ANNOTATION_FIELDS).issubset(annotation) or not set(annotation).issubset(
        allowed_fields
    ):
        raise ValueError("annotation fields do not match the required schema")
    if "visual_description" in annotation and annotation["visual_description"] is not None:
        if not isinstance(annotation["visual_description"], str):
            raise ValueError("visual_description must be a string or null")
    if annotation["image_status"] not in {"valid", "invalid"}:
        raise ValueError("invalid image_status")
    if not isinstance(annotation["invalid_reasons"], list) or not set(
        annotation["invalid_reasons"]
    ).issubset(set(taxonomy["quality"]["invalid_image_reasons"])):
        raise ValueError("invalid image reasons")
    validate_evidence_item(
        annotation["category"], set(category_values(taxonomy)), allow_null=True
    )
    validate_evidence_item(
        annotation["subcategory"], set(subcategory_values(taxonomy)), allow_null=True
    )
    colors = annotation["colors"]
    if not isinstance(colors, list):
        raise ValueError("colors must be a list")
    seen_colors: set[str] = set()
    for color in colors:
        if not isinstance(color, dict) or set(color) != {
            "value",
            "role",
            "coverage",
            "confidence",
            "evidence",
        }:
            raise ValueError("invalid color structure")
        if color["value"] not in taxonomy["colors"]:
            raise ValueError(f"unsupported color: {color['value']}")
        if color["role"] not in taxonomy["color_roles"]:
            raise ValueError(f"unsupported color role: {color['role']}")
        if color["coverage"] not in taxonomy["color_coverages"]:
            raise ValueError(f"unsupported color coverage: {color['coverage']}")
        if color["value"] in seen_colors:
            raise ValueError(f"duplicate color: {color['value']}")
        seen_colors.add(str(color["value"]))
        confidence = color["confidence"]
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("color confidence must be between 0 and 1")
        if not isinstance(color["evidence"], str):
            raise ValueError("color evidence must be a string")
    validate_evidence_item(
        annotation["color_depth"],
        set(enum_values(taxonomy, "color_depths")),
        allow_null=True,
    )
    for field, taxonomy_field in (
        ("patterns", "patterns"),
        ("styles", "styles"),
        ("fits", "fits"),
        ("design_details", "design_details"),
        ("materials", "materials"),
        ("occasions", "occasions"),
        ("seasons", "seasons"),
    ):
        validate_tag_list(annotation[field], set(enum_values(taxonomy, taxonomy_field)))
    overall_confidence = annotation["overall_confidence"]
    if not isinstance(overall_confidence, (int, float)) or not 0 <= overall_confidence <= 1:
        raise ValueError("overall_confidence must be between 0 and 1")
    if annotation["image_status"] == "valid":
        if annotation["invalid_reasons"]:
            raise ValueError("valid image cannot have invalid_reasons")
        if annotation["category"]["value"] is None:
            raise ValueError("valid image requires a category")
        if annotation["subcategory"]["value"] is None:
            raise ValueError("valid image requires a subcategory")
        if not annotation["colors"]:
            raise ValueError("valid image requires at least one color")
        if annotation["color_depth"]["value"] is None:
            raise ValueError("valid image requires color_depth")
    elif not annotation["invalid_reasons"]:
        raise ValueError("invalid image requires at least one reason")
    return annotation


def display_label(taxonomy: dict[str, Any], field: str, value: str) -> str:
    if field == "category":
        return str(taxonomy["categories"][value]["label"])
    if field == "subcategory":
        for category in taxonomy["categories"].values():
            if value in category["subcategories"]:
                return str(category["subcategories"][value])
        raise ValueError(f"unknown subcategory: {value}")
    return str(taxonomy[field][value])


def build_visual_description(
    annotation: dict[str, Any], taxonomy: dict[str, Any]
) -> str | None:
    if annotation["image_status"] != "valid":
        return None
    colors = "、".join(
        f"{display_label(taxonomy, 'colors', str(item['value']))}"
        f"（{display_label(taxonomy, 'color_roles', str(item['role']))}）"
        for item in annotation["colors"]
    )
    parts = [colors]
    for field, taxonomy_field in (
        ("patterns", "patterns"),
        ("styles", "styles"),
        ("fits", "fits"),
        ("design_details", "design_details"),
    ):
        labels = [
            display_label(taxonomy, taxonomy_field, str(item["value"]))
            for item in annotation[field]
        ]
        if labels:
            parts.append("、".join(labels))
    parts.append(
        display_label(taxonomy, "subcategory", str(annotation["subcategory"]["value"]))
    )
    return "，".join(part for part in parts if part)


def local_image_data_url(image_url: str) -> str:
    prefix = "/api/fashion200k-images/"
    if not image_url.startswith(prefix):
        return image_url
    image_path = PROJECT_ROOT / "data" / "fashion200k-images" / image_url.removeprefix(prefix)
    data = image_path.read_bytes()
    mime_type = mimetypes.guess_type(image_path.name)[0]
    if mime_type is None or not mime_type.startswith("image/"):
        raise ValueError(f"unsupported image type: {image_path}")
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def usage_record(
    *,
    request_index: int,
    call_type: str,
    call_type_index: int,
    model: str,
    prefix: str,
    elapsed_seconds: float,
    usage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "request_index": request_index,
        "call_type": call_type,
        "call_type_index": call_type_index,
        "model": model,
        "prefix": prefix,
        "elapsed_seconds": elapsed_seconds,
        "usage": usage,
    }


class ResponsesVisionClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        usage_output: Path,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.usage_output = usage_output
        self.usage_output.parent.mkdir(parents=True, exist_ok=True)
        self.client = http_client or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120,
        )
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def request(
        self,
        *,
        image_url: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        model: str,
        image_detail: str,
        request_index: int,
        call_type: str,
        call_type_index: int,
        prefix: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.perf_counter()
        response = self.client.post(
            "/responses",
            json={
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {
                                "type": "input_image",
                                "image_url": local_image_data_url(image_url),
                                "detail": image_detail,
                            },
                        ],
                    }
                ],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
            },
        )
        response.raise_for_status()
        payload = response.json()
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ValueError("Responses API result does not contain raw usage")
        record = usage_record(
            request_index=request_index,
            call_type=call_type,
            call_type_index=call_type_index,
            model=model,
            prefix=prefix,
            elapsed_seconds=round(time.perf_counter() - started, 3),
            usage=usage,
        )
        serialized = json.dumps(record, ensure_ascii=False, sort_keys=True)
        print(serialized, flush=True)
        with self.usage_output.open("a", encoding="utf-8", newline="\n") as target:
            target.write(serialized + "\n")
        return payload, record


def request_annotation_with_retry(
    client: ResponsesVisionClient,
    *,
    image_url: str,
    taxonomy: dict[str, Any],
    model: str,
    image_detail: str,
    request_index: int,
    request_attempts: int,
    retry_feedback: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    usage_records: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt in range(1, request_attempts + 1):
        try:
            response, usage = client.request(
                image_url=image_url,
                prompt=build_annotation_prompt(taxonomy, retry_feedback),
                schema_name="product_visual_annotation",
                schema=annotation_schema(taxonomy),
                model=model,
                image_detail=image_detail,
                request_index=request_index,
                call_type="visual_annotation",
                call_type_index=attempt,
                prefix="image_grounded_product_annotation",
            )
            usage_records.append(usage)
            annotation = json.loads(extract_output_text(response))
            return validate_annotation(annotation, taxonomy), usage_records, errors
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status not in {408, 409, 429, 500, 502, 503, 504}:
                raise
            errors.append(f"attempt {attempt}: HTTP {status}")
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"attempt {attempt}: {type(error).__name__}: {error}")
        if attempt < request_attempts:
            sleep(float(2 ** (attempt - 1)))
    raise VisionAttemptsExhausted(errors, usage_records)


def read_jsonl(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            rows.append(json.loads(line))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def main() -> None:
    args = parse_args()
    for path in (args.output, args.usage_output):
        if path.exists():
            raise FileExistsError(path)
    if args.request_attempts < 1:
        raise ValueError("request-attempts must be positive")
    api_key = os.getenv(args.api_key_env)
    if not api_key:
        raise RuntimeError(f"missing API key environment variable: {args.api_key_env}")

    taxonomy = load_taxonomy(args.taxonomy)
    candidates = read_jsonl(args.input, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    client = ResponsesVisionClient(
        api_key=api_key,
        base_url=args.base_url,
        usage_output=args.usage_output,
    )
    try:
        with args.output.open("w", encoding="utf-8", newline="\n") as target:
            for request_index, candidate in enumerate(candidates, start=1):
                api_calls: list[dict[str, Any]] = []
                errors: list[str] = []
                try:
                    annotation, api_calls, errors = request_annotation_with_retry(
                        client,
                        image_url=str(candidate["image_url"]),
                        taxonomy=taxonomy,
                        model=args.model,
                        image_detail=args.image_detail,
                        request_index=request_index,
                        request_attempts=args.request_attempts,
                    )
                    status = "ANNOTATED"
                    annotation["visual_description"] = build_visual_description(
                        annotation, taxonomy
                    )
                except VisionAttemptsExhausted as error:
                    status = "ERROR"
                    annotation = None
                    errors = error.errors
                    api_calls = error.usage_records
                target.write(
                    json.dumps(
                        {
                            "product_id": candidate["product_id"],
                            "candidate": candidate,
                            "status": status,
                            "annotation": annotation,
                            "errors": errors,
                            "api_calls": api_calls,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n"
                )
                target.flush()
    finally:
        client.close()


if __name__ == "__main__":
    main()
