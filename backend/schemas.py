from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IntentType(str, Enum):
    RECOMMEND_PRODUCT = "RECOMMEND_PRODUCT"
    BROWSE_PRODUCT = "BROWSE_PRODUCT"
    REFINE_FILTERS = "REFINE_FILTERS"
    CLEAR_FILTERS = "CLEAR_FILTERS"
    RESET_SESSION = "RESET_SESSION"
    EXPLAIN_PRODUCT = "EXPLAIN_PRODUCT"
    REQUEST_ALTERNATIVE = "REQUEST_ALTERNATIVE"
    COMPARE_PRODUCTS = "COMPARE_PRODUCTS"
    VIEW_PRODUCT = "VIEW_PRODUCT"
    ASK_PRICE = "ASK_PRICE"
    ASK_MATERIAL = "ASK_MATERIAL"
    ASK_STOCK = "ASK_STOCK"
    ASK_SIZE = "ASK_SIZE"
    ASK_COLOR = "ASK_COLOR"
    ASK_BRAND = "ASK_BRAND"
    ASK_CARE = "ASK_CARE"
    ASK_STYLE = "ASK_STYLE"
    ASK_OCCASION = "ASK_OCCASION"
    OUTFIT_ADVICE = "OUTFIT_ADVICE"
    SELECT_PERSON_IMAGE = "SELECT_PERSON_IMAGE"
    UPLOAD_PERSON_IMAGE = "UPLOAD_PERSON_IMAGE"
    START_TRY_ON = "START_TRY_ON"
    CHANGE_BACKGROUND = "CHANGE_BACKGROUND"
    SAVE_RESULT = "SAVE_RESULT"
    CONTACT_SALES = "CONTACT_SALES"
    PURCHASE_PRODUCT = "PURCHASE_PRODUCT"
    OPEN_PRODUCT_DETAIL = "OPEN_PRODUCT_DETAIL"
    NAVIGATE_APP = "NAVIGATE_APP"
    ASK_TREND = "ASK_TREND"
    SEARCH_EXTERNAL_PRODUCT = "SEARCH_EXTERNAL_PRODUCT"
    ASK_NEW_ARRIVAL = "ASK_NEW_ARRIVAL"
    PREFERENCE_UPDATE = "PREFERENCE_UPDATE"
    MEMORY_QUERY = "MEMORY_QUERY"
    MEMORY_DELETE = "MEMORY_DELETE"
    SMALL_TALK = "SMALL_TALK"
    GREETING = "GREETING"
    HELP = "HELP"
    THANKS = "THANKS"
    CONFIRM = "CONFIRM"
    DENY = "DENY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    UNKNOWN = "UNKNOWN"


class AgentActionType(str, Enum):
    SELECT_PERSON_IMAGE = "SELECT_PERSON_IMAGE"
    UPLOAD_PERSON_IMAGE = "UPLOAD_PERSON_IMAGE"
    OPEN_PRODUCT_LIBRARY = "OPEN_PRODUCT_LIBRARY"
    OPEN_PRODUCT_DETAIL = "OPEN_PRODUCT_DETAIL"
    START_TRY_ON = "START_TRY_ON"
    CHANGE_BACKGROUND = "CHANGE_BACKGROUND"
    SAVE_RESULT = "SAVE_RESULT"
    CONTACT_SALES = "CONTACT_SALES"
    PURCHASE_PRODUCT = "PURCHASE_PRODUCT"
    NAVIGATE_APP = "NAVIGATE_APP"


class AgentAction(BaseModel):
    type: AgentActionType
    label: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class AgentWorkflowState(BaseModel):
    current_page: Literal[
        "shopping",
        "person_images",
        "product_library",
        "try_on",
        "try_on_result",
        "background",
        "saved",
        "product_detail",
    ] = "shopping"
    person_image_url: str | None = None
    selected_product_ids: list[str] = Field(default_factory=list, max_length=4)
    try_on_result_url: str | None = None
    background_image_url: str | None = None
    pending_workflow: AgentActionType | None = None
    last_completed_action: AgentActionType | None = None


class PersonImageUploadResponse(BaseModel):
    image_url: str
    width: int = Field(ge=150, le=4096)
    height: int = Field(ge=150, le=4096)


class PersonImageRecord(BaseModel):
    id: str
    profile_id: str
    image_url: str
    width: int = Field(ge=150, le=4096)
    height: int = Field(ge=150, le=4096)
    created_at: str


class PersonImageListResponse(BaseModel):
    items: list[PersonImageRecord]


class TryOnTaskCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(min_length=8, max_length=128)
    profile_id: str = Field(min_length=8, max_length=128)
    product_id: str = Field(min_length=1, max_length=128)
    person_image_url: str = Field(min_length=1, max_length=2048)


class TryOnTaskResponse(BaseModel):
    task_id: str
    status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELED",
        "UNKNOWN",
    ]
    result_url: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class BackgroundTaskCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(min_length=8, max_length=128)
    profile_id: str = Field(min_length=8, max_length=128)
    base_image_url: str = Field(min_length=1, max_length=2048)
    ref_prompt: str = Field(min_length=2, max_length=500)


class BackgroundTaskResponse(BaseModel):
    task_id: str
    status: Literal[
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELED",
        "UNKNOWN",
    ]
    result_url: str | None = None
    error_message: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)


class SavedResultCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str = Field(min_length=8, max_length=128)
    profile_id: str = Field(min_length=8, max_length=128)
    image_url: str = Field(min_length=1, max_length=2048)
    source_type: Literal["try_on", "background"]
    product_ids: list[str] = Field(default_factory=list, max_length=4)
    prompt: str | None = Field(default=None, max_length=500)


class SavedResult(BaseModel):
    id: str
    session_id: str
    profile_id: str
    image_url: str
    source_type: Literal["try_on", "background"]
    product_ids: list[str] = Field(default_factory=list)
    prompt: str | None = None
    created_at: str


class SavedResultListResponse(BaseModel):
    items: list[SavedResult]


class ProductDetailResponse(BaseModel):
    product: "ProductCard"
    data_notice: str


class StoreContactResponse(BaseModel):
    assistant_name: str
    channel: str
    contact: str
    service_hours: str
    demo_notice: str


class CartItemCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    product_id: str = Field(min_length=1, max_length=128)
    selected_size: str = Field(min_length=1, max_length=32)
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemResponse(BaseModel):
    product: "ProductCard"
    selected_size: str = Field(min_length=1, max_length=32)
    quantity: int = Field(ge=1, le=99)
    line_total: float = Field(ge=0)


class CartResponse(BaseModel):
    profile_id: str
    items: list[CartItemResponse]
    total: float = Field(ge=0)
    currency: Literal["USD"] = "USD"
    demo_notice: str


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    profile_id: str = Field(min_length=8, max_length=128)


class DemoPaymentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    profile_id: str = Field(min_length=8, max_length=128)


class DemoOrder(BaseModel):
    order_id: str
    profile_id: str
    items: list[CartItemResponse]
    total: float = Field(ge=0)
    currency: Literal["USD"] = "USD"
    status: Literal["DEMO_PENDING", "DEMO_PAID"]
    created_at: str
    paid_at: str | None = None
    demo_notice: str


class DemoOrderListResponse(BaseModel):
    items: list[DemoOrder]


class SlotOperationType(str, Enum):
    SET = "SET"
    ADD = "ADD"
    REMOVE = "REMOVE"
    CLEAR = "CLEAR"
    KEEP = "KEEP"


class SlotOperation(BaseModel):
    field: Literal[
        "category",
        "product_scope",
        "subcategory",
        "gender",
        "age_group",
        "color",
        "color_depth",
        "size",
        "season",
        "min_price",
        "max_price",
        "min_rating",
        "price_currency",
        "brand",
        "material",
        "fit",
        "pattern",
        "sleeve_length",
        "garment_length",
        "neckline",
        "occasions",
        "style_preferences",
        "body_goals",
        "weather",
        "excluded_categories",
        "excluded_colors",
        "excluded_materials",
        "excluded_brands",
        "excluded_styles",
        "negative_colors",
        "excluded_product_ids",
    ]
    operation: SlotOperationType
    value: str | float | list[str] | None = None


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    raw_query: str | None = None
    category: str | None = None
    product_scope: str | None = None
    subcategory: str | None = None
    gender: str | None = None
    age_group: str | None = None
    color: str | None = None
    color_depth: Literal["light", "medium", "dark"] | None = None
    size: str | None = None
    season: str | None = None
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    min_rating: float | None = Field(default=None, ge=0, le=5)
    price_currency: Literal["USD", "CNY"] = "USD"
    brand: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    sleeve_length: str | None = None
    garment_length: str | None = None
    neckline: str | None = None
    occasions: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    body_goals: list[
        Literal[
            "elongate",
            "streamline",
            "tummy_coverage",
            "shoulder_balance",
            "leg_balance",
        ]
    ] = Field(default_factory=list)
    weather: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    excluded_styles: list[str] = Field(default_factory=list)
    negative_colors: list[str] = Field(default_factory=list)
    excluded_product_ids: list[str] = Field(default_factory=list)
    only_in_stock: bool = True
    limit: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def validate_price_range(self) -> "RecommendationRequest":
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("min_price cannot be greater than max_price")
        return self


class NormalizedFilters(BaseModel):
    category: str | None = None
    product_scope: str | None = None
    subcategory: str | None = None
    gender: str | None = None
    age_group: str | None = None
    color: str | None = None
    color_depth: Literal["light", "medium", "dark"] | None = None
    size: str | None = None
    season: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_rating: float | None = Field(default=None, ge=0, le=5)
    price_currency: Literal["USD", "CNY"] = "USD"
    brand: str | None = None
    material: str | None = None
    fit: str | None = None
    pattern: str | None = None
    sleeve_length: str | None = None
    garment_length: str | None = None
    neckline: str | None = None
    occasions: list[str] = Field(default_factory=list)
    style_preferences: list[str] = Field(default_factory=list)
    body_goals: list[
        Literal[
            "elongate",
            "streamline",
            "tummy_coverage",
            "shoulder_balance",
            "leg_balance",
        ]
    ] = Field(default_factory=list)
    weather: list[str] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)
    excluded_colors: list[str] = Field(default_factory=list)
    excluded_materials: list[str] = Field(default_factory=list)
    excluded_brands: list[str] = Field(default_factory=list)
    excluded_styles: list[str] = Field(default_factory=list)
    negative_colors: list[str] = Field(default_factory=list)
    excluded_product_ids: list[str] = Field(default_factory=list)


class PendingActionType(str, Enum):
    RECOMMEND_PRODUCT = "RECOMMEND_PRODUCT"
    SELECT_PRODUCT_CATEGORY = "SELECT_PRODUCT_CATEGORY"


class PendingProductOption(BaseModel):
    category: str
    product_scope: str


class PendingAction(BaseModel):
    action: PendingActionType
    filters: NormalizedFilters
    source_assistant_message: str = Field(min_length=1)
    options: list[PendingProductOption] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_options(self) -> "PendingAction":
        if self.action == PendingActionType.SELECT_PRODUCT_CATEGORY:
            if len(self.options) < 2:
                raise ValueError("product category choice requires at least two options")
        elif self.options:
            raise ValueError("recommendation confirmation cannot contain category options")
        return self


class IntentResult(BaseModel):
    intent: IntentType
    secondary_intents: list[IntentType] = Field(default_factory=list)
    slots: NormalizedFilters
    slot_operations: list[SlotOperation] = Field(default_factory=list)
    requested_fields: list[str] = Field(default_factory=list)
    product_references: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    parser_source: Literal["RULE", "LLM", "RULE_LLM_FUSION"]
    clarify_needed: bool = False
    clarify_question: str | None = None
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    fulfillment_status: Literal[
        "READY",
        "NEED_CLARIFICATION",
        "UNSUPPORTED_DATA",
        "OUT_OF_SCOPE",
    ] = "READY"


class ParsedShoppingRequest(BaseModel):
    intent_result: IntentResult
    recommendation_request: RecommendationRequest | None


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(min_length=8, max_length=128)
    profile_id: str = Field(min_length=8, max_length=128)
    current_filters: NormalizedFilters | None = None
    current_product_id: str | None = None
    selected_product_ids: list[str] | None = Field(default=None, max_length=4)
    selection_action: Literal["EXPLAIN", "COMPARE"] | None = None
    visible_product_ids: list[str] = Field(default_factory=list)
    workflow_state: AgentWorkflowState | None = None
    limit: int = Field(default=12, ge=1, le=50)

    @model_validator(mode="after")
    def validate_selection_action(self) -> "ChatRequest":
        selected_product_ids = self.selected_product_ids or []
        if len(selected_product_ids) != len(set(selected_product_ids)):
            raise ValueError("selected products must be unique")
        selected_count = len(selected_product_ids)
        if self.selection_action == "EXPLAIN" and selected_count != 1:
            raise ValueError("EXPLAIN requires exactly one selected product")
        if self.selection_action == "COMPARE" and not 2 <= selected_count <= 4:
            raise ValueError("COMPARE requires two to four selected products")
        return self


class Claim(BaseModel):
    type: str
    value: str
    source_field: str | None = None
    supported: bool
    evidence: str | None = None


class SafetyViolation(BaseModel):
    code: str
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    message: str
    evidence_field: str | None = None


class SafetyResult(BaseModel):
    status: Literal["PASS", "REWRITE", "BLOCK"]
    violations: list[SafetyViolation] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    original_text: str
    safe_text: str | None = None
    blocked: bool = False
    supported_claim_ratio: float = Field(ge=0, le=1)
    checked_fields: list[str] = Field(default_factory=list)


class ProductCard(BaseModel):
    embedding_row: int = Field(default=0, exclude=True)
    parent_asin: str
    title: str
    brand: str | None
    category: str | None
    color: str | None
    size: str | None
    season: str | None
    material: str | None
    price: float | None
    currency: Literal["USD"] = "USD"
    image_url: str | None
    rating: float
    rating_count: int
    stock_quantity: int
    price_source: str
    size_source: str
    color_source: str
    season_source: str
    stock_source: str
    category_source: str = "unknown"
    business_annotation_version: str | None = None
    business_category: str | None = None
    business_subcategory: str | None = None
    business_colors: list[str] = Field(default_factory=list)
    business_color_depth: str | None = None
    business_styles: list[str] = Field(default_factory=list)
    business_occasions: list[str] = Field(default_factory=list)
    business_fits: list[str] = Field(default_factory=list)
    business_materials: list[str] = Field(default_factory=list)
    business_sizes: list[str] = Field(default_factory=list)
    features_text: str | None = None
    description_text: str | None = None
    score: float = 0.0
    component_scores: dict[str, float] = Field(default_factory=dict)
    matched_features: list[str] = Field(default_factory=list)
    unmatched_features: list[str] = Field(default_factory=list)
    reason: str = ""
    reason_original: str = ""
    reason_safe: str = ""
    safety: SafetyResult | None = None


class FilterStage(BaseModel):
    name: str
    value: str | float | bool | list[str]
    count: int


class MemoryStatus(BaseModel):
    session_id: str
    profile_id: str
    recent_turn_count: int = Field(ge=0)
    remembered_preferences: dict[str, str | float | list[str]] = Field(
        default_factory=dict
    )


class RecommendationResponse(BaseModel):
    message: str
    filters: NormalizedFilters
    total_matches: int
    products: list[ProductCard]
    filter_stages: list[FilterStage]
    no_result_reason: str | None
    intent_result: IntentResult | None = None
    response_mode: Literal[
        "RECOMMENDATIONS", "PRODUCT_ANSWER", "COMPARISON", "MESSAGE"
    ] = (
        "RECOMMENDATIONS"
    )
    selected_product_id: str | None = None
    selected_product_ids: list[str] = Field(default_factory=list)
    currency_notice: str
    memory_status: MemoryStatus | None = None
    action: AgentAction | None = None
    workflow_state: AgentWorkflowState | None = None


class HealthResponse(BaseModel):
    status: str
    product_count: int
