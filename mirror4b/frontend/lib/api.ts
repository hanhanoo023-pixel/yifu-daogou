import type {
  AgentWorkflowState,
  BackgroundTaskResponse,
  Cart,
  DemoOrder,
  NormalizedFilters,
  PersonImageRecord,
  PersonImageUploadResponse,
  ProductDetailResponse,
  RecommendationResponse,
  SavedResult,
  StoreContact,
  TryOnTaskResponse,
} from "@/types";

const API_URL = "";

type FastApiValidationError = {
  loc: Array<string | number>;
  msg: string;
};

type FastApiErrorPayload = {
  detail: string | FastApiValidationError[];
};

function formatUploadError(payload: FastApiErrorPayload): string {
  if (typeof payload.detail === "string") return payload.detail;
  if (payload.detail.some((item) => item.loc.join(".") === "query.profile_id")) {
    return "人物身份未初始化，请刷新页面后重新上传";
  }
  return payload.detail
    .map((item) => `${item.loc.join(".")}：${item.msg}`)
    .join("；");
}

export async function sendChatMessage(
  message: string,
  sessionId: string,
  profileId: string,
  currentFilters: NormalizedFilters | null,
  currentProductId: string | null,
  selectedProductIds: string[],
  visibleProductIds: string[],
  workflowState: AgentWorkflowState | null,
  selectionAction: "EXPLAIN" | "COMPARE" | null = null,
  limit = 12,
): Promise<RecommendationResponse> {
  const response = await fetch(`${API_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      session_id: sessionId,
      profile_id: profileId,
      current_filters: currentFilters,
      current_product_id: currentProductId,
      selected_product_ids: selectedProductIds,
      selection_action: selectionAction,
      visible_product_ids: visibleProductIds,
      workflow_state: workflowState,
      limit,
    }),
  });

  if (!response.ok) {
    throw new Error(`导购服务请求失败：HTTP ${response.status}`);
  }

  return response.json() as Promise<RecommendationResponse>;
}

export async function clearConversationMemory(
  sessionId: string,
  profileId: string,
): Promise<void> {
  const response = await fetch(
    `${API_URL}/api/sessions/${encodeURIComponent(sessionId)}?profile_id=${encodeURIComponent(profileId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) {
    throw new Error(`清空会话失败：HTTP ${response.status}`);
  }
}

export async function uploadPersonImage(
  file: File,
  profileId: string,
): Promise<PersonImageUploadResponse> {
  const response = await fetch(
    `${API_URL}/api/try-on/person-image?profile_id=${encodeURIComponent(profileId)}`,
    {
    method: "POST",
    headers: { "Content-Type": file.type },
    body: file,
    },
  );
  if (!response.ok) {
    const payload = await response.json() as FastApiErrorPayload;
    throw new Error(formatUploadError(payload));
  }
  return response.json() as Promise<PersonImageUploadResponse>;
}

export async function getPersonImages(profileId: string): Promise<PersonImageRecord[]> {
  const response = await fetch(
    `${API_URL}/api/person-images/${encodeURIComponent(profileId)}`,
  );
  if (!response.ok) throw new Error(`人物照片加载失败：HTTP ${response.status}`);
  const payload = await response.json() as { items: PersonImageRecord[] };
  return payload.items;
}

export async function deletePersonImage(profileId: string, imageId: string): Promise<void> {
  const response = await fetch(
    `${API_URL}/api/person-images/${encodeURIComponent(profileId)}/${encodeURIComponent(imageId)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(`删除人物照片失败：HTTP ${response.status}`);
}

export async function createTryOnTask(
  sessionId: string,
  profileId: string,
  productId: string,
  personImageUrl: string,
): Promise<TryOnTaskResponse> {
  const response = await fetch(`${API_URL}/api/try-on/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      profile_id: profileId,
      product_id: productId,
      person_image_url: personImageUrl,
    }),
  });
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  return response.json() as Promise<TryOnTaskResponse>;
}

export async function getTryOnTask(
  taskId: string,
  sessionId: string,
  profileId: string,
): Promise<TryOnTaskResponse> {
  const query = new URLSearchParams({
    session_id: sessionId,
    profile_id: profileId,
  });
  const response = await fetch(
    `${API_URL}/api/try-on/tasks/${encodeURIComponent(taskId)}?${query.toString()}`,
  );
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  return response.json() as Promise<TryOnTaskResponse>;
}

export async function createBackgroundTask(
  sessionId: string,
  profileId: string,
  baseImageUrl: string,
  refPrompt: string,
): Promise<BackgroundTaskResponse> {
  const response = await fetch(`${API_URL}/api/background/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      profile_id: profileId,
      base_image_url: baseImageUrl,
      ref_prompt: refPrompt,
    }),
  });
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  return response.json() as Promise<BackgroundTaskResponse>;
}

export async function getBackgroundTask(
  taskId: string,
  sessionId: string,
  profileId: string,
): Promise<BackgroundTaskResponse> {
  const query = new URLSearchParams({
    session_id: sessionId,
    profile_id: profileId,
  });
  const response = await fetch(
    `${API_URL}/api/background/tasks/${encodeURIComponent(taskId)}?${query.toString()}`,
  );
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  return response.json() as Promise<BackgroundTaskResponse>;
}

export async function saveGeneratedResult(
  sessionId: string,
  profileId: string,
  imageUrl: string,
  sourceType: "try_on" | "background",
  productIds: string[],
  prompt: string | null,
): Promise<SavedResult> {
  const response = await fetch(`${API_URL}/api/saved-results`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_id: sessionId,
      profile_id: profileId,
      image_url: imageUrl,
      source_type: sourceType,
      product_ids: productIds,
      prompt,
    }),
  });
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  return response.json() as Promise<SavedResult>;
}

export async function getSavedResults(profileId: string): Promise<SavedResult[]> {
  const response = await fetch(
    `${API_URL}/api/saved-results/${encodeURIComponent(profileId)}`,
  );
  if (!response.ok) {
    const payload = await response.json() as { detail: string };
    throw new Error(payload.detail);
  }
  const payload = await response.json() as { items: SavedResult[] };
  return payload.items;
}

export async function getProductDetail(productId: string): Promise<ProductDetailResponse> {
  const response = await fetch(`${API_URL}/api/products/${encodeURIComponent(productId)}`);
  if (!response.ok) throw new Error(`商品详情加载失败：HTTP ${response.status}`);
  return response.json() as Promise<ProductDetailResponse>;
}

export async function getStoreContact(): Promise<StoreContact> {
  const response = await fetch(`${API_URL}/api/store/contact`);
  if (!response.ok) throw new Error(`导购信息加载失败：HTTP ${response.status}`);
  return response.json() as Promise<StoreContact>;
}

export async function getCart(profileId: string): Promise<Cart> {
  const response = await fetch(`${API_URL}/api/cart/${encodeURIComponent(profileId)}`);
  if (!response.ok) throw new Error(`购物车加载失败：HTTP ${response.status}`);
  return response.json() as Promise<Cart>;
}

export async function addCartItem(
  profileId: string,
  productId: string,
  selectedSize: string,
  quantity: number,
): Promise<Cart> {
  const response = await fetch(`${API_URL}/api/cart/${encodeURIComponent(profileId)}/items`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ product_id: productId, selected_size: selectedSize, quantity }),
  });
  if (!response.ok) throw new Error(`加入购物车失败：HTTP ${response.status}`);
  return response.json() as Promise<Cart>;
}

export async function removeCartItem(profileId: string, productId: string, selectedSize: string): Promise<Cart> {
  const response = await fetch(
    `${API_URL}/api/cart/${encodeURIComponent(profileId)}/items/${encodeURIComponent(productId)}?selected_size=${encodeURIComponent(selectedSize)}`,
    { method: "DELETE" },
  );
  if (!response.ok) throw new Error(`移除商品失败：HTTP ${response.status}`);
  return response.json() as Promise<Cart>;
}

export async function createDemoOrder(profileId: string): Promise<DemoOrder> {
  const response = await fetch(`${API_URL}/api/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId }),
  });
  if (!response.ok) throw new Error(`创建演示订单失败：HTTP ${response.status}`);
  return response.json() as Promise<DemoOrder>;
}

export async function payDemoOrder(orderId: string, profileId: string): Promise<DemoOrder> {
  const response = await fetch(`${API_URL}/api/orders/${encodeURIComponent(orderId)}/demo-pay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile_id: profileId }),
  });
  if (!response.ok) throw new Error(`演示支付失败：HTTP ${response.status}`);
  return response.json() as Promise<DemoOrder>;
}
