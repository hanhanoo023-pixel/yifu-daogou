from pathlib import Path

import pytest
import torch
from pydantic import ValidationError

from backend.c_recommender.ranking import (
    BUSINESS_OCCASION_LABELS,
    rank_products,
    rank_products_hybrid,
)
from backend.c_recommender.semantic import product_text, query_text
from backend.currency import to_catalog_price
from backend.recommender import normalize_request, recommend
from backend.schemas import NormalizedFilters, ProductCard, RecommendationRequest


def product(
    parent_asin: str,
    color: str,
    *,
    material: str = "Cotton",
) -> ProductCard:
    return ProductCard(
        parent_asin=parent_asin,
        title=f"{color} test top",
        brand="Test Brand",
        category="top",
        color=color,
        size="M",
        season="summer",
        material=material,
        price=29.99,
        image_url="https://example.com/product.jpg",
        rating=4.5,
        rating_count=100,
        stock_quantity=10,
        price_source="synthetic_demo",
        size_source="synthetic_demo",
        color_source="explicit_amazon",
        season_source="synthetic_demo",
        stock_source="synthetic_demo",
    )


def test_budget_is_a_hard_filter() -> None:
    result = recommend(RecommendationRequest(category="top", max_price=20, limit=20))

    assert result.products
    assert all(item.price is not None and item.price <= 20 for item in result.products)


def test_cny_budget_is_converted_to_catalog_usd() -> None:
    assert to_catalog_price(720, "CNY") == pytest.approx(100)

    result = recommend(
        RecommendationRequest(
            category="top",
            max_price=144,
            price_currency="CNY",
            limit=20,
        )
    )

    assert result.products
    assert all(item.price is not None and item.price <= 20 for item in result.products)


def test_material_is_normalized_and_hard_filtered() -> None:
    filters = normalize_request(RecommendationRequest(material="棉"))
    result = recommend(RecommendationRequest(material="棉", limit=20))

    assert filters.material == "cotton"
    assert result.products
    assert all(
        "棉" in item.business_materials
        or (item.material is not None and "cotton" in item.material.casefold())
        for item in result.products
    )


def test_explicit_color_exclusion_is_a_hard_filter() -> None:
    result = recommend(
        RecommendationRequest(
            category="top",
            excluded_colors=["黄色"],
            limit=20,
        )
    )

    assert result.products
    assert all(item.color != "yellow" for item in result.products)


def test_excluded_product_is_not_recommended_again() -> None:
    first = recommend(RecommendationRequest(category="top", limit=1)).products[0]
    result = recommend(
        RecommendationRequest(
            category="top",
            excluded_product_ids=[first.parent_asin],
            limit=20,
        )
    )

    assert all(item.parent_asin != first.parent_asin for item in result.products)


def test_explicit_brand_exclusion_is_a_hard_filter() -> None:
    result = recommend(
        RecommendationRequest(
            category="top",
            excluded_brands=["inktastic"],
            limit=20,
        )
    )

    assert result.products
    assert all(
        item.brand is None or item.brand.casefold() != "inktastic"
        for item in result.products
    )


def test_invalid_price_range_is_rejected() -> None:
    with pytest.raises(ValidationError, match="min_price cannot be greater"):
        RecommendationRequest(min_price=300, max_price=100)


def test_soft_negative_color_reduces_score_without_filtering() -> None:
    filters = NormalizedFilters(negative_colors=["blue"])
    ranked = rank_products(
        [product("BLUE", "blue"), product("BLACK", "black")],
        filters,
        limit=2,
    )

    assert [item.parent_asin for item in ranked] == ["BLACK", "BLUE"]
    assert ranked[1].component_scores["negative_soft"] == -15
    assert ranked[1].unmatched_features == ["软负向颜色：blue"]


def test_grounded_reason_marks_synthetic_demo_fields() -> None:
    ranked = rank_products(
        [product("YELLOW", "yellow")],
        NormalizedFilters(category="top", color="yellow"),
        limit=1,
    )

    item = ranked[0]
    assert item.reason.startswith("推荐「yellow test top」。")
    assert "商品库记录的品类为上衣" in item.reason
    assert "颜色为黄色" in item.reason
    assert "上衣、黄色条件相符" in item.reason
    assert "尺码、价格、季节、库存为演示数据" in item.reason


def test_hybrid_ranking_uses_35_percent_semantic_and_65_percent_rules() -> None:
    filters = NormalizedFilters(material="cotton")
    ranked = rank_products_hybrid(
        [
            product("RULE", "black", material="Cotton").model_copy(
                update={"title": "Cotton rule top"}
            ),
            product("SEMANTIC", "black", material="Wool").model_copy(
                update={"title": "Wool semantic top"}
            ),
        ],
        filters,
        semantic_scores=[0.0, 1.0],
        limit=2,
    )

    assert [item.parent_asin for item in ranked] == ["RULE", "SEMANTIC"]
    assert ranked[0].component_scores["hybrid_final"] == pytest.approx(0.65)
    assert ranked[1].component_scores["hybrid_final"] == pytest.approx(0.35)


def test_strictly_audited_product_receives_quality_score() -> None:
    audited = product("F200K_QUALITY", "blue").model_copy(
        update={"category_source": "fashion200k_category"}
    )
    unaudited = product("AMAZON_QUALITY", "blue")
    ranked = rank_products_hybrid(
        [unaudited, audited],
        NormalizedFilters(category="top", color="blue"),
        semantic_scores=[0.5, 0.5],
        limit=2,
    )

    assert ranked[0].parent_asin == "F200K_QUALITY"
    assert ranked[0].component_scores["strict_visual_audit"] == 12


def test_business_slots_receive_exact_ranking_scores() -> None:
    annotated = product("F200K_BUSINESS", "black", material="Unknown").model_copy(
        update={
            "category_source": "fashion200k_category",
            "business_annotation_version": "fashion200k-slots-v1.3",
            "business_subcategory": "上衣",
            "business_colors": ["蓝色"],
            "business_color_depth": "中等",
            "business_styles": ["简约"],
            "business_occasions": ["通勤"],
            "business_fits": ["宽松"],
            "business_materials": ["棉"],
            "business_sizes": ["M"],
        }
    )

    ranked = rank_products(
        [annotated],
        NormalizedFilters(
            category="top",
            subcategory="top",
            color="blue",
            color_depth="medium",
            size="M",
            material="cotton",
            style_preferences=["minimalist"],
            occasions=["commute"],
            fit="loose",
        ),
        limit=1,
    )

    components = ranked[0].component_scores
    assert components["color"] == 14
    assert components["color_depth"] == 10
    assert components["size"] == 12
    assert components["material"] == 12
    assert components["style_minimalist"] == 22
    assert components["occasion_commute"] == 26
    assert components["attribute_subcategory"] == 12
    assert components["attribute_fit"] == 12


def test_party_scene_uses_v13_business_annotation() -> None:
    annotated = product("F200K_PARTY", "black").model_copy(
        update={
            "category_source": "fashion200k_category",
            "business_annotation_version": "fashion200k-slots-v1.3",
            "business_occasions": ["聚会"],
        }
    )

    ranked = rank_products(
        [annotated],
        NormalizedFilters(occasions=["party"]),
        limit=1,
    )

    assert BUSINESS_OCCASION_LABELS["party"] == {"聚会"}
    assert ranked[0].component_scores["occasion_party"] == 26
    assert "业务标注场景：聚会" in ranked[0].matched_features


def test_hybrid_ranking_deduplicates_normalized_titles() -> None:
    ranked = rank_products_hybrid(
        [
            product("FIRST", "blue").model_copy(update={"title": "Blue Lace Top"}),
            product("SECOND", "blue").model_copy(update={"title": " blue   lace top "}),
        ],
        NormalizedFilters(category="top", color="blue"),
        semantic_scores=[1.0, 0.9],
        limit=2,
    )

    assert [item.parent_asin for item in ranked] == ["FIRST"]


def test_gentle_style_promotes_lace_and_penalizes_safety_workwear() -> None:
    gentle = product("GENTLE", "yellow", material="Lace").model_copy(
        update={
            "title": "A-line lace layered summer top",
            "description_text": "Cute floral ruffle design",
        }
    )
    workwear = product("WORKWEAR", "yellow", material="Polyester").model_copy(
        update={
            "title": "High Visibility Safety T-Shirt",
            "description_text": "Reflective workwear uniform",
        }
    )

    ranked = rank_products_hybrid(
        [workwear, gentle],
        NormalizedFilters(
            category="top",
            color="yellow",
            style_preferences=["gentle"],
        ),
        semantic_scores=[0.5, 0.5],
        limit=2,
    )

    assert [item.parent_asin for item in ranked] == ["GENTLE"]
    assert ranked[0].component_scores["style_gentle"] == 18
    assert "温柔风格" in ranked[0].reason


def test_beach_scene_rejects_outerwear_and_keeps_relevant_categories() -> None:
    beach_dress = product("BEACH", "blue", material="Linen").model_copy(
        update={"title": "Lightweight beach sundress", "category": "dress"}
    )
    leather_jacket = product("JACKET", "black", material="Leather").model_copy(
        update={"title": "Black leather jacket", "category": "outerwear"}
    )

    ranked = rank_products_hybrid(
        [leather_jacket, beach_dress],
        NormalizedFilters(occasions=["beach"]),
        semantic_scores=[1.0, 0.5],
        limit=2,
    )

    assert [item.parent_asin for item in ranked] == ["BEACH"]
    assert "occasion_beach" in ranked[0].component_scores


def test_style_terms_do_not_match_inside_brand_words() -> None:
    wallflower = product("WALLFLOWER", "yellow").model_copy(
        update={"title": "WallFlower Women's Printed Tee"}
    )

    ranked = rank_products(
        [wallflower],
        NormalizedFilters(style_preferences=["gentle"]),
        limit=1,
    )

    assert "style_gentle" not in ranked[0].component_scores


def test_semantic_text_uses_only_product_fields() -> None:
    text = product_text(
        {
            "title": "Blue cotton shirt",
            "category_norm": "top",
            "brand": "Example",
            "color_raw": "Blue",
            "material": "Cotton",
            "features_text": "Button front",
            "description_text": None,
        }
    )
    query = query_text("想要蓝色棉质上衣", {"category": "top", "color": "blue"})

    assert text == "Blue cotton shirt; top; Example; Blue; Cotton; Button front"
    assert query == "用户原始需求：想要蓝色棉质上衣；结构化条件：category=top; color=blue"


def test_projection_checkpoint_is_the_formal_c_model() -> None:
    checkpoint_path = (
        Path(__file__).resolve().parents[1]
        / "models"
        / "c_recommender"
        / "public_semantic_two_tower.pt"
    )
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)

    assert checkpoint["base_model"] == "intfloat/multilingual-e5-small"
    assert checkpoint["input_dim"] == 384
    assert checkpoint["output_dim"] == 128
