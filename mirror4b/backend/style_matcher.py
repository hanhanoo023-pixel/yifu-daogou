from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from backend.schemas import ProductCard


STYLE_RULES_PATH = Path(__file__).resolve().parents[1] / "configs" / "style_rules.yaml"
SCENE_RULES_PATH = Path(__file__).resolve().parents[1] / "configs" / "scene_rules.yaml"
BODY_GOAL_RULES_PATH = (
    Path(__file__).resolve().parents[1] / "configs" / "body_goal_rules.yaml"
)


@lru_cache(maxsize=1)
def load_style_rules() -> dict[str, dict[str, object]]:
    config = yaml.safe_load(STYLE_RULES_PATH.read_text(encoding="utf-8"))
    return dict(config["styles"])


@lru_cache(maxsize=1)
def load_scene_rules() -> dict[str, dict[str, object]]:
    config = yaml.safe_load(SCENE_RULES_PATH.read_text(encoding="utf-8"))
    return dict(config["scenes"])


@lru_cache(maxsize=1)
def load_body_goal_rules() -> dict[str, dict[str, object]]:
    config = yaml.safe_load(BODY_GOAL_RULES_PATH.read_text(encoding="utf-8"))
    return dict(config["body_goals"])


def product_style_text(product: ProductCard) -> str:
    return " ".join(
        value.casefold()
        for value in (
            product.title,
            product.material,
            product.features_text,
            product.description_text,
        )
        if value
    )


def _contains_term(text: str, term: str) -> bool:
    normalized = term.casefold()
    if re.fullmatch(r"[a-z0-9 -]+", normalized):
        return bool(
            re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                text,
            )
        )
    return normalized in text


def match_style(
    product: ProductCard,
    style: str,
) -> tuple[str, list[str], list[str]]:
    rule = load_style_rules()[style]
    text = product_style_text(product)
    positive = [
        str(term)
        for term in rule["positive_terms"]
        if _contains_term(text, str(term))
    ]
    negative = [
        str(term)
        for term in rule["negative_terms"]
        if _contains_term(text, str(term))
    ]
    return str(rule["label"]), positive, negative


def match_scene(
    product: ProductCard,
    scene: str,
) -> tuple[str, list[str], list[str], bool, bool]:
    rule = load_scene_rules()[scene]
    text = product_style_text(product)
    positive = [
        str(term)
        for term in rule["positive_terms"]
        if _contains_term(text, str(term))
    ]
    negative = [
        str(term)
        for term in rule["negative_terms"]
        if _contains_term(text, str(term))
    ]
    preferred_category = product.category in set(rule["preferred_categories"])
    blocked_category = product.category in set(rule["blocked_categories"])
    return (
        str(rule["label"]),
        positive,
        negative,
        preferred_category,
        blocked_category,
    )


def match_body_goal(
    product: ProductCard,
    body_goal: str,
) -> tuple[str, list[str], list[str]]:
    rule = load_body_goal_rules()[body_goal]
    text = product_style_text(product)
    positive = [
        str(term)
        for term in rule["positive_terms"]
        if _contains_term(text, str(term))
    ]
    negative = [
        str(term)
        for term in rule["negative_terms"]
        if _contains_term(text, str(term))
    ]
    return str(rule["label"]), positive, negative


def preferred_categories_for_scenes(scenes: list[str]) -> list[str]:
    rules = load_scene_rules()
    categories: list[str] = []
    for scene in scenes:
        categories.extend(str(value) for value in rules[scene]["preferred_categories"])
    return list(dict.fromkeys(categories))
