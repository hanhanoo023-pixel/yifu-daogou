from backend.c_recommender.ranking import (
    load_weights,
    product_passes_relevance_gate,
    score_product,
)
from backend.safety_guard import apply_safety_guard
from backend.schemas import NormalizedFilters, ProductCard


def product(product_id: str, title: str, features: str) -> ProductCard:
    return ProductCard(
        parent_asin=product_id,
        title=title,
        brand="Demo",
        category="pants",
        color="black",
        size="M",
        season="summer",
        material="Cotton",
        price=29.99,
        image_url="https://example.com/product.jpg",
        rating=4.5,
        rating_count=10,
        stock_quantity=8,
        price_source="synthetic_demo",
        size_source="synthetic_demo",
        color_source="explicit_amazon",
        season_source="synthetic_demo",
        stock_source="synthetic_demo",
        features_text=features,
    )


def test_body_goal_scores_only_with_product_evidence() -> None:
    filters = NormalizedFilters(body_goals=["elongate"])
    supported = score_product(
        product("P1", "High waist straight pants", "vertical stripe"),
        filters,
        load_weights(),
    )
    unsupported = score_product(
        product("P2", "Basic pants", "plain everyday pants"),
        filters,
        load_weights(),
    )

    assert supported.component_scores["body_goal_elongate"] > 0
    assert any("身材诉求：显高" in value for value in supported.matched_features)
    assert product_passes_relevance_gate(supported, filters)
    assert not product_passes_relevance_gate(unsupported, filters)
    assert apply_safety_guard(supported).safety.status == "PASS"


def test_body_goal_conflict_fails_relevance_gate() -> None:
    filters = NormalizedFilters(body_goals=["tummy_coverage"])
    scored = score_product(
        product("P3", "Cropped bodycon top", "crop top"),
        filters,
        load_weights(),
    )

    assert scored.component_scores["body_goal_conflict_tummy_coverage"] < 0
    assert not product_passes_relevance_gate(scored, filters)
