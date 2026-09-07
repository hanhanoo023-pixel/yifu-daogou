from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from backend.schemas import (
    CartItemResponse,
    CartResponse,
    DemoOrder,
    ProductCard,
    StoreContactResponse,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_COMMERCE_DIR = PROJECT_ROOT / "data" / "demo_commerce"
CART_NOTICE = "演示购物车：商品价格和库存沿用当前演示商品库，不代表门店实时数据。"
ORDER_NOTICE = "演示订单与演示支付仅用于流程展示，不会发生真实扣款或发货。"
ProductResolver = Callable[[str], ProductCard | None]


def _profile_key(profile_id: str) -> str:
    return hashlib.sha256(profile_id.encode("utf-8")).hexdigest()


def _cart_path(profile_id: str) -> Path:
    return DEMO_COMMERCE_DIR / "carts" / f"{_profile_key(profile_id)}.json"


def _order_path(order_id: str) -> Path:
    return DEMO_COMMERCE_DIR / "orders" / f"{order_id}.json"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_cart_rows(profile_id: str) -> list[dict[str, object]]:
    path = _cart_path(profile_id)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload["profile_id"] != profile_id:
        raise ValueError("cart profile mismatch")
    return list(payload["items"])


def _cart_response(
    profile_id: str,
    rows: list[dict[str, object]],
    resolve_product: ProductResolver,
) -> CartResponse:
    items: list[CartItemResponse] = []
    for row in rows:
        product_id = str(row["product_id"])
        product = resolve_product(product_id)
        if product is None:
            raise ValueError(f"cart product no longer exists: {product_id}")
        selected_size_value = row.get("selected_size")
        if selected_size_value is None:
            if product.size is None:
                raise ValueError(f"cart item has no selected size: {product_id}")
            selected_size_value = product.size
        quantity = int(row["quantity"])
        line_total = round((product.price or 0) * quantity, 2)
        items.append(
            CartItemResponse(
                product=product,
                selected_size=str(selected_size_value),
                quantity=quantity,
                line_total=line_total,
            )
        )
    return CartResponse(
        profile_id=profile_id,
        items=items,
        total=round(sum(item.line_total for item in items), 2),
        demo_notice=CART_NOTICE,
    )


def get_cart(profile_id: str, resolve_product: ProductResolver) -> CartResponse:
    return _cart_response(profile_id, _load_cart_rows(profile_id), resolve_product)


def add_cart_item(
    profile_id: str,
    product_id: str,
    selected_size: str,
    quantity: int,
    resolve_product: ProductResolver,
) -> CartResponse:
    if resolve_product(product_id) is None:
        raise LookupError("product does not exist")
    rows = _load_cart_rows(profile_id)
    existing = next(
        (
            row
            for row in rows
            if row["product_id"] == product_id
            and row.get("selected_size") == selected_size
        ),
        None,
    )
    if existing is None:
        rows.append(
            {
                "product_id": product_id,
                "selected_size": selected_size,
                "quantity": quantity,
            }
        )
    else:
        next_quantity = int(existing["quantity"]) + quantity
        if next_quantity > 99:
            raise ValueError("cart quantity cannot exceed 99")
        existing["quantity"] = next_quantity
    _write_json(_cart_path(profile_id), {"profile_id": profile_id, "items": rows})
    return _cart_response(profile_id, rows, resolve_product)


def remove_cart_item(
    profile_id: str,
    product_id: str,
    selected_size: str,
    resolve_product: ProductResolver,
) -> CartResponse:
    rows: list[dict[str, object]] = []
    for row in _load_cart_rows(profile_id):
        if row["product_id"] != product_id:
            rows.append(row)
            continue
        row_size = row.get("selected_size")
        if row_size is None:
            product = resolve_product(product_id)
            if product is None or product.size is None:
                raise ValueError(f"cart item has no selected size: {product_id}")
            row_size = product.size
        if row_size != selected_size:
            rows.append(row)
    _write_json(_cart_path(profile_id), {"profile_id": profile_id, "items": rows})
    return _cart_response(profile_id, rows, resolve_product)


def create_demo_order(
    profile_id: str,
    resolve_product: ProductResolver,
) -> DemoOrder:
    cart = get_cart(profile_id, resolve_product)
    if not cart.items:
        raise ValueError("cart is empty")
    order = DemoOrder(
        order_id=f"DEMO-{uuid4().hex[:12].upper()}",
        profile_id=profile_id,
        items=cart.items,
        total=cart.total,
        status="DEMO_PENDING",
        created_at=datetime.now(timezone.utc).isoformat(),
        demo_notice=ORDER_NOTICE,
    )
    _write_json(_order_path(order.order_id), order.model_dump(mode="json"))
    _write_json(_cart_path(profile_id), {"profile_id": profile_id, "items": []})
    return order


def list_demo_orders(profile_id: str) -> list[DemoOrder]:
    order_directory = DEMO_COMMERCE_DIR / "orders"
    if not order_directory.exists():
        return []
    orders = [
        DemoOrder.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in order_directory.glob("DEMO-*.json")
    ]
    return sorted(
        (order for order in orders if order.profile_id == profile_id),
        key=lambda order: order.created_at,
        reverse=True,
    )


def get_demo_order(order_id: str) -> DemoOrder | None:
    path = _order_path(order_id)
    if not path.exists():
        return None
    return DemoOrder.model_validate(json.loads(path.read_text(encoding="utf-8")))


def demo_pay_order(order_id: str, profile_id: str) -> DemoOrder:
    path = _order_path(order_id)
    order = get_demo_order(order_id)
    if order is None:
        raise LookupError("order does not exist")
    if order.profile_id != profile_id:
        raise PermissionError("order does not belong to profile")
    paid = order.model_copy(
        update={
            "status": "DEMO_PAID",
            "paid_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _write_json(path, paid.model_dump(mode="json"))
    return paid


def store_contact() -> StoreContactResponse:
    return StoreContactResponse(
        assistant_name=os.getenv("STYLEMATE_SALES_NAME", "门店导购 Mia（演示）"),
        channel=os.getenv("STYLEMATE_SALES_CHANNEL", "店内服务台"),
        contact=os.getenv("STYLEMATE_SALES_CONTACT", "请向门店工作人员出示当前商品"),
        service_hours=os.getenv("STYLEMATE_SALES_HOURS", "10:00–21:00（演示）"),
        demo_notice="当前为演示导购入口，未连接真实门店客服系统。",
    )
