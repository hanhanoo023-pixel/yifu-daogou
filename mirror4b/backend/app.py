from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.agent_orchestrator import merge_workflow_state, plan_agent_action
from backend.aliyun_background_client import (
    ProviderBackgroundTask,
    query_background_task,
    submit_background_task,
)
from backend.aliyun_tryon_client import (
    BOTTOM_GARMENT_CATEGORIES,
    TOP_GARMENT_CATEGORIES,
    ProviderTask,
    query_try_on_task,
    submit_try_on_task,
)
from backend.conversation_memory import (
    ConversationContext,
    apply_remembered_preferences,
    delete_profile_preferences,
    delete_session,
    load_conversation_context,
    record_turn,
    remember_explicit_preferences,
)
from backend.currency import load_currency_config
from backend.database import database_product_count, get_product_by_id
from backend.demo_commerce import (
    add_cart_item,
    create_demo_order,
    demo_pay_order,
    get_cart,
    get_demo_order,
    list_demo_orders,
    remove_cart_item,
    store_contact,
)
from backend.dialogue_response import generate_advisor_reply
from backend.llm_client import (
    UNSUPPORTED_OCCASION_WARNING,
    infer_pending_action,
    parse_shopping_request,
)
from backend.mirror4b_client import (
    chat_with_agent as mirror4b_chat_with_agent,
    sync_products as mirror4b_sync_products,
)
from backend.mirror4b_adapter import (
    build_product_catalog,
    recommendation_response_from_agent,
)
from backend.image_storage import (
    TRY_ON_STORAGE_DIR,
    absolute_public_url,
    background_source_data_url,
    background_result_public_url,
    load_task_record,
    result_public_url,
    save_person_image,
    save_remote_background_image,
    save_remote_result_image,
    save_task_record,
    task_record_exists,
)
from backend.product_query import answer_product_question, compare_products
from backend.person_gallery import (
    delete_person_image,
    list_person_images,
    record_person_image,
)
from backend.recommender import recommend
from backend.saved_results import list_saved_results, save_result
from backend.schemas import (
    AgentWorkflowState,
    BackgroundTaskCreateRequest,
    BackgroundTaskResponse,
    CartItemCreateRequest,
    CartResponse,
    ChatRequest,
    DemoOrder,
    DemoOrderListResponse,
    DemoPaymentRequest,
    HealthResponse,
    IntentResult,
    IntentType,
    Mirror4BAgentChatRequest,
    Mirror4BAgentChatResponse,
    Mirror4BAgentRuntimeContext,
    Mirror4BHistoryMessage,
    Mirror4BProduct,
    NormalizedFilters,
    OrderCreateRequest,
    ParsedShoppingRequest,
    PersonImageUploadResponse,
    PersonImageListResponse,
    ProductDetailResponse,
    ProductCard,
    RecommendationRequest,
    RecommendationResponse,
    SavedResult,
    SavedResultCreateRequest,
    SavedResultListResponse,
    StoreContactResponse,
    TryOnTaskCreateRequest,
    TryOnTaskResponse,
)


app = FastAPI(title="StyleMate Mirror4B API", version="0.3.0")

app.mount(
    "/api/try-on-files",
    StaticFiles(directory=TRY_ON_STORAGE_DIR, check_dir=False),
    name="try-on-files",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

PRODUCT_QUESTION_INTENTS = {
    IntentType.ASK_PRICE,
    IntentType.ASK_MATERIAL,
    IntentType.ASK_STOCK,
    IntentType.ASK_SIZE,
    IntentType.ASK_COLOR,
    IntentType.ASK_BRAND,
    IntentType.ASK_CARE,
    IntentType.ASK_STYLE,
    IntentType.ASK_OCCASION,
    IntentType.OUTFIT_ADVICE,
    IntentType.EXPLAIN_PRODUCT,
    IntentType.VIEW_PRODUCT,
}


async def _mirror4b_catalog() -> dict[str, ProductCard]:
    return build_product_catalog(await mirror4b_sync_products())


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    catalog = await _mirror4b_catalog()
    return HealthResponse(status="ok", product_count=len(catalog))


@app.get("/api/mirror4b/products", response_model=list[Mirror4BProduct])
async def mirror4b_products() -> list[Mirror4BProduct]:
    return await mirror4b_sync_products()


@app.post(
    "/api/mirror4b/chat/agent",
    response_model=Mirror4BAgentChatResponse,
)
async def mirror4b_agent_chat(
    request: Mirror4BAgentChatRequest,
) -> Mirror4BAgentChatResponse:
    return await mirror4b_chat_with_agent(request)


@app.post("/api/recommend", response_model=RecommendationResponse)
async def recommendation(request: RecommendationRequest) -> RecommendationResponse:
    if request.raw_query is None:
        raise HTTPException(status_code=400, detail="Mirror4B 推荐需要 raw_query")
    catalog = await _mirror4b_catalog()
    agent_response = await mirror4b_chat_with_agent(
        Mirror4BAgentChatRequest(
            text=request.raw_query,
            current_page="product_library",
        )
    )
    filter_values = {
        key: value
        for key, value in request.model_dump().items()
        if key in NormalizedFilters.model_fields
    }
    return recommendation_response_from_agent(
        response=agent_response,
        catalog=catalog,
        filters=NormalizedFilters.model_validate(filter_values),
        workflow_state=AgentWorkflowState(current_page="product_library"),
        selected_product_ids=[],
    )


@app.get("/api/products/{product_id}", response_model=ProductDetailResponse)
async def product_detail(product_id: str) -> ProductDetailResponse:
    product = (await _mirror4b_catalog()).get(product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    return ProductDetailResponse(
        product=product,
        data_notice="商品信息、价格与库存来自当前 Mirror4B 商家数据。",
    )


@app.get("/api/store/contact", response_model=StoreContactResponse)
def get_store_contact() -> StoreContactResponse:
    return store_contact()


def _mirror4b_cart_notice(cart: CartResponse) -> CartResponse:
    return cart.model_copy(
        update={
            "demo_notice": "购物车与支付仍为演示流程；商品价格和库存来自 Mirror4B 商家数据。"
        }
    )


@app.get("/api/cart/{profile_id}", response_model=CartResponse)
async def cart_detail(profile_id: str) -> CartResponse:
    catalog = await _mirror4b_catalog()
    return _mirror4b_cart_notice(get_cart(profile_id, catalog.get))


@app.post("/api/cart/{profile_id}/items", response_model=CartResponse)
async def cart_add_item(
    profile_id: str,
    request: CartItemCreateRequest,
) -> CartResponse:
    catalog = await _mirror4b_catalog()
    product = catalog.get(request.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="商品不存在")
    available_sizes = product.business_sizes or ([product.size] if product.size else [])
    if request.selected_size not in available_sizes:
        raise HTTPException(status_code=400, detail="所选尺码不在当前商品尺码范围内")
    cart = get_cart(profile_id, catalog.get)
    current_quantity = next(
        (
            item.quantity
            for item in cart.items
            if item.product.parent_asin == request.product_id
            and item.selected_size == request.selected_size
        ),
        0,
    )
    if current_quantity + request.quantity > product.stock_quantity:
        raise HTTPException(status_code=400, detail="加入数量超过当前门店库存")
    return _mirror4b_cart_notice(
        add_cart_item(
            profile_id,
            request.product_id,
            request.selected_size,
            request.quantity,
            catalog.get,
        )
    )


@app.delete(
    "/api/cart/{profile_id}/items/{product_id}",
    response_model=CartResponse,
)
async def cart_remove_item(
    profile_id: str,
    product_id: str,
    selected_size: str,
) -> CartResponse:
    catalog = await _mirror4b_catalog()
    return _mirror4b_cart_notice(
        remove_cart_item(profile_id, product_id, selected_size, catalog.get)
    )


@app.post("/api/orders", response_model=DemoOrder)
async def order_create(request: OrderCreateRequest) -> DemoOrder:
    catalog = await _mirror4b_catalog()
    cart = get_cart(request.profile_id, catalog.get)
    if not cart.items:
        raise HTTPException(status_code=400, detail="演示购物车为空")
    return create_demo_order(request.profile_id, catalog.get)


@app.get("/api/orders/{profile_id}", response_model=DemoOrderListResponse)
def orders_list(profile_id: str) -> DemoOrderListResponse:
    return DemoOrderListResponse(items=list_demo_orders(profile_id))


@app.post("/api/orders/{order_id}/demo-pay", response_model=DemoOrder)
def order_demo_pay(
    order_id: str,
    request: DemoPaymentRequest,
) -> DemoOrder:
    order = get_demo_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="演示订单不存在")
    if order.profile_id != request.profile_id:
        raise HTTPException(status_code=403, detail="无权访问该演示订单")
    return demo_pay_order(order_id, request.profile_id)


@app.post(
    "/api/try-on/person-image",
    response_model=PersonImageUploadResponse,
)
async def upload_person_image(
    request: Request,
    profile_id: str,
) -> PersonImageUploadResponse:
    content_type = request.headers.get("content-type")
    if content_type is not None:
        content_type = content_type.split(";", 1)[0]
    image_url, width, height = save_person_image(
        await request.body(),
        content_type,
    )
    response = PersonImageUploadResponse(
        image_url=image_url,
        width=width,
        height=height,
    )
    record_person_image(profile_id, image_url, width, height)
    return response


@app.get(
    "/api/person-images/{profile_id}",
    response_model=PersonImageListResponse,
)
def get_person_images(profile_id: str) -> PersonImageListResponse:
    return PersonImageListResponse(items=list_person_images(profile_id))


@app.delete("/api/person-images/{profile_id}/{image_id}")
def remove_person_image(profile_id: str, image_id: str) -> dict[str, str]:
    if not delete_person_image(profile_id, image_id):
        raise HTTPException(status_code=404, detail="人物照片不存在")
    return {"status": "deleted"}


def _try_on_task_response(
    provider_task: ProviderTask,
    result_url: str | None,
) -> TryOnTaskResponse:
    return TryOnTaskResponse(
        task_id=provider_task.task_id,
        status=provider_task.status,
        result_url=result_url,
        error_message=provider_task.error_message,
        usage=provider_task.usage,
    )


@app.post("/api/try-on/tasks", response_model=TryOnTaskResponse)
async def create_try_on_task(
    request: TryOnTaskCreateRequest,
) -> TryOnTaskResponse:
    product = (await _mirror4b_catalog()).get(request.product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="试穿商品不存在")
    if product.image_url is None:
        raise HTTPException(status_code=400, detail="该商品没有可用的服装图片")
    supported_categories = TOP_GARMENT_CATEGORIES | BOTTOM_GARMENT_CATEGORIES
    if product.category not in supported_categories:
        raise HTTPException(
            status_code=400,
            detail="基础版 AI 试衣仅支持上装、下装和连衣裙",
        )
    expected_person_prefix = (
        f"{absolute_public_url('/api/try-on-files/person/')}"
    )
    if not request.person_image_url.startswith(expected_person_prefix):
        raise HTTPException(status_code=400, detail="请先上传当前会话的人物照片")
    provider_task = await submit_try_on_task(
        person_image_url=request.person_image_url,
        garment_image_url=absolute_public_url(product.image_url),
        category=product.category,
    )
    saved_result_url = None
    if provider_task.status == "SUCCEEDED":
        if provider_task.result_image_url is None:
            raise ValueError("successful DashScope task has no image_url")
        saved_result_url = await save_remote_result_image(
            provider_task.task_id,
            provider_task.result_image_url,
        )
    save_task_record(
        provider_task.task_id,
        {
            "task_id": provider_task.task_id,
            "task_type": "try_on",
            "session_id": request.session_id,
            "profile_id": request.profile_id,
            "product_id": request.product_id,
            "status": provider_task.status,
            "remote_result_url": provider_task.result_image_url,
            "result_url": saved_result_url,
            "error_message": provider_task.error_message,
            "api_calls": [provider_task.usage_record],
        },
    )
    return _try_on_task_response(provider_task, saved_result_url)


@app.get(
    "/api/try-on/tasks/{task_id}",
    response_model=TryOnTaskResponse,
)
async def get_try_on_task(
    task_id: str,
    session_id: str,
    profile_id: str,
) -> TryOnTaskResponse:
    if not task_record_exists(task_id):
        raise HTTPException(status_code=404, detail="试穿任务不存在")
    record = load_task_record(task_id)
    if record.get("task_type") != "try_on":
        raise HTTPException(status_code=404, detail="试穿任务不存在")
    if (
        record["session_id"] != session_id
        or record["profile_id"] != profile_id
    ):
        raise HTTPException(status_code=403, detail="无权访问该试穿任务")
    saved_result_url = result_public_url(task_id)
    if saved_result_url is not None:
        usage = record["api_calls"][-1]["usage"]
        if not isinstance(usage, dict):
            raise TypeError("stored DashScope usage must be an object")
        return TryOnTaskResponse(
            task_id=task_id,
            status="SUCCEEDED",
            result_url=saved_result_url,
            usage=usage,
        )
    provider_task = await query_try_on_task(task_id)
    api_calls = record["api_calls"]
    if not isinstance(api_calls, list):
        raise TypeError("try-on api_calls must be a list")
    api_calls.append(provider_task.usage_record)
    if provider_task.status == "SUCCEEDED":
        if provider_task.result_image_url is None:
            raise ValueError("successful DashScope task has no image_url")
        saved_result_url = await save_remote_result_image(
            provider_task.task_id,
            provider_task.result_image_url,
        )
    record.update(
        {
            "status": provider_task.status,
            "remote_result_url": provider_task.result_image_url,
            "result_url": saved_result_url,
            "error_message": provider_task.error_message,
            "api_calls": api_calls,
        }
    )
    save_task_record(task_id, record)
    return _try_on_task_response(provider_task, saved_result_url)


def _background_task_response(
    provider_task: ProviderBackgroundTask,
    result_url: str | None,
) -> BackgroundTaskResponse:
    return BackgroundTaskResponse(
        task_id=provider_task.task_id,
        status=provider_task.status,
        result_url=result_url,
        error_message=provider_task.error_message,
        usage=provider_task.usage,
    )


@app.post("/api/background/tasks", response_model=BackgroundTaskResponse)
async def create_background_task(
    request: BackgroundTaskCreateRequest,
) -> BackgroundTaskResponse:
    allowed_prefixes = (
        absolute_public_url("/api/try-on-files/results/"),
        absolute_public_url("/api/try-on-files/backgrounds/"),
    )
    if not request.base_image_url.startswith(allowed_prefixes):
        raise HTTPException(
            status_code=400,
            detail="请先完成 AI 试穿，再更换背景",
        )
    provider_task = await submit_background_task(
        base_image_url=background_source_data_url(request.base_image_url),
        ref_prompt=request.ref_prompt,
    )
    saved_result_url = None
    if provider_task.status == "SUCCEEDED":
        if provider_task.result_image_url is None:
            raise ValueError("successful background task has no result URL")
        saved_result_url = await save_remote_background_image(
            provider_task.task_id,
            provider_task.result_image_url,
        )
    save_task_record(
        provider_task.task_id,
        {
            "task_id": provider_task.task_id,
            "task_type": "background",
            "session_id": request.session_id,
            "profile_id": request.profile_id,
            "status": provider_task.status,
            "prompt": request.ref_prompt,
            "source_image_url": request.base_image_url,
            "remote_result_url": provider_task.result_image_url,
            "result_url": saved_result_url,
            "error_message": provider_task.error_message,
            "api_calls": [provider_task.usage_record],
        },
    )
    return _background_task_response(provider_task, saved_result_url)


@app.get(
    "/api/background/tasks/{task_id}",
    response_model=BackgroundTaskResponse,
)
async def get_background_task(
    task_id: str,
    session_id: str,
    profile_id: str,
) -> BackgroundTaskResponse:
    if not task_record_exists(task_id):
        raise HTTPException(status_code=404, detail="背景任务不存在")
    record = load_task_record(task_id)
    if record.get("task_type") != "background":
        raise HTTPException(status_code=404, detail="背景任务不存在")
    if (
        record["session_id"] != session_id
        or record["profile_id"] != profile_id
    ):
        raise HTTPException(status_code=403, detail="无权访问该背景任务")
    saved_result_url = background_result_public_url(task_id)
    if saved_result_url is not None:
        usage = record["api_calls"][-1]["usage"]
        if not isinstance(usage, dict):
            raise TypeError("stored DashScope usage must be an object")
        return BackgroundTaskResponse(
            task_id=task_id,
            status="SUCCEEDED",
            result_url=saved_result_url,
            usage=usage,
        )
    provider_task = await query_background_task(task_id)
    api_calls = record["api_calls"]
    if not isinstance(api_calls, list):
        raise TypeError("background api_calls must be a list")
    api_calls.append(provider_task.usage_record)
    if provider_task.status == "SUCCEEDED":
        if provider_task.result_image_url is None:
            raise ValueError("successful background task has no result URL")
        saved_result_url = await save_remote_background_image(
            provider_task.task_id,
            provider_task.result_image_url,
        )
    record.update(
        {
            "status": provider_task.status,
            "remote_result_url": provider_task.result_image_url,
            "result_url": saved_result_url,
            "error_message": provider_task.error_message,
            "api_calls": api_calls,
        }
    )
    save_task_record(task_id, record)
    return _background_task_response(provider_task, saved_result_url)


@app.post("/api/saved-results", response_model=SavedResult)
def create_saved_result(request: SavedResultCreateRequest) -> SavedResult:
    expected_prefix = absolute_public_url("/api/try-on-files/")
    if not request.image_url.startswith(expected_prefix):
        raise HTTPException(
            status_code=400,
            detail="只能保存 StyleMate 生成的试穿图片",
        )
    return save_result(request)


@app.get(
    "/api/saved-results/{profile_id}",
    response_model=SavedResultListResponse,
)
def get_saved_results(profile_id: str) -> SavedResultListResponse:
    return SavedResultListResponse(items=list_saved_results(profile_id))


@app.delete("/api/sessions/{session_id}")
def clear_session(session_id: str, profile_id: str) -> dict[str, str]:
    delete_session(session_id, profile_id)
    return {"status": "cleared"}


def _currency_notice() -> str:
    return str(load_currency_config()["notice"])


def _mirror4b_numeric_product_id(product_id: str | None) -> int | None:
    if product_id is None:
        return None
    return int(product_id)


def _mirror4b_recommended_context(
    product_ids: list[str],
    catalog: dict[str, ProductCard],
) -> list[dict[str, object]]:
    return [
        {
            "id": int(product_id),
            "name": catalog[product_id].title,
            "category": catalog[product_id].category or "",
        }
        for product_id in product_ids
    ]


@app.post("/api/chat", response_model=RecommendationResponse)
async def chat(request: ChatRequest) -> RecommendationResponse:
    context = load_conversation_context(request.session_id, request.profile_id)
    catalog = await _mirror4b_catalog()
    filters = request.current_filters or context.working_filters or NormalizedFilters()
    selected_product_ids = (
        request.selected_product_ids
        if request.selected_product_ids is not None
        else context.selected_product_ids
    )
    workflow_state = request.workflow_state or context.workflow_state
    workflow_state = workflow_state.model_copy(
        update={"selected_product_ids": selected_product_ids}
    )
    current_product_id = request.current_product_id or context.current_product_id
    visible_product_ids = request.visible_product_ids or context.visible_product_ids
    excluded_product_ids = [
        int(product_id) for product_id in filters.excluded_product_ids
    ]
    agent_response = await mirror4b_chat_with_agent(
        Mirror4BAgentChatRequest(
            text=request.message,
            current_page="/chat",
            history=[
                Mirror4BHistoryMessage.model_validate(message)
                for message in context.recent_messages
            ],
            excluded_product_ids=excluded_product_ids,
            context=Mirror4BAgentRuntimeContext(
                has_person_photo=workflow_state.person_image_url is not None,
                has_result_image=(
                    workflow_state.background_image_url is not None
                    or workflow_state.try_on_result_url is not None
                ),
                has_running_task=workflow_state.pending_workflow is not None,
                current_product_id=_mirror4b_numeric_product_id(current_product_id),
                recommended_products=_mirror4b_recommended_context(
                    visible_product_ids,
                    catalog,
                ),
            ),
        )
    )
    response = recommendation_response_from_agent(
        response=agent_response,
        catalog=catalog,
        filters=filters,
        workflow_state=workflow_state,
        selected_product_ids=selected_product_ids,
    )
    next_visible_product_ids = (
        [product.parent_asin for product in response.products]
        if response.response_mode == "RECOMMENDATIONS"
        else visible_product_ids
    )
    memory_status = record_turn(
        session_id=request.session_id,
        profile_id=request.profile_id,
        user_message=request.message,
        assistant_message=response.message,
        filters=filters,
        current_product_id=response.selected_product_id,
        selected_product_ids=response.selected_product_ids,
        visible_product_ids=next_visible_product_ids,
        pending_action=None,
        workflow_state=response.workflow_state,
    )
    return response.model_copy(update={"memory_status": memory_status})


def _assistant_referenced_product_id(
    assistant_message: str,
    response: RecommendationResponse,
) -> str | None:
    normalized_message = assistant_message.casefold()
    matches = list(
        dict.fromkeys(
            product.parent_asin
            for product in response.products
            if product.title.casefold() in normalized_message
        )
    )
    return matches[0] if len(matches) == 1 else None


def _preference_summary(
    preferences: dict[str, str | float | list[str]],
) -> str:
    if not preferences:
        return "你还没有让我长期记住任何穿搭偏好。"
    parts = [f"{field}={value}" for field, value in preferences.items()]
    return "我目前记住的长期偏好有：" + "；".join(parts) + "。"


def _build_selection_request(
    request: ChatRequest,
    filters: NormalizedFilters,
    visible_product_ids: list[str],
) -> ParsedShoppingRequest:
    selected_product_ids = request.selected_product_ids or []
    invisible_product_ids = [
        product_id
        for product_id in selected_product_ids
        if product_id not in visible_product_ids
    ]
    if invisible_product_ids:
        raise HTTPException(
            status_code=400,
            detail="selected products must belong to the visible recommendation list",
        )
    intent = (
        IntentType.EXPLAIN_PRODUCT
        if request.selection_action == "EXPLAIN"
        else IntentType.COMPARE_PRODUCTS
    )
    return ParsedShoppingRequest(
        intent_result=IntentResult(
            intent=intent,
            slots=filters,
            product_references=selected_product_ids,
            confidence=1.0,
            parser_source="RULE",
        ),
        recommendation_request=None,
    )


def _build_base_response(
    request: ChatRequest,
    parsed_request: ParsedShoppingRequest,
    remembered_preferences: dict[str, str | float | list[str]],
    current_filters,
    current_product_id: str | None,
    selected_product_ids: list[str],
) -> RecommendationResponse:
    intent_result = parsed_request.intent_result
    if (
        intent_result.product_references
        and not intent_result.clarify_needed
        and intent_result.intent in PRODUCT_QUESTION_INTENTS
    ):
        product_id = intent_result.product_references[0]
        product = get_product_by_id(product_id)
        if product is None:
            message = "当前选中的商品不存在或已停止推荐，请重新选择一件商品。"
        else:
            question_intents = [
                value
                for value in [intent_result.intent, *intent_result.secondary_intents]
                if value in PRODUCT_QUESTION_INTENTS
            ]
            message = "\n".join(
                answer_product_question(
                    product,
                    question_intent,
                    request.message,
                    intent_result.slots,
                )
                for question_intent in dict.fromkeys(question_intents)
            )
        return RecommendationResponse(
            message=message,
            filters=current_filters or intent_result.slots,
            total_matches=0,
            products=[product] if product is not None else [],
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            response_mode="PRODUCT_ANSWER",
            selected_product_id=product_id,
            selected_product_ids=[product_id],
            currency_notice=_currency_notice(),
        )

    if (
        intent_result.intent == IntentType.COMPARE_PRODUCTS
        and not intent_result.clarify_needed
    ):
        products = [
            product
            for product_id in intent_result.product_references
            if (product := get_product_by_id(product_id)) is not None
        ]
        return RecommendationResponse(
            message=compare_products(products, intent_result.slots),
            filters=intent_result.slots,
            total_matches=len(products),
            products=products,
            filter_stages=[],
            no_result_reason=None,
            intent_result=intent_result,
            response_mode="COMPARISON",
            selected_product_id=None,
            selected_product_ids=[product.parent_asin for product in products],
            currency_notice=_currency_notice(),
        )

    if parsed_request.recommendation_request is not None:
        return recommend(parsed_request.recommendation_request, intent_result)

    if intent_result.intent == IntentType.OUT_OF_SCOPE:
        message = "我目前专注于服装、鞋包、配饰和穿搭相关问题。"
    elif intent_result.intent in {IntentType.CLEAR_FILTERS, IntentType.RESET_SESSION}:
        message = "筛选条件已清空，可以重新告诉我今天想看什么。"
    elif intent_result.intent == IntentType.GREETING:
        message = "你好，我可以帮你挑衣服、鞋包和配饰，也可以一起讨论场合搭配。"
    elif intent_result.intent == IntentType.HELP:
        message = "你可以告诉我品类、颜色、尺码、预算、风格和场合，也可以追问或比较当前商品。"
    elif intent_result.intent == IntentType.THANKS:
        message = "不客气，可以继续调整条件或问我某件商品。"
    elif intent_result.intent in {IntentType.CONFIRM, IntentType.DENY}:
        message = "我明白了，请继续告诉我想保留或修改哪个条件。"
    elif intent_result.intent == IntentType.PREFERENCE_UPDATE:
        message = _preference_summary(remembered_preferences)
    elif intent_result.intent == IntentType.MEMORY_QUERY:
        message = _preference_summary(remembered_preferences)
    elif intent_result.intent == IntentType.MEMORY_DELETE:
        message = "你之前让我长期记住的穿搭偏好已经删除。"
    elif intent_result.intent in {
        IntentType.ASK_TREND,
        IntentType.SEARCH_EXTERNAL_PRODUCT,
        IntentType.ASK_NEW_ARRIVAL,
    }:
        message = "我能理解你在问实时平台信息，但当前还没有接入淘宝或小红书的授权数据源，不能为你编造销量、热度或新品结论。"
    elif intent_result.intent == IntentType.OUTFIT_ADVICE:
        message = "我会根据你提到的单品、场合和风格给出搭配建议；信息不足时会先向你确认。"
    elif intent_result.intent == IntentType.SMALL_TALK:
        message = "我在呀，我是你的穿搭顾问 Mia。可以聊穿搭，也可以直接告诉我今天想找什么。"
    else:
        message = intent_result.clarify_question or "请再告诉我一点具体的穿搭需求。"
    return RecommendationResponse(
        message=message,
        filters=intent_result.slots,
        total_matches=0,
        products=[],
        filter_stages=[],
        no_result_reason=None,
        intent_result=intent_result,
        response_mode="MESSAGE",
        selected_product_id=current_product_id,
        selected_product_ids=selected_product_ids,
        currency_notice=_currency_notice(),
    )


def _legacy_chat(request: ChatRequest) -> RecommendationResponse:
    context: ConversationContext = load_conversation_context(
        request.session_id,
        request.profile_id,
    )
    current_filters = apply_remembered_preferences(
        request.current_filters or context.working_filters,
        context.remembered_preferences,
    )
    selected_product_ids = (
        list(dict.fromkeys(request.selected_product_ids))
        if request.selected_product_ids is not None
        else context.selected_product_ids
    )
    if request.current_product_id is not None:
        current_product_id = request.current_product_id
    elif selected_product_ids:
        current_product_id = (
            selected_product_ids[0] if len(selected_product_ids) == 1 else None
        )
    else:
        current_product_id = context.current_product_id
    visible_product_ids = request.visible_product_ids or context.visible_product_ids

    parsed_request = (
        _build_selection_request(request, current_filters, visible_product_ids)
        if request.selection_action is not None
        else parse_shopping_request(
            request.message,
            request.limit,
            current_filters,
            current_product_id,
            visible_product_ids,
            context.recent_messages,
            context.remembered_preferences,
            selected_product_ids,
            context.pending_action,
        )
    )
    intent_result = parsed_request.intent_result
    remembered_preferences = context.remembered_preferences
    if intent_result.intent == IntentType.PREFERENCE_UPDATE:
        remembered_preferences = remember_explicit_preferences(
            profile_id=request.profile_id,
            operations=intent_result.slot_operations,
            source_message=request.message,
        )
    elif intent_result.intent == IntentType.MEMORY_DELETE:
        delete_profile_preferences(request.profile_id)
        remembered_preferences = {}

    base_response = _build_base_response(
        request,
        parsed_request,
        remembered_preferences,
        current_filters,
        current_product_id,
        selected_product_ids,
    )
    workflow_state = merge_workflow_state(
        context.workflow_state,
        request.workflow_state,
        selected_product_ids,
    )
    agent_plan = plan_agent_action(
        intent_result=intent_result,
        workflow_state=workflow_state,
        user_message=request.message,
    )
    if agent_plan.handled:
        if agent_plan.message is None:
            raise ValueError("handled agent plan requires a message")
        reply = agent_plan.message
    elif UNSUPPORTED_OCCASION_WARNING in intent_result.warnings:
        reply = base_response.message
    else:
        reply = generate_advisor_reply(
            user_message=request.message,
            base_response=base_response,
            recent_messages=context.recent_messages,
            remembered_preferences=remembered_preferences,
        )

    if intent_result.intent == IntentType.RESET_SESSION:
        delete_session(request.session_id, request.profile_id)
    if base_response.response_mode == "RECOMMENDATIONS":
        next_selected_product_ids: list[str] = []
    elif base_response.selected_product_ids:
        next_selected_product_ids = base_response.selected_product_ids
    else:
        next_selected_product_ids = selected_product_ids
    next_product_id = (
        next_selected_product_ids[0]
        if len(next_selected_product_ids) == 1
        else None
    )
    memory_product_id = next_product_id
    if base_response.response_mode == "RECOMMENDATIONS":
        memory_product_id = _assistant_referenced_product_id(reply, base_response)
    next_visible_product_ids = (
        [product.parent_asin for product in base_response.products]
        if base_response.response_mode == "RECOMMENDATIONS"
        else visible_product_ids
    )
    next_workflow_state = agent_plan.workflow_state.model_copy(
        update={"selected_product_ids": next_selected_product_ids}
    )
    next_pending_action = infer_pending_action(
        reply,
        base_response.filters,
        memory_product_id,
    )
    memory_status = record_turn(
        session_id=request.session_id,
        profile_id=request.profile_id,
        user_message=request.message,
        assistant_message=reply,
        filters=base_response.filters,
        current_product_id=memory_product_id,
        selected_product_ids=next_selected_product_ids,
        visible_product_ids=next_visible_product_ids,
        pending_action=next_pending_action,
        workflow_state=next_workflow_state,
    )
    return base_response.model_copy(
        update={
            "message": reply,
            "memory_status": memory_status,
            "selected_product_id": next_product_id,
            "selected_product_ids": next_selected_product_ids,
            "action": agent_plan.action,
            "workflow_state": next_workflow_state,
        }
    )
