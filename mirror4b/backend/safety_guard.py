from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from backend.product_explanation import CATEGORY_LABELS, COLOR_LABELS, build_product_explanation
from backend.schemas import Claim, NormalizedFilters, ProductCard, SafetyResult, SafetyViolation


CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
PROHIBITED_PHRASES_PATH = CONFIG_DIR / "prohibited_phrases.yaml"
SAFETY_RULES_PATH = CONFIG_DIR / "safety_rules.yaml"

MATERIAL_TERMS = {
    "cotton": "cotton",
    "棉": "cotton",
    "纯棉": "cotton",
    "wool": "wool",
    "羊毛": "wool",
    "羊绒": "wool",
    "polyester": "polyester",
    "聚酯": "polyester",
    "聚酯纤维": "polyester",
    "leather": "leather",
    "皮革": "leather",
    "真皮": "leather",
    "silk": "silk",
    "真丝": "silk",
    "丝绸": "silk",
    "lace": "lace",
    "蕾丝": "lace",
    "denim": "denim",
    "牛仔": "denim",
    "linen": "linen",
    "亚麻": "linen",
}


@lru_cache(maxsize=1)
def load_safety_config() -> tuple[list[dict[str, str]], dict[str, object]]:
    prohibited = yaml.safe_load(PROHIBITED_PHRASES_PATH.read_text(encoding="utf-8"))
    rules = yaml.safe_load(SAFETY_RULES_PATH.read_text(encoding="utf-8"))
    return list(prohibited["phrases"]), dict(rules)


def _claim(
    claim_type: str,
    value: str,
    source_field: str | None,
    supported: bool,
    evidence: str | None,
) -> Claim:
    return Claim(
        type=claim_type,
        value=value,
        source_field=source_field,
        supported=supported,
        evidence=evidence,
    )


def extract_claims(product: ProductCard, text: str) -> list[Claim]:
    claims: list[Claim] = []
    lowered = text.casefold()
    price_source_disclosed = bool(
        re.search(r"(?:演示价格|价格[^；。]*演示)", text)
    )
    stock_source_disclosed = bool(
        re.search(r"(?:演示库存|库存[^；。]*演示)", text)
    )

    for match in re.finditer(
        r"(?:(?:价格|售价)[：:为是 ]*[$￥¥]?|[$￥¥])(\d+(?:\.\d+)?)",
        text,
    ):
        value = float(match.group(1))
        exact = product.price is not None and abs(value - product.price) < 0.01
        source_allowed = (
            product.price_source != "synthetic_demo" or price_source_disclosed
        )
        claims.append(
            _claim(
                "PRICE",
                match.group(1),
                "price",
                exact and source_allowed,
                None if product.price is None else str(product.price),
            )
        )
    if "价格为演示值" in text:
        claims.append(
            _claim(
                "PRICE_SOURCE",
                "synthetic_demo",
                "price_source",
                product.price_source == "synthetic_demo",
                product.price_source,
            )
        )

    material_evidence = " | ".join(
        value
        for value in (product.material, product.title, product.features_text)
        if value
    )
    actual_material = material_evidence.casefold()
    actual_material_values = {
        normalized
        for term, normalized in MATERIAL_TERMS.items()
        if term.casefold() in actual_material
    }
    seen_materials: set[str] = set()
    for term, normalized in MATERIAL_TERMS.items():
        if term.casefold() in lowered and normalized not in seen_materials:
            seen_materials.add(normalized)
            absolute_claim = bool(
                re.search(rf"100%\s*{re.escape(term)}", text, re.IGNORECASE)
            )
            absolute_supported = not absolute_claim or bool(
                re.search(
                    rf"100%\s*{re.escape(term)}",
                    material_evidence,
                    re.IGNORECASE,
                )
            )
            claims.append(
                _claim(
                    "MATERIAL",
                    normalized,
                    "material/title/features_text",
                    normalized in actual_material_values and absolute_supported,
                    material_evidence,
                )
            )

    for match in re.finditer(r"(?:库存[：:为有 ]*|剩余?)(\d+)件", text):
        value = int(match.group(1))
        exact = value == product.stock_quantity
        source_allowed = (
            product.stock_source != "synthetic_demo" or stock_source_disclosed
        )
        claims.append(
            _claim(
                "STOCK",
                str(value),
                "stock_quantity",
                exact and source_allowed,
                str(product.stock_quantity),
            )
        )
    if "库存为演示值" in text:
        claims.append(
            _claim(
                "STOCK_SOURCE",
                "synthetic_demo",
                "stock_source",
                product.stock_source == "synthetic_demo",
                product.stock_source,
            )
        )

    title_match = re.search(r"推荐「([^」]+)」", text)
    if title_match:
        value = title_match.group(1).strip()
        claims.append(
            _claim("TITLE", value, "title", value == product.title, product.title)
        )

    category_label = CATEGORY_LABELS.get(product.category or "", product.category)
    color_label = COLOR_LABELS.get(product.color or "", product.color)
    for label, claim_type, field, actual, alternate in (
        ("品类", "CATEGORY", "category", category_label, product.category),
        ("品牌", "BRAND", "brand", product.brand, product.brand),
        ("颜色", "COLOR", "color", color_label, product.color),
    ):
        for match in re.finditer(rf"{label}(?:[：:]|为)([^，；。]+)", text):
            value = match.group(1).strip()
            accepted = {
                candidate.casefold()
                for candidate in (actual, alternate)
                if candidate is not None
            }
            claims.append(
                _claim(
                    claim_type,
                    value,
                    field,
                    value.casefold() in accepted,
                    actual,
                )
            )

    feature_match = re.search(r"商品特征记录为([^。，]+)", text)
    if feature_match:
        value = feature_match.group(1).strip()
        evidence = product.features_text or ""
        claims.append(
            _claim(
                "FEATURE",
                value,
                "features_text",
                value.casefold() in evidence.casefold(),
                product.features_text,
            )
        )

    source_match = re.search(r"其中([^。]+)为演示数据", text)
    if source_match:
        disclosed_fields = re.split(r"[、和]", source_match.group(1))
        source_fields = {
            "尺码": ("size_source", product.size_source),
            "价格": ("price_source", product.price_source),
            "季节": ("season_source", product.season_source),
            "库存": ("stock_source", product.stock_source),
        }
        for value in disclosed_fields:
            if value in source_fields:
                source_field, actual = source_fields[value]
                claims.append(
                    _claim(
                        "DEMO_SOURCE",
                        value,
                        source_field,
                        actual == "synthetic_demo",
                        actual,
                    )
                )
    return claims


def _safe_reason(product: ProductCard) -> str:
    return build_product_explanation(product, NormalizedFilters())


def check_product_reason(product: ProductCard, text: str) -> SafetyResult:
    prohibited, rules = load_safety_config()
    claims = extract_claims(product, text)
    violations: list[SafetyViolation] = []
    for item in prohibited:
        if re.search(item["pattern"], text):
            violations.append(
                SafetyViolation(
                    code=item["code"],
                    severity=item["severity"],
                    message=item["message"],
                )
            )
    for claim in claims:
        if not claim.supported:
            violations.append(
                SafetyViolation(
                    code=f"UNSUPPORTED_{claim.type}",
                    severity="HIGH",
                    message=f"{claim.type}与商品字段不一致或缺少真实来源",
                    evidence_field=claim.source_field,
                )
            )

    high_severity_count = sum(
        violation.severity == "HIGH" for violation in violations
    )
    if high_severity_count >= int(rules["block_on_high_severity_count"]):
        status = "BLOCK"
        safe_text = None
    elif violations:
        status = "REWRITE"
        safe_text = _safe_reason(product)
    else:
        status = "PASS"
        safe_text = text
    supported_count = sum(claim.supported for claim in claims)
    supported_claim_ratio = supported_count / len(claims) if claims else 1.0
    return SafetyResult(
        status=status,
        violations=violations,
        claims=claims,
        original_text=text,
        safe_text=safe_text,
        blocked=status == "BLOCK",
        supported_claim_ratio=supported_claim_ratio,
        checked_fields=list(rules["checked_fields"]),
    )


def apply_safety_guard(product: ProductCard) -> ProductCard:
    original = product.reason
    safety = check_product_reason(product, original)
    safe_reason = safety.safe_text or "该推荐理由未通过安全检查，已阻止展示。"
    return product.model_copy(
        update={
            "reason": safe_reason,
            "reason_original": original,
            "reason_safe": safe_reason,
            "safety": safety,
        }
    )
