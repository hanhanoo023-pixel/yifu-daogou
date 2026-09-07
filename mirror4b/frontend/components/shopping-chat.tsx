"use client";

import {
  ArrowUpRight,
  Check,
  ChevronRight,
  CreditCard,
  Download,
  Images,
  LoaderCircle,
  Maximize2,
  MessageCircleMore,
  Minus,
  PackageCheck,
  PhoneCall,
  Plus,
  RotateCcw,
  Scale,
  Search,
  Send,
  ShoppingBag,
  ShoppingCart,
  Sparkles,
  Star,
  Upload,
  UserRound,
  Trash2,
  X,
} from "lucide-react";
import { ChangeEvent, FormEvent, KeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import Image from "next/image";

import {
  addCartItem,
  clearConversationMemory,
  createBackgroundTask,
  createDemoOrder,
  createTryOnTask,
  getCart,
  getBackgroundTask,
  getPersonImages,
  getProductDetail,
  getSavedResults,
  getStoreContact,
  getTryOnTask,
  payDemoOrder,
  deletePersonImage,
  removeCartItem,
  saveGeneratedResult,
  sendChatMessage,
  uploadPersonImage,
} from "@/lib/api";
import type {
  AgentAction,
  AgentWorkflowState,
  Cart,
  ChatMessage,
  DemoOrder,
  IntentResult,
  MemoryStatus,
  NormalizedFilters,
  PersonImageRecord,
  PersistedSession,
  ProductCard,
  ProductDetailResponse,
  RecommendationResponse,
  SavedResult,
  StoreContact,
} from "@/types";

const STORAGE_KEY = "stylemate-shopping-session-v10";
const PROFILE_KEY = "stylemate-shopping-profile-v1";

const starterPrompts = [
  "我想找一些适合夏天去海边玩的衣服",
  "找一条300元以内的黑色连衣裙",
  "推荐蓝色XL码上衣，不太喜欢粉色",
  "找一双白色夏季鞋子，不要黄色",
];

const followUpPrompts = ["换成蓝色", "尺码改成XL", "不限制季节", "换成连衣裙"];

function AdvisorAvatar() {
  return <Image className="advisor-avatar-image" src="/mia-avatar.png" alt="穿搭顾问 Mia" width={28} height={28} loading="eager" unoptimized />;
}

const intentLabels: Record<string, string> = {
  RECOMMEND_PRODUCT: "推荐商品",
  BROWSE_PRODUCT: "浏览商品",
  REFINE_FILTERS: "修改条件",
  CLEAR_FILTERS: "清空条件",
  RESET_SESSION: "重置会话",
  EXPLAIN_PRODUCT: "解释商品",
  REQUEST_ALTERNATIVE: "更换推荐",
  COMPARE_PRODUCTS: "比较商品",
  VIEW_PRODUCT: "查看商品",
  ASK_PRICE: "询问价格",
  ASK_MATERIAL: "询问材质",
  ASK_STOCK: "询问库存",
  ASK_SIZE: "询问尺码",
  ASK_COLOR: "询问颜色",
  ASK_BRAND: "询问品牌",
  ASK_CARE: "询问洗护",
  ASK_STYLE: "询问风格",
  ASK_OCCASION: "询问场景",
  OUTFIT_ADVICE: "穿搭建议",
  SELECT_PERSON_IMAGE: "选择人物照片",
  UPLOAD_PERSON_IMAGE: "上传人物照片",
  START_TRY_ON: "AI 试穿",
  CHANGE_BACKGROUND: "更换背景",
  SAVE_RESULT: "保存结果",
  CONTACT_SALES: "咨询导购",
  PURCHASE_PRODUCT: "购买商品",
  OPEN_PRODUCT_DETAIL: "商品详情",
  NAVIGATE_APP: "打开功能页面",
  ASK_TREND: "流行趋势",
  SEARCH_EXTERNAL_PRODUCT: "外部商品搜索",
  ASK_NEW_ARRIVAL: "新品查询",
  PREFERENCE_UPDATE: "记住偏好",
  MEMORY_QUERY: "查看记忆",
  MEMORY_DELETE: "删除记忆",
  SMALL_TALK: "轻松聊天",
  GREETING: "打招呼",
  HELP: "使用帮助",
  THANKS: "致谢",
  CONFIRM: "确认",
  DENY: "否定",
  OUT_OF_SCOPE: "超出范围",
  UNKNOWN: "需要澄清",
};

function createMessageId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function wait(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

const filterLabels: Record<keyof NormalizedFilters, string> = {
  category: "品类",
  product_scope: "商品范围",
  subcategory: "细分类",
  gender: "性别",
  age_group: "年龄",
  color: "颜色",
  color_depth: "颜色深浅",
  size: "尺码",
  season: "季节",
  min_price: "最低价",
  max_price: "最高价",
  min_rating: "最低评分",
  price_currency: "预算币种",
  brand: "品牌",
  material: "材质",
  fit: "版型",
  pattern: "图案",
  sleeve_length: "袖长",
  garment_length: "衣长",
  neckline: "领型",
  occasions: "场景",
  style_preferences: "风格",
  body_goals: "身材诉求",
  weather: "天气",
  excluded_categories: "排除品类",
  excluded_colors: "排除颜色",
  excluded_materials: "排除材质",
  excluded_brands: "排除品牌",
  excluded_styles: "排除风格",
  negative_colors: "不偏好颜色",
  excluded_product_ids: "排除商品",
};

const valueLabels: Record<string, string> = {
  top: "上衣",
  dress: "连衣裙",
  pants: "长裤",
  shorts: "短裤",
  skirt: "半身裙",
  outerwear: "外套",
  sweater: "毛衣 / 卫衣",
  swimwear: "泳装",
  underwear: "内衣",
  shoes: "鞋履",
  bag: "箱包",
  jewelry: "首饰",
  watch: "手表",
  accessory: "配饰",
  set: "套装",
  costume: "服装道具",
  yellow: "黄色",
  black: "黑色",
  white: "白色",
  blue: "蓝色",
  red: "红色",
  green: "绿色",
  pink: "粉色",
  purple: "紫色",
  brown: "棕色",
  gray: "灰色",
  beige: "米色",
  orange: "橙色",
  silver: "银色",
  gold: "金色",
  multicolor: "多色",
  light: "浅色",
  medium: "中等深浅",
  dark: "深色",
  spring: "春季",
  summer: "夏季",
  autumn: "秋季",
  winter: "冬季",
  cotton: "棉",
  wool: "羊毛",
  polyester: "聚酯纤维",
  leather: "皮革",
  silk: "真丝",
  lace: "蕾丝",
  denim: "牛仔",
  linen: "亚麻",
  USD: "美元",
  CNY: "人民币",
  gentle: "温柔",
  casual: "休闲",
  minimalist: "简约",
  formal: "正式",
  sporty: "运动",
  retro: "复古",
  streetwear: "街头",
  elegant: "优雅",
  elongate: "显高",
  streamline: "显瘦",
  tummy_coverage: "遮腹",
  shoulder_balance: "修饰肩部",
  leg_balance: "修饰腿型",
  beach: "海边",
  vacation: "度假",
  commute: "通勤",
  interview: "面试",
  date: "约会",
  wedding: "婚礼",
  sports: "运动",
  daily: "日常",
  clothing: "服装",
  footwear: "鞋履",
  bags: "箱包",
  accessories: "配饰",
};

const occasionLabels: Record<string, string> = {
  daily: "日常",
  school: "上学 / 校园",
  commute: "上班 / 通勤",
  interview: "面试",
  formal: "商务 / 会议",
  date: "约会",
  party: "聚会 / 派对 / 年会",
  dining: "聚餐",
  wedding: "婚礼",
  ceremony: "毕业 / 典礼",
  vacation: "旅行 / 度假",
  beach: "海边",
  shopping: "逛街 / 购物",
  sports: "运动 / 健身",
  outdoor: "户外 / 徒步 / 露营",
  home: "居家",
  sleep: "睡眠",
  photo: "拍照 / 写真",
  performance: "演出 / 舞台",
  festival: "节日活动",
};

const chineseRanks: Record<string, number> = {
  一: 1,
  二: 2,
  三: 3,
  四: 4,
  五: 5,
  六: 6,
  七: 7,
  八: 8,
  九: 9,
  十: 10,
  十一: 11,
  十二: 12,
};

function resolveProductReference(
  message: string,
  products: ProductCard[],
): string | null | undefined {
  const match = message.match(/第\s*(\d+|一|二|三|四|五|六|七|八|九|十|十一|十二)\s*件/);
  if (!match) return undefined;
  const rank = /^\d+$/.test(match[1]) ? Number(match[1]) : chineseRanks[match[1]];
  return products[rank - 1]?.parent_asin || null;
}

function formatFilterValue(
  field: keyof NormalizedFilters,
  value: string | number | string[],
) {
  const labels = field === "occasions" ? occasionLabels : valueLabels;
  if (Array.isArray(value)) {
    return value.map((item) => labels[item] || item).join("、");
  }
  if (typeof value === "number") {
    return value.toFixed(2);
  }
  return labels[value] || value;
}

function formatMoney(value: number, currency: "USD" | "CNY") {
  return `${currency === "CNY" ? "¥" : "$"}${value.toFixed(2)}`;
}

function availableProductSizes(product: ProductCard): string[] {
  const sizes = product.business_sizes.length > 0
    ? product.business_sizes
    : product.size
      ? [product.size]
      : [];
  return [...new Set(sizes)];
}

function ProductItem({
  product,
  rank,
  focused,
  inComparison,
  onAsk,
  onTryOn,
  onOpenDetail,
  onAddCart,
  onToggleComparison,
}: {
  product: ProductCard;
  rank: number;
  focused: boolean;
  inComparison: boolean;
  onAsk: () => void;
  onTryOn: () => void;
  onOpenDetail: () => void;
  onAddCart: () => void;
  onToggleComparison: () => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);

  return (
    <article className={`product-card ${focused || inComparison ? "product-card-selected" : ""}`}>
      <button className="product-image-wrap" type="button" onClick={onOpenDetail}>
        <span className="rank-badge">第 {rank} 件</span>
        {product.image_url && !imageFailed ? (
          <img
            className="product-image"
            src={product.image_url}
            alt={product.title}
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="image-placeholder">
            <ShoppingBag size={30} />
            <span>暂无图片</span>
          </div>
        )}
        <span className={`stock-badge ${product.stock_quantity <= 5 ? "low-stock" : ""}`}>
          <PackageCheck size={14} />
          {product.stock_source === "synthetic_demo" ? "演示库存 " : "库存 "}
          {product.stock_quantity} 件
        </span>
      </button>

      <div className="product-body">
        <div className="brand-row">
          <span>{product.brand || "品牌未知"}</span>
          <span className="rating">
            <Star size={14} fill="currentColor" />
            {product.rating.toFixed(1)}
            <small>({product.rating_count})</small>
          </span>
        </div>
        <h3>{product.title}</h3>
        <div className="product-tags">
          {product.color && <span>{valueLabels[product.color] || product.color}</span>}
          {product.size && <span>{product.size}{product.size_source === "synthetic_demo" ? "（演示）" : ""}</span>}
          {product.season && <span>{valueLabels[product.season] || product.season}{product.season_source === "synthetic_demo" ? "（演示）" : ""}</span>}
          {product.matched_features.slice(0, 2).map((feature) => <span key={feature}>{feature}</span>)}
        </div>
        <div className="product-action-row">
          <button type="button" className="try-on-product-button" onClick={onTryOn}>
            <Sparkles size={13} />
            AI 试穿
          </button>
          <button type="button" className="ask-product-button" onClick={onAsk}>
            <MessageCircleMore size={13} />
            {focused ? (inComparison ? "正在对比" : "正在聊这件") : "问问 Mia"}
          </button>
          <button
            type="button"
            className={`compare-product-button ${inComparison ? "compare-product-button-active" : ""}`}
            onClick={onToggleComparison}
          >
            {inComparison ? <X size={13} /> : <Scale size={13} />}
            {inComparison ? "取消对比" : "加入对比"}
          </button>
        </div>
        <div className="price-row">
          <strong>
            {product.price === null ? "价格未知" : formatMoney(product.price, product.currency)}
            {product.price_source === "synthetic_demo" ? " · 演示" : ""}
          </strong>
          <button type="button" className="detail-link" onClick={onOpenDetail} aria-label="查看商品详情">
            查看商品 <ArrowUpRight size={15} />
          </button>
        </div>
        <button type="button" className="add-cart-button" onClick={onAddCart}>
          <ShoppingCart size={14} /> 加入演示购物车
        </button>
      </div>
    </article>
  );
}

function FilterChips({ filters }: { filters: NormalizedFilters }) {
  return (
    <div className="filter-chips">
      {(Object.entries(filters) as [keyof NormalizedFilters, string | number | string[] | null][]).map(
        ([key, value]) =>
          value !== null &&
          (!Array.isArray(value) || value.length > 0) &&
          (key !== "price_currency" || filters.min_price !== null || filters.max_price !== null) && (
            <span key={key}>
              <Check size={13} />
              <small>{filterLabels[key]}</small>
              {formatFilterValue(key, value)}
            </span>
          ),
      )}
    </div>
  );
}

export function ShoppingChat() {
  const [sessionId, setSessionId] = useState("");
  const [profileId, setProfileId] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [selectedProductIds, setSelectedProductIds] = useState<string[]>([]);
  const [comparisonProductIds, setComparisonProductIds] = useState<string[]>([]);
  const [lastIntentResult, setLastIntentResult] = useState<IntentResult | null>(null);
  const [memoryStatus, setMemoryStatus] = useState<MemoryStatus | null>(null);
  const [agentAction, setAgentAction] = useState<AgentAction | null>(null);
  const [workflowState, setWorkflowState] = useState<AgentWorkflowState | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [tryOnBusy, setTryOnBusy] = useState(false);
  const [tryOnStatus, setTryOnStatus] = useState<string | null>(null);
  const [tryOnPanelOpen, setTryOnPanelOpen] = useState(true);
  const [tryOnPanelHeight, setTryOnPanelHeight] = useState(300);
  const [previewImageUrl, setPreviewImageUrl] = useState<string | null>(null);
  const [backgroundPrompt, setBackgroundPrompt] = useState("");
  const [lastBackgroundPrompt, setLastBackgroundPrompt] = useState<string | null>(null);
  const [savedResults, setSavedResults] = useState<SavedResult[]>([]);
  const [personImages, setPersonImages] = useState<PersonImageRecord[]>([]);
  const [personCenterOpen, setPersonCenterOpen] = useState(false);
  const [personUploadNotice, setPersonUploadNotice] = useState<string | null>(null);
  const [productDetail, setProductDetail] = useState<ProductDetailResponse | null>(null);
  const [storeContact, setStoreContact] = useState<StoreContact | null>(null);
  const [salesCallRequested, setSalesCallRequested] = useState(false);
  const [cart, setCart] = useState<Cart | null>(null);
  const [cartOpen, setCartOpen] = useState(false);
  const [pendingCartProduct, setPendingCartProduct] = useState<ProductCard | null>(null);
  const [pendingCartSize, setPendingCartSize] = useState("");
  const [pendingCartQuantity, setPendingCartQuantity] = useState(1);
  const [cartItemBusy, setCartItemBusy] = useState(false);
  const [activeOrder, setActiveOrder] = useState<DemoOrder | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hydrated, setHydrated] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const personImageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const savedProfileId = localStorage.getItem(PROFILE_KEY);
    const nextProfileId = savedProfileId || crypto.randomUUID();
    if (!savedProfileId) localStorage.setItem(PROFILE_KEY, nextProfileId);
    setProfileId(nextProfileId);

    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) {
      const session = JSON.parse(saved) as PersistedSession;
      setSessionId(session.sessionId);
      setMessages(session.messages);
      setResult(session.result);
      setSelectedProductIds(session.selectedProductIds);
      setComparisonProductIds(session.comparisonProductIds);
      setLastIntentResult(session.intentResult);
      setMemoryStatus(session.memoryStatus);
      setAgentAction(session.agentAction || null);
      setWorkflowState(session.workflowState || null);
    } else setSessionId(crypto.randomUUID());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) {
      const session: PersistedSession = {
        sessionId,
        messages,
        result,
        selectedProductIds,
        comparisonProductIds,
        intentResult: lastIntentResult,
        memoryStatus,
        agentAction,
        workflowState,
      };
      if (sessionId) localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    }
  }, [hydrated, sessionId, messages, result, selectedProductIds, comparisonProductIds, lastIntentResult, memoryStatus, agentAction, workflowState]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (!profileId) return;
    void Promise.all([getSavedResults(profileId), getCart(profileId), getPersonImages(profileId)])
      .then(([saved, loadedCart, loadedPersonImages]) => {
        setSavedResults(saved);
        setCart(loadedCart);
        setPersonImages(loadedPersonImages);
      })
      .catch((loadError) => {
        setError(loadError instanceof Error ? loadError.message : "个人数据加载失败");
      });
  }, [profileId]);

  useEffect(() => {
    if (agentAction?.type !== "CHANGE_BACKGROUND") return;
    const prompt = agentAction.payload.prompt;
    if (typeof prompt === "string") setBackgroundPrompt(prompt);
  }, [agentAction]);

  useEffect(() => {
    if (workflowState?.try_on_result_url || workflowState?.background_image_url) {
      setTryOnPanelOpen(true);
    }
  }, [workflowState?.try_on_result_url, workflowState?.background_image_url]);

  function clampTryOnPanelHeight(height: number): number {
    return Math.min(Math.max(320, window.innerHeight - 350), Math.max(200, height));
  }

  function startTryOnPanelResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = tryOnPanelHeight;
    document.body.style.cursor = "ns-resize";
    document.body.style.userSelect = "none";

    const handlePointerMove = (pointerEvent: PointerEvent) => {
      setTryOnPanelHeight(clampTryOnPanelHeight(
        startHeight + startY - pointerEvent.clientY,
      ));
    };
    const handlePointerUp = () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("pointercancel", handlePointerUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("pointercancel", handlePointerUp);
  }

  async function submitMessage(
    content: string,
    selectionAction: "EXPLAIN" | "COMPARE" | null = null,
    explicitProductIds: string[] | null = null,
    workflowStateOverride: AgentWorkflowState | null = null,
  ) {
    const trimmed = content.trim();
    if (!trimmed || loading || !sessionId || !profileId) return;

    const userMessage: ChatMessage = {
      id: createMessageId(),
      role: "user",
      content: trimmed,
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setLoading(true);
    setError(null);

    try {
      const referencedProductId = resolveProductReference(
        trimmed,
        result?.products || [],
      );
      const nextSelectedProductIds = explicitProductIds || (
        referencedProductId === undefined
          ? selectedProductIds
          : referencedProductId
            ? [referencedProductId]
            : []
      );
      const productContext =
        nextSelectedProductIds.length === 1 ? nextSelectedProductIds[0] : null;
      if (referencedProductId !== undefined) {
        setSelectedProductIds(nextSelectedProductIds);
      }
      const response = await sendChatMessage(
        trimmed,
        sessionId,
        profileId,
        result?.filters || null,
        productContext,
        nextSelectedProductIds,
        result?.products.map((product) => product.parent_asin) || [],
        workflowStateOverride || workflowState,
        selectionAction,
      );
      setLastIntentResult(response.intent_result);
      setMemoryStatus(response.memory_status);
      setAgentAction(response.action);
      setWorkflowState(response.workflow_state);
      if (response.response_mode === "RECOMMENDATIONS") {
        setResult(response);
        setSelectedProductIds([]);
        setComparisonProductIds([]);
      } else if (
        response.intent_result?.intent === "CLEAR_FILTERS" ||
        response.intent_result?.intent === "RESET_SESSION"
      ) {
        setResult(null);
        setSelectedProductIds([]);
        setComparisonProductIds([]);
      } else {
        setSelectedProductIds(response.selected_product_ids);
      }
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: "assistant",
          content: response.message,
        },
      ]);
    } catch (requestError) {
      const message = requestError instanceof Error ? requestError.message : "导购服务请求失败";
      setError(message);
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage(input);
    }
  }

  async function clearConversation() {
    if (sessionId && profileId) {
      await clearConversationMemory(sessionId, profileId);
    }
    localStorage.removeItem(STORAGE_KEY);
    setSessionId(crypto.randomUUID());
    setMessages([]);
    setResult(null);
    setSelectedProductIds([]);
    setComparisonProductIds([]);
    setLastIntentResult(null);
    setMemoryStatus(null);
    setAgentAction(null);
    setWorkflowState(null);
    setTryOnStatus(null);
    setTryOnPanelOpen(true);
    setTryOnPanelHeight(300);
    setPreviewImageUrl(null);
    setBackgroundPrompt("");
    setLastBackgroundPrompt(null);
    setPersonUploadNotice(null);
    setInput("");
    setError(null);
  }

  function askProduct(product: ProductCard) {
    setSelectedProductIds([product.parent_asin]);
    void submitMessage(
      `想了解「${product.title}」`,
      "EXPLAIN",
      [product.parent_asin],
    );
  }

  function tryOnProduct(product: ProductCard) {
    setSelectedProductIds([product.parent_asin]);
    void submitMessage("试穿这件", null, [product.parent_asin]);
  }

  async function handlePersonImageChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setTryOnBusy(true);
    setTryOnStatus("正在上传人物照片…");
    setPersonUploadNotice(null);
    setError(null);
    try {
      await uploadPersonImage(file, profileId);
      setPersonImages(await getPersonImages(profileId));
      setTryOnStatus(null);
      setPersonUploadNotice("照片已保存。请点击照片进行选择；再次点击可取消选择。");
    } finally {
      event.target.value = "";
      setTryOnBusy(false);
    }
  }

  function togglePersonImageSelection(item: PersonImageRecord) {
    const isSelected = workflowState?.person_image_url === item.image_url;
    if (isSelected) {
      setWorkflowState((current) => current ? {
        ...current,
        current_page: "person_images",
        person_image_url: null,
        last_completed_action: null,
      } : current);
      setAgentAction((current) => current?.type === "START_TRY_ON" ? null : current);
      setTryOnStatus(null);
      setPersonUploadNotice("已取消选择，当前不会使用任何人物照片进行试穿。");
      return;
    }

    const pendingWorkflow = workflowState?.pending_workflow || null;
    const workflowProductIds = workflowState?.selected_product_ids.length
      ? workflowState.selected_product_ids
      : selectedProductIds;
    const selectedState: AgentWorkflowState = {
      current_page: "person_images",
      person_image_url: item.image_url,
      selected_product_ids: workflowProductIds,
      try_on_result_url: workflowState?.try_on_result_url || null,
      background_image_url: workflowState?.background_image_url || null,
      pending_workflow: pendingWorkflow,
      last_completed_action: null,
    };
    setWorkflowState(selectedState);
    setTryOnStatus("已选择人物照片");
    setPersonUploadNotice("已选择这张照片。再次点击可取消，关闭人物中心后再主动开始 AI 试穿。");
    if (workflowProductIds.length > 0) {
      setAgentAction({
        type: "START_TRY_ON",
        label: "开始 AI 试穿",
        payload: {
          route_name: "try_on",
          person_image_url: item.image_url,
          product_ids: workflowProductIds,
        },
      });
    } else {
      setAgentAction(null);
    }
  }

  async function removePersonImage(item: PersonImageRecord) {
    await deletePersonImage(profileId, item.id);
    setPersonImages((current) => current.filter((value) => value.id !== item.id));
    if (workflowState?.person_image_url === item.image_url) {
      setWorkflowState((current) => current ? {
        ...current,
        person_image_url: null,
      } : current);
      setAgentAction((current) => current?.type === "START_TRY_ON" ? null : current);
      setTryOnStatus(null);
    }
  }

  async function runTryOn() {
    if (!agentAction || agentAction.type !== "START_TRY_ON") return;
    const personImageUrl = agentAction.payload.person_image_url;
    const productIds = agentAction.payload.product_ids;
    if (typeof personImageUrl !== "string") {
      throw new Error("缺少已上传的人物照片");
    }
    if (!Array.isArray(productIds) || typeof productIds[0] !== "string") {
      throw new Error("缺少要试穿的商品");
    }
    setTryOnBusy(true);
    setTryOnStatus("正在提交 AI 试穿任务…");
    setError(null);
    try {
      let task = await createTryOnTask(
        sessionId,
        profileId,
        productIds[0],
        personImageUrl,
      );
      for (let attempt = 0; attempt < 150 && ["PENDING", "RUNNING"].includes(task.status); attempt += 1) {
        setTryOnStatus(task.status === "PENDING" ? "试穿任务排队中…" : "正在生成试穿效果…");
        await wait(2000);
        task = await getTryOnTask(task.task_id, sessionId, profileId);
      }
      if (task.status !== "SUCCEEDED" || !task.result_url) {
        throw new Error(task.error_message || `AI 试穿任务未完成：${task.status}`);
      }
      const completedState: AgentWorkflowState = {
        current_page: "try_on_result",
        person_image_url: personImageUrl,
        selected_product_ids: [productIds[0]],
        try_on_result_url: task.result_url,
        background_image_url: null,
        pending_workflow: null,
        last_completed_action: null,
      };
      setWorkflowState(completedState);
      setAgentAction(null);
      setTryOnStatus("AI 试穿已完成");
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: "assistant",
          content: "AI 试穿效果已经生成，可以在下方查看或保存图片。",
        },
      ]);
    } finally {
      setTryOnBusy(false);
    }
  }

  async function runBackgroundChange() {
    if (!agentAction || agentAction.type !== "CHANGE_BACKGROUND") return;
    const sourceImageUrl = agentAction.payload.source_image_url;
    const prompt = backgroundPrompt.trim();
    if (typeof sourceImageUrl !== "string") {
      throw new Error("缺少可更换背景的试穿图片");
    }
    if (prompt.length < 2) {
      throw new Error("请描述想要的背景，例如海边日落或咖啡厅");
    }
    setTryOnBusy(true);
    setTryOnStatus("正在提交背景生成任务…");
    setError(null);
    try {
      let task = await createBackgroundTask(
        sessionId,
        profileId,
        sourceImageUrl,
        prompt,
      );
      for (let attempt = 0; attempt < 150 && ["PENDING", "RUNNING"].includes(task.status); attempt += 1) {
        setTryOnStatus(task.status === "PENDING" ? "背景任务排队中…" : "正在生成新背景…");
        await wait(2000);
        task = await getBackgroundTask(task.task_id, sessionId, profileId);
      }
      if (task.status !== "SUCCEEDED" || !task.result_url) {
        throw new Error(task.error_message || `背景任务未完成：${task.status}`);
      }
      const completedState: AgentWorkflowState = {
        current_page: "background",
        person_image_url: workflowState?.person_image_url || null,
        selected_product_ids: workflowState?.selected_product_ids || selectedProductIds,
        try_on_result_url: workflowState?.try_on_result_url || sourceImageUrl,
        background_image_url: task.result_url,
        pending_workflow: null,
        last_completed_action: null,
      };
      setWorkflowState(completedState);
      setAgentAction(null);
      setLastBackgroundPrompt(prompt);
      setTryOnStatus("背景更换已完成");
      setMessages((current) => [
        ...current,
        {
          id: createMessageId(),
          role: "assistant",
          content: "新背景已经生成，可以继续调整背景，或者保存当前效果。",
        },
      ]);
    } finally {
      setTryOnBusy(false);
    }
  }

  async function openProductDetail(productId: string) {
    setError(null);
    setProductDetail(await getProductDetail(productId));
  }

  async function openStoreContact() {
    setError(null);
    setSalesCallRequested(false);
    setStoreContact(await getStoreContact());
  }

  async function openSingleProductCart(productId: string, knownProduct?: ProductCard) {
    setError(null);
    const product = knownProduct || (await getProductDetail(productId)).product;
    const sizes = availableProductSizes(product);
    if (sizes.length === 0) throw new Error("当前商品没有可选尺码");
    setPendingCartProduct(product);
    setPendingCartSize(product.size && sizes.includes(product.size) ? product.size : sizes[0]);
    setPendingCartQuantity(1);
    setCartOpen(false);
  }

  async function confirmSingleProductCart() {
    if (!pendingCartProduct || !pendingCartSize) return;
    setCartItemBusy(true);
    setError(null);
    try {
      setCart(await addCartItem(
        profileId,
        pendingCartProduct.parent_asin,
        pendingCartSize,
        pendingCartQuantity,
      ));
      setPendingCartProduct(null);
    } finally {
      setCartItemBusy(false);
    }
  }

  async function removeProductFromCart(productId: string, selectedSize: string) {
    setCart(await removeCartItem(profileId, productId, selectedSize));
  }

  async function submitDemoOrder() {
    const order = await createDemoOrder(profileId);
    setActiveOrder(order);
    setCart(await getCart(profileId));
    setCartOpen(false);
  }

  async function completeDemoPayment() {
    if (!activeOrder) return;
    setActiveOrder(await payDemoOrder(activeOrder.order_id, profileId));
  }

  async function saveCurrentGeneratedResult() {
    const resultUrl = workflowState?.background_image_url || workflowState?.try_on_result_url;
    if (!resultUrl) throw new Error("当前没有可保存的试穿结果");
    const sourceType = resultUrl === workflowState?.background_image_url ? "background" : "try_on";
    const productIds = workflowState?.selected_product_ids.length
      ? workflowState.selected_product_ids
      : selectedProductIds;
    const saved = await saveGeneratedResult(
      sessionId,
      profileId,
      resultUrl,
      sourceType,
      productIds,
      sourceType === "background" ? lastBackgroundPrompt : null,
    );
    setSavedResults((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
    const link = document.createElement("a");
    link.href = resultUrl;
    link.download = sourceType === "background" ? "stylemate-background.jpg" : "stylemate-try-on.jpg";
    link.click();
    setMessages((current) => [
      ...current,
      { id: createMessageId(), role: "assistant", content: "当前效果已加入保存中心，你还可以查看商品详情、咨询导购或加入购物车。" },
    ]);
  }

  async function executeAgentAction() {
    if (!agentAction) return;
    const routeName = agentAction.payload.route_name;
    if (
      agentAction.type === "SELECT_PERSON_IMAGE" ||
      agentAction.type === "UPLOAD_PERSON_IMAGE"
    ) {
      setPersonCenterOpen(true);
      return;
    }
    if (agentAction.type === "START_TRY_ON") {
      await runTryOn();
      return;
    }
    if (agentAction.type === "CHANGE_BACKGROUND") {
      await runBackgroundChange();
      return;
    }
    if (agentAction.type === "SAVE_RESULT") {
      await saveCurrentGeneratedResult();
      setWorkflowState((current) => current ? {
        ...current,
        current_page: "saved",
        pending_workflow: null,
        last_completed_action: null,
      } : current);
      setAgentAction(null);
      return;
    }
    if (
      agentAction.type === "OPEN_PRODUCT_LIBRARY" ||
      (agentAction.type === "NAVIGATE_APP" && routeName === "product_library")
    ) {
      document.querySelector(".results-panel")?.scrollIntoView({ behavior: "smooth" });
      return;
    }
    if (agentAction.type === "OPEN_PRODUCT_DETAIL") {
      const productId = agentAction.payload.product_id;
      if (typeof productId === "string") {
        await openProductDetail(productId);
      }
      return;
    }
    if (agentAction.type === "CONTACT_SALES") {
      await openStoreContact();
      return;
    }
    if (agentAction.type === "PURCHASE_PRODUCT") {
      const productId = agentAction.payload.product_id;
      if (typeof productId === "string") await openSingleProductCart(productId);
    }
  }

  function toggleComparisonProduct(productId: string) {
    setComparisonProductIds((current) => {
      if (current.includes(productId)) {
        return current.filter((value) => value !== productId);
      }
      if (current.length === 4) {
        setError("最多同时对比 4 件商品");
        return current;
      }
      setError(null);
      return [...current, productId];
    });
  }

  function submitComparison() {
    if (comparisonProductIds.length < 2) return;
    setSelectedProductIds(comparisonProductIds);
    void submitMessage(
      `请帮我比较选中的 ${comparisonProductIds.length} 件商品`,
      "COMPARE",
      comparisonProductIds,
    );
  }

  const selectedProducts = (result?.products || []).filter((product) =>
    selectedProductIds.includes(product.parent_asin),
  );
  const comparisonProducts = (result?.products || []).filter((product) =>
    comparisonProductIds.includes(product.parent_asin),
  );
  const conversionResultUrl = workflowState?.background_image_url || workflowState?.try_on_result_url;
  const conversionProductId = workflowState?.selected_product_ids[0] || selectedProductIds[0] || null;
  const hasTryOnVisual = Boolean(
    workflowState?.person_image_url ||
    workflowState?.try_on_result_url ||
    workflowState?.background_image_url,
  );
  const pendingCartSizes = pendingCartProduct ? availableProductSizes(pendingCartProduct) : [];
  const pendingCartTotal = pendingCartProduct?.price === null || !pendingCartProduct
    ? null
    : pendingCartProduct.price * pendingCartQuantity;

  const hasConversation = messages.length > 0 || result !== null;
  const actionRoute = agentAction?.payload.route_name;
  const actionAvailableInWeb = Boolean(
    agentAction && (
      agentAction.type === "OPEN_PRODUCT_LIBRARY" ||
      agentAction.type === "OPEN_PRODUCT_DETAIL" ||
      agentAction.type === "CONTACT_SALES" ||
      agentAction.type === "PURCHASE_PRODUCT" ||
      agentAction.type === "SELECT_PERSON_IMAGE" ||
      agentAction.type === "UPLOAD_PERSON_IMAGE" ||
      agentAction.type === "START_TRY_ON" ||
      agentAction.type === "CHANGE_BACKGROUND" ||
      agentAction.type === "SAVE_RESULT" ||
      (agentAction.type === "NAVIGATE_APP" && actionRoute === "product_library")
    )
  );

  return (
    <main className={`app-shell ${hasConversation ? "conversation-active" : ""}`}>
      <header className="topbar">
        <a className="brand" href="#" aria-label="StyleMate 首页">
          <span className="brand-mark"><Sparkles size={19} /></span>
          <span>
            <strong>StyleMate</strong>
            <small>AI 服装导购</small>
          </span>
        </a>
        <div className="topbar-status">
          <span><i /> Mirror4B 门店商品</span>
          <button type="button" className="header-action" onClick={() => setCartOpen(true)}>
            <ShoppingCart size={15} /> 购物车
            {cart && cart.items.length > 0 && <b>{cart.items.length}</b>}
          </button>
          <button type="button" className="header-action" onClick={() => setPersonCenterOpen(true)}>
            <Images size={15} /> 人物中心
          </button>
          <button type="button" className="header-action" onClick={() => {
            void openStoreContact().catch((contactError) => {
              setError(contactError instanceof Error ? contactError.message : "导购信息加载失败");
            });
          }}>
            <PhoneCall size={15} /> 咨询导购
          </button>
          {hasConversation && (
            <button type="button" className="clear-button" onClick={() => void clearConversation()}>
              <RotateCcw size={15} /> 清空对话
            </button>
          )}
        </div>
      </header>

      <input
        ref={personImageInputRef}
        className="person-image-input"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        onChange={(event) => {
          void handlePersonImageChange(event).catch((uploadError) => {
            setError(uploadError instanceof Error ? uploadError.message : "人物照片上传失败");
            setTryOnBusy(false);
          });
        }}
      />

      {!hasConversation ? (
        <section className="hero">
          <div className="hero-orb hero-orb-one" />
          <div className="hero-orb hero-orb-two" />
          <div className="hero-content">
            <div className="hero-mia" aria-label="穿搭顾问 Mia">
              <span className="hero-mia-portrait">
                <Image src="/mia-avatar.png" alt="穿搭顾问 Mia" width={148} height={148} priority />
              </span>
              <span className="hero-mia-greeting">
                <strong>Hi，我是 Mia</strong>
                <small>告诉我今天想穿什么吧</small>
                <small>告诉我颜色、尺码、季节或场合，我会从商品数据库中为你推荐。</small>
              </span>
            </div>
            <form className="hero-composer" onSubmit={handleSubmit}>
              <Search size={21} />
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="例如：推荐一件黄色上衣，夏天穿，L码"
                rows={1}
                autoFocus
              />
              <button type="submit" disabled={!input.trim() || loading} aria-label="发送需求">
                {loading ? <LoaderCircle className="spin" size={20} /> : <Send size={19} />}
              </button>
            </form>
            <div className="starter-prompts">
              <span>试试这样问</span>
              <div>
                {starterPrompts.map((prompt) => (
                  <button type="button" key={prompt} onClick={() => void submitMessage(prompt)}>
                    {prompt}<ChevronRight size={14} />
                  </button>
                ))}
              </div>
            </div>
            <div className="trust-row">
              <span><Check size={14} /> 查询商品数据库</span>
              <span><Check size={14} /> 支持连续追问</span>
              <span><Check size={14} /> 无匹配不乱推荐</span>
            </div>
          </div>
        </section>
      ) : (
        <section className="workspace">
          <aside className="chat-panel">
            {selectedProductIds.length > 0 && (
              <div className="active-product-context">
                <MessageCircleMore size={15} />
                <div>
                  <small>正在和 Mia 聊</small>
                  <strong>
                    {selectedProducts.length === 1
                      ? selectedProducts[0].title
                      : `已选择 ${selectedProductIds.length} 件商品`}
                  </strong>
                </div>
                <button
                  type="button"
                  aria-label="取消当前商品关联"
                  onClick={() => setSelectedProductIds([])}
                >
                  <X size={14} />
                </button>
              </div>
            )}

            <div className="conversation-scroll">
              <div className="message-list">
              <div className="message assistant-message intro-message">
                <span className="avatar"><AdvisorAvatar /></span>
                <p>你好呀～我是穿搭顾问 Mia。今天想看衣服、鞋子还是包包？告诉我场合和预算，我会挑得更准。</p>
              </div>
              {messages.map((message) => (
                <div className={`message ${message.role}-message`} key={message.id}>
                  <span className="avatar">
                    {message.role === "assistant" ? <AdvisorAvatar /> : <UserRound size={16} />}
                  </span>
                  <p>{message.content}</p>
                </div>
              ))}
              {loading && (
                <div className="message assistant-message thinking-message">
                  <span className="avatar"><AdvisorAvatar /></span>
                  <p><i /><i /><i /></p>
                </div>
              )}
              {error && <div className="error-banner">{error}。如果持续失败，请查看后端服务日志。</div>}
                <div ref={messagesEndRef} />
              </div>

              {result && <FilterChips filters={result.filters} />}

              {agentAction && (
                <div className="agent-action-card">
                <div>
                  <Sparkles size={15} />
                  <span>
                    <strong>下一步</strong>
                    <small>
                      {tryOnStatus || (actionAvailableInWeb
                        ? "可在当前网页执行"
                        : "当前网页暂不支持该动作")}
                    </small>
                  </span>
                </div>
                {agentAction.type === "CHANGE_BACKGROUND" && (
                  <input
                    className="background-prompt-input"
                    value={backgroundPrompt}
                    onChange={(event) => setBackgroundPrompt(event.target.value)}
                    placeholder="描述背景，例如海边日落、咖啡厅"
                    maxLength={500}
                  />
                )}
                <button
                  type="button"
                  disabled={!actionAvailableInWeb || tryOnBusy}
                  onClick={() => {
                    void executeAgentAction().catch((actionError) => {
                      setError(actionError instanceof Error ? actionError.message : "AI 试穿失败");
                      setTryOnBusy(false);
                    });
                  }}
                >
                  {tryOnBusy ? <LoaderCircle className="spin" size={14} /> : null}
                  {tryOnBusy ? "处理中" : agentAction.label}
                </button>
                </div>
              )}
            </div>

            {hasTryOnVisual && (
              tryOnPanelOpen ? (
                <section className="try-on-panel" style={{ height: `${tryOnPanelHeight}px` }}>
                  <div
                    className="try-on-resize-handle"
                    role="separator"
                    aria-label="调节聊天区和试穿区高度"
                    aria-orientation="horizontal"
                    tabIndex={0}
                    onPointerDown={startTryOnPanelResize}
                    onKeyDown={(event) => {
                      if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
                      event.preventDefault();
                      setTryOnPanelHeight((height) => clampTryOnPanelHeight(
                        height + (event.key === "ArrowUp" ? 30 : -30),
                      ));
                    }}
                  >
                    <span />
                  </div>
                  <div className="try-on-panel-heading">
                    <div>
                      <Sparkles size={15} />
                      <span><strong>AI 穿搭视觉区</strong><small>拖动上方横条可调节高度</small></span>
                    </div>
                    <button type="button" onClick={() => setTryOnPanelOpen(false)} aria-label="关闭 AI 穿搭视觉区">
                      <X size={16} />
                    </button>
                  </div>
                  <div className="try-on-panel-scroll">
                    {conversionResultUrl && (
                      <div className="conversion-card">
                        <div>
                          <Sparkles size={14} />
                          <span><strong>试穿满意？继续购买</strong><small>查看商品、咨询导购或加入购物车</small></span>
                        </div>
                        <div className="conversion-actions">
                          <button type="button" onClick={() => {
                            void saveCurrentGeneratedResult().catch((saveError) => setError(saveError instanceof Error ? saveError.message : "保存结果失败"));
                          }}><Download size={13} /> 保存效果</button>
                          <button type="button" disabled={!conversionProductId} onClick={() => {
                            if (conversionProductId) void openProductDetail(conversionProductId).catch((detailError) => setError(detailError instanceof Error ? detailError.message : "商品详情加载失败"));
                          }}><ShoppingBag size={13} /> 商品详情</button>
                          <button type="button" onClick={() => {
                            void openStoreContact().catch((contactError) => setError(contactError instanceof Error ? contactError.message : "导购信息加载失败"));
                          }}><PhoneCall size={13} /> 咨询导购</button>
                          <button type="button" disabled={!conversionProductId} onClick={() => {
                            if (conversionProductId) void openSingleProductCart(conversionProductId).catch((cartError) => setError(cartError instanceof Error ? cartError.message : "加入购物车失败"));
                          }}><ShoppingCart size={13} /> 加入购物车</button>
                        </div>
                      </div>
                    )}

                    {(workflowState?.person_image_url || workflowState?.try_on_result_url) && (
                      <div className="try-on-comparison-grid">
                        {workflowState?.person_image_url && (
                          <div className="try-on-media-card">
                            <div><Upload size={14} /><span>原人物照片</span></div>
                            <button
                              type="button"
                              className="try-on-image-button"
                              onClick={() => setPreviewImageUrl(workflowState.person_image_url)}
                              aria-label="放大查看原人物照片"
                            >
                              <img src={workflowState.person_image_url} alt="已上传的人物照片" />
                              <span><Maximize2 size={12} /> 点击放大</span>
                            </button>
                          </div>
                        )}

                        {workflowState?.try_on_result_url && (
                          <div className="try-on-media-card try-on-result-card">
                            <div><Sparkles size={14} /><span>AI 试穿效果</span></div>
                            <button
                              type="button"
                              className="try-on-image-button"
                              onClick={() => setPreviewImageUrl(workflowState.try_on_result_url)}
                              aria-label="放大查看 AI 试穿效果"
                            >
                              <img src={workflowState.try_on_result_url} alt="AI 试穿效果" />
                              <span><Maximize2 size={12} /> 点击放大</span>
                            </button>
                            <a href={workflowState.try_on_result_url} download="stylemate-try-on.jpg">
                              <Download size={14} /> 保存图片
                            </a>
                          </div>
                        )}
                      </div>
                    )}

                    {workflowState?.background_image_url && (
                      <div className="try-on-media-card try-on-result-card background-result-card">
                        <div><Sparkles size={14} /><span>换背景效果</span></div>
                        <button
                          type="button"
                          className="try-on-image-button"
                          onClick={() => setPreviewImageUrl(workflowState.background_image_url)}
                          aria-label="放大查看换背景效果"
                        >
                          <img src={workflowState.background_image_url} alt="更换背景后的试穿效果" />
                          <span><Maximize2 size={12} /> 点击放大</span>
                        </button>
                        <a href={workflowState.background_image_url} download="stylemate-background.jpg">
                          <Download size={14} /> 保存图片
                        </a>
                      </div>
                    )}

                    {savedResults.length > 0 && (
                      <div className="saved-results-card">
                        <div className="saved-results-heading">
                          <span><Download size={14} /> 保存中心</span>
                          <small>{savedResults.length} 张</small>
                        </div>
                        <div className="saved-results-list">
                          {savedResults.slice(0, 6).map((item) => (
                            <a href={item.image_url} download="stylemate-saved.jpg" key={item.id}>
                              <img src={item.image_url} alt={item.prompt || "已保存的试穿效果"} />
                              <span>{item.source_type === "background" ? "换背景" : "试穿"}</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </section>
              ) : (
                <button type="button" className="try-on-panel-collapsed" onClick={() => setTryOnPanelOpen(true)}>
                  <Images size={15} /> 查看 AI 试穿效果
                </button>
              )
            )}

            {comparisonProducts.length > 0 && (
              <div className="comparison-tray">
                <div className="comparison-tray-heading">
                  <span><Scale size={14} /> 已加入对比 {comparisonProducts.length}/4</span>
                  <button type="button" onClick={() => setComparisonProductIds([])}>清空</button>
                </div>
                <div className="comparison-product-list">
                  {comparisonProducts.map((product) => (
                    <div key={product.parent_asin}>
                      {product.image_url ? (
                        <img src={product.image_url} alt="" />
                      ) : (
                        <span><ShoppingBag size={14} /></span>
                      )}
                      <p>{product.title}</p>
                      <button
                        type="button"
                        aria-label={`取消对比 ${product.title}`}
                        onClick={() => toggleComparisonProduct(product.parent_asin)}
                      >
                        <X size={13} />
                      </button>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  className="submit-comparison-button"
                  disabled={comparisonProducts.length < 2 || loading}
                  onClick={submitComparison}
                >
                  <MessageCircleMore size={14} />
                  {comparisonProducts.length < 2 ? "再选 1 件即可比较" : `让 Mia 比较这 ${comparisonProducts.length} 件`}
                </button>
              </div>
            )}

            <div className="follow-ups">
              {followUpPrompts.map((prompt) => (
                <button type="button" key={prompt} onClick={() => void submitMessage(prompt)} disabled={loading}>
                  {prompt}
                </button>
              ))}
            </div>

            <form className="chat-composer" onSubmit={handleSubmit}>
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="继续修改颜色、尺码或季节……"
                rows={2}
              />
              <button type="submit" disabled={!input.trim() || loading} aria-label="发送消息">
                {loading ? <LoaderCircle className="spin" size={19} /> : <Send size={18} />}
              </button>
              <small>Enter 发送 · Shift + Enter 换行</small>
            </form>
          </aside>

          <section className="results-panel">
            <div className="results-heading">
              <div>
                <h2>{result?.products.length ? `${result.total_matches} 件合适商品` : "正在理解你的需求"}</h2>
                <p>{result?.products.length ? `依据匹配度展示前 ${result.products.length} 件` : "筛选结果会显示在这里"}</p>
              </div>
              {result && result.products.length > 0 && (
                <span className="inventory-note"><PackageCheck size={16} /> 已按演示库存筛选</span>
              )}
            </div>

            {lastIntentResult && (
              <div className="intent-summary">
                <span>
                  意图 <strong>{intentLabels[lastIntentResult.intent]}</strong>
                </span>
                <span>置信度 {(lastIntentResult.confidence * 100).toFixed(0)}%</span>
                <span>来源 {lastIntentResult.parser_source}</span>
                {lastIntentResult.secondary_intents.length > 0 && (
                  <span>
                    同时识别 {lastIntentResult.secondary_intents.map((intent) => intentLabels[intent]).join("、")}
                  </span>
                )}
                {lastIntentResult.conflicts.length > 0 && (
                  <span className="intent-conflict">
                    冲突：{lastIntentResult.conflicts.join("；")}
                  </span>
                )}
              </div>
            )}

            {loading && !result ? (
              <div className="product-grid loading-grid">
                {Array.from({ length: 6 }).map((_, index) => <div className="skeleton-card" key={index} />)}
              </div>
            ) : result?.products.length ? (
              <div className="product-grid">
                {result.products.map((product, index) => (
                  <ProductItem
                    product={product}
                    rank={index + 1}
                    focused={selectedProductIds.includes(product.parent_asin)}
                    inComparison={comparisonProductIds.includes(product.parent_asin)}
                    onAsk={() => askProduct(product)}
                    onTryOn={() => tryOnProduct(product)}
                    onOpenDetail={() => {
                      void openProductDetail(product.parent_asin).catch((detailError) => {
                        setError(detailError instanceof Error ? detailError.message : "商品详情加载失败");
                      });
                    }}
                    onAddCart={() => {
                      void openSingleProductCart(product.parent_asin, product).catch((cartError) => {
                        setError(cartError instanceof Error ? cartError.message : "加入购物车失败");
                      });
                    }}
                    onToggleComparison={() => toggleComparisonProduct(product.parent_asin)}
                    key={product.parent_asin}
                  />
                ))}
              </div>
            ) : result ? (
              <div className="empty-state">
                <span><Search size={28} /></span>
                <h3>{result.intent_result?.clarify_needed ? "需要补充信息" : "暂时没有完全匹配的商品"}</h3>
                <p>{result.message}</p>
                {!result.intent_result?.clarify_needed && (
                  <small>可以尝试更换颜色、尺码，或说“不限制季节”。</small>
                )}
              </div>
            ) : (
              <div className="empty-state waiting-state">
                <span><Sparkles size={28} /></span>
                <h3>正在为你挑选</h3>
                <p>很快就会呈现符合条件的商品。</p>
              </div>
            )}
          </section>
        </section>
      )}

      {personCenterOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setPersonCenterOpen(false)}>
          <section className="store-modal person-center-modal" role="dialog" aria-modal="true" aria-labelledby="person-center-title" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setPersonCenterOpen(false)} aria-label="关闭"><X size={18} /></button>
            <span className="modal-icon"><Images size={22} /></span>
            <h2 id="person-center-title">人物选择中心</h2>
            <p>选择曾经上传的人像，或者上传一张新的正面全身或半身照片。</p>
            <button type="button" className="primary-modal-button" disabled={tryOnBusy} onClick={() => personImageInputRef.current?.click()}>
              <Upload size={15} /> {tryOnBusy ? "正在上传…" : "上传新人物照片"}
            </button>
            {personUploadNotice && <p className="person-upload-notice">{personUploadNotice}</p>}
            {personImages.length > 0 ? (
              <div className="person-gallery">
                {personImages.map((item) => (
                  <div className={`person-gallery-item ${workflowState?.person_image_url === item.image_url ? "person-gallery-item-active" : ""}`} key={item.id}>
                    <button
                      type="button"
                      className="person-image-select"
                      aria-pressed={workflowState?.person_image_url === item.image_url}
                      onClick={() => togglePersonImageSelection(item)}
                    >
                      <img src={item.image_url} alt="已上传的人物照片" />
                      <span>{workflowState?.person_image_url === item.image_url ? "已选择 · 点击取消" : "点击选择"}</span>
                    </button>
                    <button type="button" className="person-image-delete" aria-label="删除人物照片" onClick={() => {
                      void removePersonImage(item).catch((deleteError) => setError(deleteError instanceof Error ? deleteError.message : "删除人物照片失败"));
                    }}><Trash2 size={14} /></button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="person-gallery-empty">还没有保存的人物照片</div>
            )}
            <p className="demo-notice">人物照片仅保存在当前 StyleMate 演示服务器，可随时在这里删除。</p>
          </section>
        </div>
      )}

      {productDetail && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setProductDetail(null)}>
          <section className="store-modal product-detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setProductDetail(null)} aria-label="关闭"><X size={18} /></button>
            <div className="detail-layout">
              <div className="detail-image">
                {productDetail.product.image_url ? <img src={productDetail.product.image_url} alt={productDetail.product.title} /> : <ShoppingBag size={42} />}
              </div>
              <div>
                <span className="detail-brand">{productDetail.product.brand || "品牌未知"}</span>
                <h2 id="detail-title">{productDetail.product.title}</h2>
                <p>{productDetail.product.description_text || productDetail.product.features_text || "当前商品库暂无更多文字介绍。"}</p>
                <div className="detail-facts">
                  <span>品类 {valueLabels[productDetail.product.category || ""] || productDetail.product.category || "未知"}</span>
                  <span>颜色 {valueLabels[productDetail.product.color || ""] || productDetail.product.color || "未知"}</span>
                  <span>尺码 {productDetail.product.size || "未知"}</span>
                  <span>库存 {productDetail.product.stock_quantity} 件</span>
                </div>
                <strong className="detail-price">{productDetail.product.price === null ? "价格未知" : formatMoney(productDetail.product.price, productDetail.product.currency)}</strong>
                <p className="demo-notice">{productDetail.data_notice}</p>
                <div className="modal-actions">
                  <button type="button" onClick={() => {
                    const product = productDetail.product;
                    setProductDetail(null);
                    void openSingleProductCart(product.parent_asin, product).catch((cartError) => setError(cartError instanceof Error ? cartError.message : "加入购物车失败"));
                  }}><ShoppingCart size={15} /> 加入演示购物车</button>
                  <button type="button" className="secondary-modal-button" onClick={() => {
                    void openStoreContact().catch((contactError) => setError(contactError instanceof Error ? contactError.message : "导购信息加载失败"));
                  }}><PhoneCall size={15} /> 咨询导购</button>
                </div>
              </div>
            </div>
          </section>
        </div>
      )}

      {storeContact && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setStoreContact(null)}>
          <section className="store-modal contact-modal" role="dialog" aria-modal="true" aria-labelledby="contact-title" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setStoreContact(null)} aria-label="关闭"><X size={18} /></button>
            <span className="modal-icon"><PhoneCall size={22} /></span>
            <h2 id="contact-title">{storeContact.assistant_name}</h2>
            <dl>
              <div><dt>咨询渠道</dt><dd>{storeContact.channel}</dd></div>
              <div><dt>服务时间</dt><dd>{storeContact.service_hours}</dd></div>
            </dl>
            <div className={`sales-call-panel ${salesCallRequested ? "sales-call-panel-success" : ""}`}>
              <p>{salesCallRequested
                ? "呼叫已提交，服务员预计 1 分钟内到达（演示）。"
                : "需要现场帮助？请点击按钮，服务员将在 1 分钟内到达。"}</p>
              <button type="button" disabled={salesCallRequested} onClick={() => setSalesCallRequested(true)}>
                {salesCallRequested ? <Check size={15} /> : <PhoneCall size={15} />}
                {salesCallRequested ? "已召唤服务员" : "一键召唤服务员"}
              </button>
            </div>
            <p className="demo-notice">{storeContact.demo_notice}</p>
          </section>
        </div>
      )}

      {pendingCartProduct && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setPendingCartProduct(null)}>
          <section className="store-modal single-cart-modal" role="dialog" aria-modal="true" aria-labelledby="single-cart-title" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setPendingCartProduct(null)} aria-label="关闭"><X size={18} /></button>
            <div className="single-cart-heading">
              <span className="modal-icon"><ShoppingCart size={22} /></span>
              <div><small>加入演示购物车</small><h2 id="single-cart-title">选择尺码和数量</h2></div>
            </div>
            <div className="single-cart-product">
              {pendingCartProduct.image_url ? <img src={pendingCartProduct.image_url} alt={pendingCartProduct.title} /> : <span><ShoppingBag size={24} /></span>}
              <div>
                <small>{pendingCartProduct.brand || "品牌未知"}</small>
                <strong>{pendingCartProduct.title}</strong>
                <span>单价 {pendingCartProduct.price === null ? "未知" : formatMoney(pendingCartProduct.price, pendingCartProduct.currency)} · {pendingCartProduct.stock_source === "synthetic_demo" ? "演示库存" : "库存"} {pendingCartProduct.stock_quantity} 件</span>
              </div>
            </div>
            <div className="single-cart-options">
              <label>
                <span>选择尺码</span>
                <select value={pendingCartSize} onChange={(event) => setPendingCartSize(event.target.value)}>
                  {pendingCartSizes.map((size) => <option value={size} key={size}>{size}</option>)}
                </select>
              </label>
              <div className="single-cart-quantity-field">
                <span>选择数量</span>
                <div className="quantity-stepper">
                  <button type="button" disabled={pendingCartQuantity <= 1} onClick={() => setPendingCartQuantity((quantity) => quantity - 1)} aria-label="减少数量"><Minus size={15} /></button>
                  <strong>{pendingCartQuantity}</strong>
                  <button type="button" disabled={pendingCartQuantity >= Math.min(99, pendingCartProduct.stock_quantity)} onClick={() => setPendingCartQuantity((quantity) => quantity + 1)} aria-label="增加数量"><Plus size={15} /></button>
                </div>
              </div>
            </div>
            <div className="single-cart-total">
              <span>单品合计</span>
              <strong>{pendingCartTotal === null || !pendingCartProduct ? "价格未知" : formatMoney(pendingCartTotal, pendingCartProduct.currency)}</strong>
            </div>
            <p className="demo-notice">当前是单品加入面板；顶部“购物车”可查看所有已加入商品。</p>
            {error && <div className="error-banner">{error}</div>}
            <button type="button" className="primary-modal-button" disabled={cartItemBusy} onClick={() => {
              void confirmSingleProductCart().catch((cartError) => setError(cartError instanceof Error ? cartError.message : "加入购物车失败"));
            }}>
              {cartItemBusy ? <LoaderCircle className="spin" size={15} /> : <ShoppingCart size={15} />}
              {cartItemBusy ? "正在加入…" : "确认加入购物车"}
            </button>
          </section>
        </div>
      )}

      {cartOpen && (
        <div className="modal-backdrop drawer-backdrop" role="presentation" onMouseDown={() => setCartOpen(false)}>
          <aside className="cart-drawer" role="dialog" aria-modal="true" aria-labelledby="cart-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="cart-heading">
              <div><ShoppingCart size={19} /><h2 id="cart-title">演示购物车</h2></div>
              <button type="button" onClick={() => setCartOpen(false)} aria-label="关闭"><X size={18} /></button>
            </div>
            {cart?.items.length ? (
              <>
                <div className="cart-items">
                  {cart.items.map((item) => (
                    <div className="cart-item" key={`${item.product.parent_asin}-${item.selected_size}`}>
                      {item.product.image_url ? <img src={item.product.image_url} alt="" /> : <span><ShoppingBag size={18} /></span>}
                      <div><strong>{item.product.title}</strong><small>尺码 {item.selected_size} · 数量 {item.quantity} · {formatMoney(item.line_total, item.product.currency)}</small></div>
                      <button type="button" aria-label="移除商品" onClick={() => {
                        void removeProductFromCart(item.product.parent_asin, item.selected_size).catch((cartError) => setError(cartError instanceof Error ? cartError.message : "移除商品失败"));
                      }}><Trash2 size={15} /></button>
                    </div>
                  ))}
                </div>
                <div className="cart-summary"><span>合计</span><strong>{formatMoney(cart.total, cart.currency)}</strong></div>
                <p className="demo-notice">{cart.demo_notice}</p>
                <button type="button" className="primary-modal-button" onClick={() => {
                  void submitDemoOrder().catch((orderError) => setError(orderError instanceof Error ? orderError.message : "创建演示订单失败"));
                }}>确认演示订单</button>
              </>
            ) : (
              <div className="cart-empty"><ShoppingBag size={30} /><p>购物车还是空的，先从推荐商品中挑一件吧。</p></div>
            )}
          </aside>
        </div>
      )}

      {previewImageUrl && (
        <div className="image-lightbox" role="presentation" onMouseDown={() => setPreviewImageUrl(null)}>
          <div className="image-lightbox-content" role="dialog" aria-modal="true" aria-label="试穿效果大图" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" onClick={() => setPreviewImageUrl(null)} aria-label="关闭大图">
              <X size={20} />
            </button>
            <img src={previewImageUrl} alt="放大的试穿效果" />
          </div>
        </div>
      )}

      {activeOrder && (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setActiveOrder(null)}>
          <section className="store-modal order-modal" role="dialog" aria-modal="true" aria-labelledby="order-title" onMouseDown={(event) => event.stopPropagation()}>
            <button type="button" className="modal-close" onClick={() => setActiveOrder(null)} aria-label="关闭"><X size={18} /></button>
            <span className="modal-icon"><CreditCard size={22} /></span>
            <h2 id="order-title">{activeOrder.status === "DEMO_PAID" ? "演示支付完成" : "演示订单已确认"}</h2>
            <p>订单号：{activeOrder.order_id}</p>
            <strong className="detail-price">{formatMoney(activeOrder.total, activeOrder.currency)}</strong>
            <p className="demo-notice">{activeOrder.demo_notice}</p>
            {activeOrder.status === "DEMO_PENDING" && (
              <button type="button" className="primary-modal-button" onClick={() => {
                void completeDemoPayment().catch((paymentError) => setError(paymentError instanceof Error ? paymentError.message : "演示支付失败"));
              }}><CreditCard size={15} /> 完成演示支付（不会扣款）</button>
            )}
          </section>
        </div>
      )}
    </main>
  );
}
