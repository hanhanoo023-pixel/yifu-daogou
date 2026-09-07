from backend.evaluator import (
    EVAL_DIR,
    evaluate_rule_intents,
    evaluate_safety,
    load_jsonl,
    product_passes_hard_constraints,
)
from backend.schemas import NormalizedFilters, ProductCard


def test_draft_evaluation_dataset_sizes() -> None:
    assert len(load_jsonl(EVAL_DIR / "intent_draft.jsonl")) == 50
    assert len(load_jsonl(EVAL_DIR / "recommendation_draft.jsonl")) == 30
    assert len(load_jsonl(EVAL_DIR / "safety_draft.jsonl")) == 30


def test_rule_intent_draft_reports_errors_without_claiming_gold() -> None:
    report = evaluate_rule_intents(load_jsonl(EVAL_DIR / "intent_draft.jsonl"))

    assert report["scope"] == "rule_intent_baseline_only"
    assert report["total"] == 50
    assert 0 <= report["accuracy"] <= 1


def test_safety_draft_metrics_are_computed() -> None:
    report = evaluate_safety(load_jsonl(EVAL_DIR / "safety_draft.jsonl"))

    assert report["scope"] == "deterministic_safety_regression"
    assert report["total"] == 30
    assert 0 <= report["risk_recall"] <= 1
    assert 0 <= report["false_block_rate"] <= 1


def test_hard_constraint_check_uses_cny_conversion() -> None:
    product = ProductCard(
        parent_asin="EVAL",
        title="Test",
        brand="Brand",
        category="top",
        color="blue",
        size="M",
        season="summer",
        material="Cotton",
        price=20,
        image_url="https://example.com/test.jpg",
        rating=4.0,
        rating_count=10,
        stock_quantity=1,
        price_source="original",
        size_source="original",
        color_source="explicit_amazon",
        season_source="original",
        stock_source="synthetic_demo",
    )

    assert product_passes_hard_constraints(
        product,
        NormalizedFilters(max_price=144, price_currency="CNY"),
    )
    assert not product_passes_hard_constraints(
        product,
        NormalizedFilters(max_price=100, price_currency="CNY"),
    )
