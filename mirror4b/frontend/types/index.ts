export type NormalizedFilters = {
  category: string | null;
  product_scope: string | null;
  subcategory: string | null;
  gender: string | null;
  age_group: string | null;
  color: string | null;
  color_depth: "light" | "medium" | "dark" | null;
  size: string | null;
  season: string | null;
  min_price: number | null;
  max_price: number | null;
  min_rating: number | null;
  price_currency: "USD" | "CNY";
  brand: string | null;
  material: string | null;
  fit: string | null;
  pattern: string | null;
  sleeve_length: string | null;
  garment_length: string | null;
  neckline: string | null;
  occasions: string[];
  style_preferences: string[];
  body_goals: ("elongate" | "streamline" | "tummy_coverage" | "shoulder_balance" | "leg_balance")[];
  weather: string[];
  excluded_categories: string[];
  excluded_colors: string[];
  excluded_materials: string[];
  excluded_brands: string[];
  excluded_styles: string[];
  negative_colors: string[];
  excluded_product_ids: string[];
};

export type IntentType =
  | "RECOMMEND_PRODUCT"
  | "BROWSE_PRODUCT"
  | "REFINE_FILTERS"
  | "CLEAR_FILTERS"
  | "RESET_SESSION"
  | "EXPLAIN_PRODUCT"
  | "REQUEST_ALTERNATIVE"
  | "COMPARE_PRODUCTS"
  | "VIEW_PRODUCT"
  | "ASK_PRICE"
  | "ASK_MATERIAL"
  | "ASK_STOCK"
  | "ASK_SIZE"
  | "ASK_COLOR"
  | "ASK_BRAND"
  | "ASK_CARE"
  | "ASK_STYLE"
  | "ASK_OCCASION"
  | "OUTFIT_ADVICE"
  | "SELECT_PERSON_IMAGE"
  | "UPLOAD_PERSON_IMAGE"
  | "START_TRY_ON"
  | "CHANGE_BACKGROUND"
  | "SAVE_RESULT"
  | "CONTACT_SALES"
  | "PURCHASE_PRODUCT"
  | "OPEN_PRODUCT_DETAIL"
  | "NAVIGATE_APP"
  | "ASK_TREND"
  | "SEARCH_EXTERNAL_PRODUCT"
  | "ASK_NEW_ARRIVAL"
  | "PREFERENCE_UPDATE"
  | "MEMORY_QUERY"
  | "MEMORY_DELETE"
  | "SMALL_TALK"
  | "GREETING"
  | "HELP"
  | "THANKS"
  | "CONFIRM"
  | "DENY"
  | "OUT_OF_SCOPE"
  | "UNKNOWN";

export type AgentActionType =
  | "SELECT_PERSON_IMAGE"
  | "UPLOAD_PERSON_IMAGE"
  | "OPEN_PRODUCT_LIBRARY"
  | "OPEN_PRODUCT_DETAIL"
  | "START_TRY_ON"
  | "CHANGE_BACKGROUND"
  | "SAVE_RESULT"
  | "CONTACT_SALES"
  | "PURCHASE_PRODUCT"
  | "NAVIGATE_APP";

export type AgentAction = {
  type: AgentActionType;
  label: string;
  payload: Record<string, unknown>;
};

export type AgentWorkflowState = {
  current_page: "shopping" | "person_images" | "product_library" | "try_on" | "try_on_result" | "background" | "saved" | "product_detail";
  person_image_url: string | null;
  selected_product_ids: string[];
  try_on_result_url: string | null;
  background_image_url: string | null;
  pending_workflow: AgentActionType | null;
  last_completed_action: AgentActionType | null;
};

export type PersonImageUploadResponse = {
  image_url: string;
  width: number;
  height: number;
};

export type PersonImageRecord = {
  id: string;
  profile_id: string;
  image_url: string;
  width: number;
  height: number;
  created_at: string;
};

export type TryOnTaskStatus =
  | "PENDING"
  | "RUNNING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELED"
  | "UNKNOWN";

export type TryOnTaskResponse = {
  task_id: string;
  status: TryOnTaskStatus;
  result_url: string | null;
  error_message: string | null;
  usage: Record<string, unknown>;
};

export type BackgroundTaskResponse = {
  task_id: string;
  status: TryOnTaskStatus;
  result_url: string | null;
  error_message: string | null;
  usage: Record<string, unknown>;
};

export type SavedResult = {
  id: string;
  session_id: string;
  profile_id: string;
  image_url: string;
  source_type: "try_on" | "background";
  product_ids: string[];
  prompt: string | null;
  created_at: string;
};

export type ProductDetailResponse = {
  product: ProductCard;
  data_notice: string;
};

export type StoreContact = {
  assistant_name: string;
  channel: string;
  contact: string;
  service_hours: string;
  demo_notice: string;
};

export type CartItem = {
  product: ProductCard;
  selected_size: string;
  quantity: number;
  line_total: number;
};

export type Cart = {
  profile_id: string;
  items: CartItem[];
  total: number;
  currency: "USD" | "CNY";
  demo_notice: string;
};

export type DemoOrder = {
  order_id: string;
  profile_id: string;
  items: CartItem[];
  total: number;
  currency: "USD" | "CNY";
  status: "DEMO_PENDING" | "DEMO_PAID";
  created_at: string;
  paid_at: string | null;
  demo_notice: string;
};

export type IntentResult = {
  intent: IntentType;
  secondary_intents: IntentType[];
  slots: NormalizedFilters;
  slot_operations: {
    field: keyof NormalizedFilters;
    operation: "SET" | "ADD" | "REMOVE" | "CLEAR" | "KEEP";
    value: string | number | string[] | null;
  }[];
  requested_fields: string[];
  product_references: string[];
  confidence: number;
  parser_source: "RULE" | "LLM" | "RULE_LLM_FUSION";
  clarify_needed: boolean;
  clarify_question: string | null;
  conflicts: string[];
  warnings: string[];
  fulfillment_status: "READY" | "NEED_CLARIFICATION" | "UNSUPPORTED_DATA" | "OUT_OF_SCOPE";
};

export type Claim = {
  type: string;
  value: string;
  source_field: string | null;
  supported: boolean;
  evidence: string | null;
};

export type SafetyViolation = {
  code: string;
  severity: "LOW" | "MEDIUM" | "HIGH";
  message: string;
  evidence_field: string | null;
};

export type SafetyResult = {
  status: "PASS" | "REWRITE" | "BLOCK";
  violations: SafetyViolation[];
  claims: Claim[];
  original_text: string;
  safe_text: string | null;
  blocked: boolean;
  supported_claim_ratio: number;
  checked_fields: string[];
};

export type ProductCard = {
  parent_asin: string;
  title: string;
  brand: string | null;
  category: string | null;
  color: string | null;
  size: string | null;
  season: string | null;
  material: string | null;
  price: number | null;
  currency: "USD" | "CNY";
  image_url: string | null;
  rating: number;
  rating_count: number;
  stock_quantity: number;
  price_source: string;
  size_source: string;
  color_source: string;
  season_source: string;
  stock_source: string;
  category_source: string;
  business_sizes: string[];
  features_text: string | null;
  description_text: string | null;
  score: number;
  component_scores: Record<string, number>;
  matched_features: string[];
  unmatched_features: string[];
  reason: string;
  reason_original: string;
  reason_safe: string;
  safety: SafetyResult | null;
};

export type FilterStage = {
  name: string;
  value: string | number | boolean | string[];
  count: number;
};

export type MemoryStatus = {
  session_id: string;
  profile_id: string;
  recent_turn_count: number;
  remembered_preferences: Record<string, string | number | string[]>;
};

export type RecommendationResponse = {
  message: string;
  filters: NormalizedFilters;
  total_matches: number;
  products: ProductCard[];
  filter_stages: FilterStage[];
  no_result_reason: string | null;
  intent_result: IntentResult | null;
  response_mode: "RECOMMENDATIONS" | "PRODUCT_ANSWER" | "COMPARISON" | "MESSAGE";
  selected_product_id: string | null;
  selected_product_ids: string[];
  currency_notice: string;
  memory_status: MemoryStatus | null;
  action: AgentAction | null;
  workflow_state: AgentWorkflowState | null;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
};

export type PersistedSession = {
  sessionId: string;
  messages: ChatMessage[];
  result: RecommendationResponse | null;
  selectedProductIds: string[];
  comparisonProductIds: string[];
  intentResult: IntentResult | null;
  memoryStatus: MemoryStatus | null;
  agentAction?: AgentAction | null;
  workflowState?: AgentWorkflowState | null;
};
