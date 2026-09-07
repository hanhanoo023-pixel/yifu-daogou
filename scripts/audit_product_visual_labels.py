from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable

import httpx

from scripts.annotate_product_images import (
    DEFAULT_BASE_URL,
    DEFAULT_TAXONOMY,
    ResponsesVisionClient,
    VisionAttemptsExhausted,
    build_visual_description,
    extract_output_text,
    load_taxonomy,
    request_annotation_with_retry,
    validate_annotation,
)


DEFAULT_ANNOTATION_MODEL = "gpt-5.6-terra"
DEFAULT_AUDIT_MODEL = "gpt-5.6-sol"
AUDIT_FIELDS = (
    "status",
    "overall_confidence",
    "field_checks",
    "failure_reasons",
    "retry_instructions",
)


class AuditAttemptsExhausted(RuntimeError):
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
        description="Independently audit image-grounded product labels and retry conflicts."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--usage-output", type=Path, required=True)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--annotation-model", default=DEFAULT_ANNOTATION_MODEL)
    parser.add_argument("--audit-model", default=DEFAULT_AUDIT_MODEL)
    parser.add_argument("--base-url", default=os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--request-attempts", type=int, default=3)
    parser.add_argument("--maximum-audit-rounds", type=int, default=None)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def audit_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["PASS", "RETRY", "FAIL"]},
            "overall_confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "field_checks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "field": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["PASS", "FAIL"],
                        },
                        "evidence": {"type": "string"},
                    },
                    "required": ["field", "status", "evidence"],
                },
            },
            "failure_reasons": {
                "type": "array",
                "items": {"type": "string"},
            },
            "retry_instructions": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": list(AUDIT_FIELDS),
    }


def build_audit_prompt(
    annotation: dict[str, Any],
    minimum_annotation_confidence: float,
    minimum_audit_confidence: float,
) -> str:
    return """你是独立的服饰图片质量审核员。重新查看原始商品图片，逐项核验候选标注，不得因为候选已经给出就默认同意。

检查规则：
1. 颜色只能来自目标服饰，忽略肤色、头发、背景和道具。
2. 检查底色、主要配色、点缀色、明暗、图案、风格、版型和设计元素。
3. 材质、季节和场景没有充分可见证据时必须为空。
4. category、subcategory、colors、color_depth 是有效图片的必审字段。
5. 任一必审字段错误、证据与图片不符或标注总体置信度不足时返回 RETRY。
6. 只有图片本身无效、没有服饰主体、多商品无法确定主体或严重遮挡时返回 FAIL。
7. 所有字段都正确且置信度达到阈值时才能返回 PASS。
8. failure_reasons 和 retry_instructions 必须具体指出需要重新观察的图片证据。

最低标注置信度：""" + str(minimum_annotation_confidence) + """
最低审核置信度：""" + str(minimum_audit_confidence) + """

待审核标注：
""" + json.dumps(annotation, ensure_ascii=False, sort_keys=True)


def validate_audit(audit: object) -> dict[str, Any]:
    if not isinstance(audit, dict) or set(audit) != set(AUDIT_FIELDS):
        raise ValueError("audit fields do not match the required schema")
    if audit["status"] not in {"PASS", "RETRY", "FAIL"}:
        raise ValueError("invalid audit status")
    confidence = audit["overall_confidence"]
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError("audit confidence must be between 0 and 1")
    if not isinstance(audit["field_checks"], list) or not audit["field_checks"]:
        raise ValueError("audit field_checks must be a non-empty list")
    for check in audit["field_checks"]:
        if not isinstance(check, dict) or set(check) != {"field", "status", "evidence"}:
            raise ValueError("invalid audit field check")
        if check["status"] not in {"PASS", "FAIL"}:
            raise ValueError("invalid field check status")
        if not all(isinstance(check[key], str) for key in ("field", "evidence")):
            raise ValueError("audit field and evidence must be strings")
    for field in ("failure_reasons", "retry_instructions"):
        if not isinstance(audit[field], list) or not all(
            isinstance(value, str) for value in audit[field]
        ):
            raise ValueError(f"{field} must be a string list")
    if audit["status"] != "PASS" and not audit["failure_reasons"]:
        raise ValueError("non-PASS audit requires failure reasons")
    if audit["status"] == "RETRY" and not audit["retry_instructions"]:
        raise ValueError("RETRY audit requires retry instructions")
    return audit


def request_audit_with_retry(
    client: ResponsesVisionClient,
    *,
    image_url: str,
    annotation: dict[str, Any],
    audit_model: str,
    image_detail: str,
    request_index: int,
    audit_round: int,
    request_attempts: int,
    minimum_annotation_confidence: float,
    minimum_audit_confidence: float,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    usage_records: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt in range(1, request_attempts + 1):
        call_type_index = (audit_round - 1) * request_attempts + attempt
        try:
            response, usage = client.request(
                image_url=image_url,
                prompt=build_audit_prompt(
                    annotation,
                    minimum_annotation_confidence,
                    minimum_audit_confidence,
                ),
                schema_name="product_visual_audit",
                schema=audit_schema(),
                model=audit_model,
                image_detail=image_detail,
                request_index=request_index,
                call_type="visual_audit",
                call_type_index=call_type_index,
                prefix="independent_image_label_audit",
            )
            usage_records.append(usage)
            return validate_audit(json.loads(extract_output_text(response))), usage_records, errors
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status not in {408, 409, 429, 500, 502, 503, 504}:
                raise
            errors.append(f"attempt {attempt}: HTTP {status}")
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as error:
            errors.append(f"attempt {attempt}: {type(error).__name__}: {error}")
        if attempt < request_attempts:
            sleep(float(2 ** (attempt - 1)))
    raise AuditAttemptsExhausted(errors, usage_records)


def read_jsonl(path: Path, limit: int | None) -> list[dict[str, Any]]:
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
    maximum_rounds = args.maximum_audit_rounds or int(
        taxonomy["quality"]["maximum_audit_rounds"]
    )
    if maximum_rounds < 1:
        raise ValueError("maximum-audit-rounds must be positive")
    minimum_annotation_confidence = float(
        taxonomy["quality"]["minimum_annotation_confidence"]
    )
    minimum_audit_confidence = float(
        taxonomy["quality"]["minimum_audit_confidence"]
    )

    rows = read_jsonl(args.input, args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    client = ResponsesVisionClient(
        api_key=api_key,
        base_url=args.base_url,
        usage_output=args.usage_output,
    )
    try:
        with args.output.open("w", encoding="utf-8", newline="\n") as target:
            for request_index, row in enumerate(rows, start=1):
                candidate = row["candidate"]
                annotation = row.get("annotation")
                api_calls = list(row.get("api_calls") or [])
                audit_rounds: list[dict[str, Any]] = []
                errors = list(row.get("errors") or [])
                final_status = "FAIL"

                if row.get("status") == "ANNOTATED" and annotation is not None:
                    annotation = validate_annotation(annotation, taxonomy)
                    for audit_round in range(1, maximum_rounds + 1):
                        detail = "high" if audit_round == 1 else "original"
                        try:
                            audit, audit_usage, audit_errors = request_audit_with_retry(
                                client,
                                image_url=str(candidate["image_url"]),
                                annotation=annotation,
                                audit_model=args.audit_model,
                                image_detail=detail,
                                request_index=request_index,
                                audit_round=audit_round,
                                request_attempts=args.request_attempts,
                                minimum_annotation_confidence=minimum_annotation_confidence,
                                minimum_audit_confidence=minimum_audit_confidence,
                            )
                            api_calls.extend(audit_usage)
                            errors.extend(audit_errors)
                        except AuditAttemptsExhausted as error:
                            api_calls.extend(error.usage_records)
                            errors.extend(error.errors)
                            audit_rounds.append(
                                {
                                    "round": audit_round,
                                    "status": "ERROR",
                                    "errors": error.errors,
                                }
                            )
                            break

                        if (
                            audit["status"] == "PASS"
                            and float(audit["overall_confidence"])
                            >= minimum_audit_confidence
                            and float(annotation["overall_confidence"])
                            >= minimum_annotation_confidence
                        ):
                            final_status = "PASS"
                        elif audit["status"] == "PASS":
                            audit["status"] = "RETRY"
                            audit["failure_reasons"].append(
                                "annotation or audit confidence is below the configured threshold"
                            )
                            audit["retry_instructions"].append(
                                "reinspect every required visual field at original image detail"
                            )

                        audit_rounds.append(
                            {
                                "round": audit_round,
                                "annotation": annotation,
                                "audit": audit,
                            }
                        )
                        if final_status == "PASS" or audit["status"] == "FAIL":
                            break
                        if audit_round == maximum_rounds:
                            break

                        try:
                            annotation, annotation_usage, annotation_errors = (
                                request_annotation_with_retry(
                                    client,
                                    image_url=str(candidate["image_url"]),
                                    taxonomy=taxonomy,
                                    model=args.annotation_model,
                                    image_detail="original",
                                    request_index=request_index,
                                    request_attempts=args.request_attempts,
                                    retry_feedback=list(audit["retry_instructions"]),
                                )
                            )
                            api_calls.extend(annotation_usage)
                            errors.extend(annotation_errors)
                            annotation["visual_description"] = build_visual_description(
                                annotation, taxonomy
                            )
                        except VisionAttemptsExhausted as error:
                            api_calls.extend(error.usage_records)
                            errors.extend(error.errors)
                            break

                target.write(
                    json.dumps(
                        {
                            "product_id": row["product_id"],
                            "candidate": candidate,
                            "status": final_status,
                            "final_annotation": annotation if final_status == "PASS" else None,
                            "audit_rounds": audit_rounds,
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
