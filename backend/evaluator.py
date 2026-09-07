from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.currency import to_catalog_price
from backend.database import PRODUCT_SCOPE_CATEGORIES
from backend.llm_client import detect_rule_intent
from backend.recommender import normalize_request, recommend
from backend.safety_guard import check_product_reason
from backend.schemas import NormalizedFilters, ProductCard, RecommendationRequest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = PROJECT_ROOT / "data" / "eval"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def evaluate_rule_intents(cases: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[dict[str, str | None]] = []
    correct = 0
    for case in cases:
        predicted = detect_rule_intent(case["text"])
        predicted_value = predicted.value if predicted is not None else None
        if predicted_value == case["expected_intent"]:
            correct += 1
        else:
            errors.append(
                {
                    "case_id": case["case_id"],
                    "text": case["text"],
                    "expected": case["expected_intent"],
                    "predicted": predicted_value,
                }
            )
    total = len(cases)
    return {
        "scope": "rule_intent_baseline_only",
        "total": total,
        "correct": correct,
        "accuracy": correct / total,
        "errors": errors[:20],
    }


def product_passes_hard_constraints(
    product: ProductCard,
    filters: NormalizedFilters,
) -> bool:
    exact_fields = ("category", "color", "size")
    for field in exact_fields:
        wanted = getattr(filters, field)
        if wanted is not None and getattr(product, field) != wanted:
            return False
    if (
        filters.season is not None
        and product.season_source != "synthetic_demo"
        and product.season != filters.season
    ):
        return False
    if filters.product_scope and filters.product_scope != "all":
        if product.category not in PRODUCT_SCOPE_CATEGORIES[filters.product_scope]:
            return False
    if filters.min_price is not None:
        minimum = to_catalog_price(filters.min_price, filters.price_currency)
        if product.price is None or product.price < minimum:
            return False
    if filters.max_price is not None:
        maximum = to_catalog_price(filters.max_price, filters.price_currency)
        if product.price is None or product.price > maximum:
            return False
    if filters.brand is not None:
        if product.brand is None or product.brand.casefold() != filters.brand.casefold():
            return False
    if filters.material is not None:
        if product.material is None or filters.material.casefold() not in product.material.casefold():
            return False
    if product.color in filters.excluded_colors:
        return False
    if product.category in filters.excluded_categories:
        return False
    if product.brand is not None and product.brand.casefold() in {
        brand.casefold() for brand in filters.excluded_brands
    }:
        return False
    if product.material is not None and any(
        material.casefold() in product.material.casefold()
        for material in filters.excluded_materials
    ):
        return False
    if product.parent_asin in filters.excluded_product_ids:
        return False
    return product.stock_quantity > 0


def evaluate_recommendations(cases: list[dict[str, Any]]) -> dict[str, Any]:
    query_results: list[dict[str, Any]] = []
    violations = 0
    covered = 0
    returned_products = 0
    for case in cases:
        request = RecommendationRequest(**case["request"])
        result = recommend(request)
        filters = normalize_request(request)
        case_violations = [
            product.parent_asin
            for product in result.products
            if not product_passes_hard_constraints(product, filters)
        ]
        violations += len(case_violations)
        returned_products += len(result.products)
        if result.products:
            covered += 1
        query_results.append(
            {
                "case_id": case["case_id"],
                "result_count": len(result.products),
                "hard_constraint_violations": case_violations,
                "no_result_reason": result.no_result_reason,
            }
        )
    total = len(cases)
    return {
        "scope": "hard_constraints_and_coverage_only",
        "total_queries": total,
        "coverage": covered / total,
        "returned_products": returned_products,
        "hard_constraint_violations": violations,
        "hard_constraint_violation_rate": (
            violations / returned_products if returned_products else 0.0
        ),
        "hit_at_3": None,
        "mrr": None,
        "ndcg_at_3": None,
        "metric_notice": "相关性指标等待人工商品相关性标注后计算。",
        "cases": query_results,
    }


def _safety_product(overrides: dict[str, Any]) -> ProductCard:
    values: dict[str, Any] = {
        "parent_asin": "EVAL_PRODUCT",
        "title": "Blue cotton shirt",
        "brand": "Test Brand",
        "category": "top",
        "color": "blue",
        "size": "M",
        "season": "summer",
        "material": "Cotton",
        "price": 29.99,
        "image_url": "https://example.com/product.jpg",
        "rating": 4.5,
        "rating_count": 100,
        "stock_quantity": 10,
        "price_source": "synthetic_demo",
        "size_source": "synthetic_demo",
        "color_source": "explicit_amazon",
        "season_source": "synthetic_demo",
        "stock_source": "synthetic_demo",
        "category_source": "amazon_category",
    }
    values.update(overrides)
    return ProductCard(**values)


def evaluate_safety(cases: list[dict[str, Any]]) -> dict[str, Any]:
    correct = 0
    risky = 0
    risky_detected = 0
    safe = 0
    false_blocks = 0
    rewrite_expected = 0
    rewrite_success = 0
    errors: list[dict[str, str]] = []
    for case in cases:
        result = check_product_reason(
            _safety_product(case.get("product_overrides", {})),
            case["text"],
        )
        expected = case["expected_status"]
        if result.status == expected:
            correct += 1
        else:
            errors.append(
                {
                    "case_id": case["case_id"],
                    "expected": expected,
                    "predicted": result.status,
                }
            )
        if expected == "PASS":
            safe += 1
            if result.status == "BLOCK":
                false_blocks += 1
        else:
            risky += 1
            if result.status != "PASS":
                risky_detected += 1
        if expected == "REWRITE":
            rewrite_expected += 1
            if result.status == "REWRITE" and result.safe_text:
                rewrite_success += 1
    total = len(cases)
    return {
        "scope": "deterministic_safety_regression",
        "total": total,
        "status_accuracy": correct / total,
        "risk_recall": risky_detected / risky if risky else 0.0,
        "false_block_rate": false_blocks / safe if safe else 0.0,
        "rewrite_success": (
            rewrite_success / rewrite_expected if rewrite_expected else 0.0
        ),
        "errors": errors[:20],
    }


def run_draft_evaluation(suite: str = "all") -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "DRAFT_AI_REQUIRES_HUMAN_REVIEW",
        "acceptance_claim_allowed": False,
        "dataset_notice": (
            "这些样本是待人工复核的评测草稿，不得作为正式准确率或PRD验收结论。"
        ),
    }
    if suite in {"all", "intent"}:
        report["intent"] = evaluate_rule_intents(
            load_jsonl(EVAL_DIR / "intent_draft.jsonl")
        )
    if suite in {"all", "recommendation"}:
        report["recommendation"] = evaluate_recommendations(
            load_jsonl(EVAL_DIR / "recommendation_draft.jsonl")
        )
    if suite in {"all", "safety"}:
        report["safety"] = evaluate_safety(
            load_jsonl(EVAL_DIR / "safety_draft.jsonl")
        )
    return report
