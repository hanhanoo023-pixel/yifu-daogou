import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend import demo_commerce
from backend.app import app
from backend.schemas import ProductCard


client = TestClient(app)


def product() -> ProductCard:
    return ProductCard(
        parent_asin="STORE-DEMO-001",
        title="Demo high waist dress",
        brand="StyleMate",
        category="dress",
        color="blue",
        size="M",
        season="summer",
        material="Cotton",
        price=49.5,
        image_url="https://example.com/product.jpg",
        rating=4.8,
        rating_count=20,
        stock_quantity=6,
        price_source="synthetic_demo",
        size_source="synthetic_demo",
        color_source="explicit_amazon",
        season_source="synthetic_demo",
        stock_source="synthetic_demo",
        business_sizes=["S", "M", "L"],
    )


def test_internal_product_detail_is_explicitly_labeled_demo(monkeypatch) -> None:
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())

    response = client.get("/api/products/STORE-DEMO-001")

    assert response.status_code == 200
    assert response.json()["product"]["parent_asin"] == "STORE-DEMO-001"
    assert "演示数据" in response.json()["data_notice"]


def test_demo_cart_order_and_payment_flow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_commerce, "DEMO_COMMERCE_DIR", tmp_path)
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    profile_id = "profile-store-demo-001"

    added = client.post(
        f"/api/cart/{profile_id}/items",
        json={"product_id": "STORE-DEMO-001", "selected_size": "M", "quantity": 2},
    )
    assert added.status_code == 200
    assert added.json()["items"][0]["selected_size"] == "M"
    assert added.json()["items"][0]["quantity"] == 2
    assert added.json()["total"] == 99.0
    assert "演示购物车" in added.json()["demo_notice"]

    order = client.post("/api/orders", json={"profile_id": profile_id})
    assert order.status_code == 200
    order_payload = order.json()
    assert order_payload["status"] == "DEMO_PENDING"
    assert "不会发生真实扣款" in order_payload["demo_notice"]

    emptied = client.get(f"/api/cart/{profile_id}")
    assert emptied.status_code == 200
    assert emptied.json()["items"] == []

    paid = client.post(
        f"/api/orders/{order_payload['order_id']}/demo-pay",
        json={"profile_id": profile_id},
    )
    assert paid.status_code == 200
    assert paid.json()["status"] == "DEMO_PAID"
    assert paid.json()["paid_at"] is not None


def test_demo_cart_keeps_size_variants_separate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_commerce, "DEMO_COMMERCE_DIR", tmp_path)
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    profile_id = "profile-store-demo-variants"

    client.post(
        f"/api/cart/{profile_id}/items",
        json={"product_id": "STORE-DEMO-001", "selected_size": "M", "quantity": 1},
    )
    added = client.post(
        f"/api/cart/{profile_id}/items",
        json={"product_id": "STORE-DEMO-001", "selected_size": "L", "quantity": 2},
    )

    assert added.status_code == 200
    assert [(item["selected_size"], item["quantity"]) for item in added.json()["items"]] == [
        ("M", 1),
        ("L", 2),
    ]

    removed = client.delete(
        f"/api/cart/{profile_id}/items/STORE-DEMO-001",
        params={"selected_size": "M"},
    )
    assert removed.status_code == 200
    assert [(item["selected_size"], item["quantity"]) for item in removed.json()["items"]] == [
        ("L", 2),
    ]


def test_existing_demo_cart_without_selected_size_is_preserved(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_commerce, "DEMO_COMMERCE_DIR", tmp_path)
    monkeypatch.setattr("backend.app.get_product_by_id", lambda product_id: product())
    profile_id = "profile-existing-cart"
    cart_path = demo_commerce._cart_path(profile_id)
    cart_path.parent.mkdir(parents=True)
    cart_path.write_text(
        json.dumps(
            {
                "profile_id": profile_id,
                "items": [{"product_id": "STORE-DEMO-001", "quantity": 1}],
            }
        ),
        encoding="utf-8",
    )

    response = client.get(f"/api/cart/{profile_id}")

    assert response.status_code == 200
    assert response.json()["items"][0]["selected_size"] == "M"


def test_demo_contact_never_claims_real_store_integration() -> None:
    response = client.get("/api/store/contact")

    assert response.status_code == 200
    assert "未连接真实门店客服系统" in response.json()["demo_notice"]
