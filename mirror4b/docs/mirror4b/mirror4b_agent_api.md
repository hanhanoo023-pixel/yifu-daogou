# Mirror4B Agent 封装接口说明

后端地址是47.103.38.216

> 范围：`server/app/api/mirror4b/v1/endpoints/chat.py` 中基于 `services/mirror4b_agent` 封装对外暴露的接口，
> 以及两个直接调用搭配推荐 LLM 服务的推荐接口（`endpoints/recommend.py`）。
> 本文只描述 HTTP 接口与出入参，不涉及内部算法细节。

- 基础路径：`/mirror4b`（Client 端，`app/main.py` 中 `mirror4b_client_router` 挂载于 `/mirror4b`）
- 均为 `POST`、`application/json`（例外：`wardrobe-mode` 为 `multipart/form-data`）
- 与 Agent 无关的商品/门店等管理接口见 `mirror4b_api.md`。

---

## 1. 普通聊天（直连 LLM，无意图解析）

### `POST /mirror4b/chat`

纯文本透传给配置的 LLM（`settings.LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL`），无 Agent 路由、无鉴权、无动作输出。

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | 是 | 用户消息文本 |
| `history` | `[{role, content}]` | 否 | 历史对话，`role` 为 `user`/`assistant` 等 |

返回：

| 字段 | 类型 | 说明 |
|---|---|---|
| `reply` | string | LLM 回复文本 |

- 未配置 `LLM_API_KEY` 时返回 mock 文案 `Backend Error: LLM_API_KEY not configured in .env`（HTTP 200）。
- LLM 返回非 2xx 时抛 `HTTPException`（透传上游状态码）；其它异常为 500。

---

## 2. Agent 聊天（核心封装接口）

### `POST /mirror4b/chat/agent`

语义：把用户自然语言解析为“回复 + 可选页面跳转 / 领域动作 / 内嵌商品·套装推荐”的结构化结果。
推荐类意图会在后端按请求携带的 Bearer Token 对应商家库存做真实查询。

可选携带 `Authorization: Bearer <mirror4b_merchant_token>`。**不强制登录**：未带/无效 Token 时仅推荐类意图退化为“无商品”兜底，其余对话不受影响。

#### 2.1 请求体 `AgentChatRequest`

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `text` | string | 是 | — | 用户本次输入 |
| `current_page` | string | 否 | `null` | 客户端当前页面路由名（如 `/chat`），Agent 不会返回跳转到当前页的动作 |
| `history` | `[{role, content}]` | 否 | `null` | 历史对话 |
| `excluded_product_ids` | int[] | 否 | `[]` | 需排除的商品 ID；`revise_recommendation`、`recommend_outfit_set` 会额外排除上次结果 |
| `context` | object（`AgentRuntimeContext`） | 否 | 全空默认 | 当前会话运行时状态，见下表 |

`context` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `has_person_photo` | bool | 是否已有可用人物照片 |
| `has_result_image` | bool | 是否已有试穿/换背景结果图 |
| `has_running_task` | bool | 是否有进行中的任务 |
| `current_product_id` | int? | 当前聚焦商品 ID（详情页/试穿结果对应商品） |
| `recommended_products` | `[{id, name, category}]` | Agent 本轮已推荐的商品，用于“这件/第一件/第二件”引用校验 |
| `current_outfit_id` | string? | 当前套装 ID（形如 `top-{id}-bottom-{id}`） |
| `recommended_outfits` | `[{outfit_id, slots:[{slot, product_id}]}]` | 本轮已推荐套装及槽位 |

#### 2.2 返回 `AgentChatResponse`

```json
{
  "status": "SUCCESS",
  "message": {
    "text": "给用户的中文回复",
    "action": { "type": "AGENT_INTENT", "payload": { "intent": "...", "parameters": {} } },
    "intent": "recommend_clothes",
    "sales_stage": "recommendation",
    "recommended_products": [ ... ],
    "recommended_outfits": [ ... ],
    "recommendation_method": "...",
    "debug_note": "...",
    "quick_replies": ["..."]
  }
}
```

- `status`：当前恒为 `"SUCCESS"`（错误走 HTTP 状态码）。
- `message`（`AgentMessage`）字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `text` | string | 展示给用户的回复文本 |
| `action` | `{type, payload}`? | 需要客户端执行的动作；纯对话/推荐意图时为 `null` |
| `intent` | string | 本轮命中意图（见 2.4），纯寒暄可为 `"none"` |
| `sales_stage` | string | 销售阶段（见 2.5） |
| `recommended_products` | `AgentRecommendedProduct[]`? | 推荐类意图返回的推荐单品（普通聊天为空） |
| `recommended_outfits` | `AgentRecommendedOutfit[]`? | `recommend_outfit_set` 套装模式返回的整套搭配 |
| `recommendation_method` | string? | 推荐算法标识（调试用） |
| `debug_note` | string? | 调试说明，含命中规则/算法；普通意图为 `null` |
| `quick_replies` | string[] | 建议的快捷回复按钮文案 |

`action.payload` 结构（`AgentActionPayload`）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `route_name` | string? | 页面跳转时填充，见 2.6 路由表 |
| `intent` | string? | `AGENT_INTENT` 动作时填充为意图名 |
| `parameters` | object | 领域动作附带参数；键可能为 `product_id` / `product_reference` / `search_query` / `prompt`（换背景描述）等 |

`action.type` 取值与触发条件（`_to_agent_message`）：

| `type` | 触发条件 |
|---|---|
| `NAVIGATE` / `NAVIGATE_GO` | 命中文档 2.6 导航意图，`payload.route_name` 为跳转目标 |
| `AGENT_INTENT` | 命中领域动作意图，`payload.intent` = 意图、`payload.parameters` = 结构化参数 |
| （`action: null`） | 纯对话/澄清/推荐回复，客户端仅展示文本与快捷回复 |

#### 2.3 推荐/套装返回结构

推荐类意图（`recommend_clothes` / `recommend_outfit` / `recommend_outfit_set` / `revise_recommendation`）命中时，
后端查询当前商家在售商品做确定性推荐，`action` 为 `null`，结果放在 `message.recommended_products`（以及套装模式的 `recommended_outfits`）。

`AgentRecommendedProduct`（单件商品，全部字段由后端 `ProductRead` 拉平）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 商品 ID |
| `name` | string | 名称，缺省为 `未命名商品` |
| `sku` | string | SKU |
| `main_image_url` | string | 主图 URL |
| `price` | float | 售价（来自销售数据） |
| `total_stock` | int | 总库存 |
| `status` | string | 销售状态（如 `在售`） |
| `store_location` | string | 门店位置 |
| `shelf_position` | string | 货架位 |
| `selling_points` | string | 卖点 |
| `applicable_scenarios` | string | 适用场景 |
| `style_tags` | string[] | 风格标签 |
| `styling_advice` | string | 搭配建议 |
| `fit` | string | 版型 |
| `material` | string | 材质 |
| `season` | string | 季节 |
| `description` | string | 描述 |
| `selling_point_image_url` | string | 特点图 URL |
| `category` | string | 品类 |
| `brand` | string | 品牌 |
| `tags` | string[] | 参与匹配的标签 |
| `recommendation_score` | int | 匹配得分 |
| `matched_tags` | string[] | 命中的标签 |
| `sales_data` | object | 原始销售数据字典 |

`AgentRecommendedOutfit`（套装，`recommend_outfit_set` 且配置 `AGENT_OUTFIT_SET_ENABLED=true` 时返回整套）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `outfit_id` | string | 形如 `top-{id}-bottom-{id}` |
| `slots` | `[{slot, role, product}]` | 恰含 `top`、`bottom` 两个槽位；`slot` ∈ `top/bottom`，`role` ∈ `anchor`（发起商品）/`recommended` |
| `total_price` | float? | 已录入两件总价（缺省无价格） |
| `score` | int | 0–100 |
| `matched_reasons` | string[] | 匹配点说明 |

- 套装模式下 `message.recommended_products` 为该套的上装+下装商品列表。
- `recommend_outfit_set` 未启用（`AGENT_OUTFIT_SET_ENABLED=false`）时，走普通单品推荐逻辑返回单品。
- “换一套 / 再来一套 / 重新搭一套”等会清空锚点商品重新组合；否则优先以 `context.current_product_id` 或意图参数 `product_id` 作为锚点（anchor）。
- 排除逻辑：`revise_recommendation` / `recommend_outfit_set` 默认排除 `excluded_product_ids`；`recommend_outfit` / `recommend_outfit_set` 默认排除 `context.current_product_id`（换方案时除外）。

空结果兜底文案：

| 意图 | 空结果回复 |
|---|---|
| `revise_recommendation` | 当前门店暂时没有更多不同的衣服可换… |
| `recommend_outfit` | 当前门店暂时没有找到符合目标品类的搭配商品… |
| `recommend_outfit_set` | 当前门店暂时无法组成同时满足条件的上装和下装… |
| 其它/未登录 | 我暂时没有读取到当前门店的可推荐衣服… |

#### 2.4 `intent` 取值（服务端合法集合）

领域意图 `DOMAIN_INTENTS`：

```
call_store_staff request_person_photo browse_clothes recommend_clothes
clarify_recommendation recommend_outfit recommend_outfit_set revise_recommendation
inspect_product styling_advice try_on compare_try_on change_background
choose_background task_status save_result hesitate purchase_interest
compare_products truthful_commerce
```

页面导航意图：`open_camera open_scan_upload open_wardrobe go_home open_chat open_guide play_promo_video open_merchant_home open_wifi_settings`

外加 `none`（纯对话、无法归类的寒暄）。其中返回 `AGENT_INTENT` 动作的领域意图为
`call_store_staff / request_person_photo / browse_clothes / inspect_product / try_on / compare_try_on / change_background / choose_background / task_status / save_result / purchase_interest`。

#### 2.5 `sales_stage` 取值

```
opening discovery recommendation evaluation styling try_on
try_on_result specification purchase_handoff
```

主要映射：`none/寒暄`→`opening`；`recommend_clothes/revise_recommendation`→`recommendation`；
`recommend_outfit / recommend_outfit_set / styling_advice`→`styling`；
`inspect_product / compare_products / hesitate`→`evaluation`；
`try_on / compare_try_on / task_status` 等→`try_on`；换背景/保存→`try_on_result`；价格库存类→`purchase_handoff`。

#### 2.6 导航动作路由表（`payload.route_name`）

| intent | route_name | action.type |
|---|---|---|
| `open_camera` | `/shooting` | `NAVIGATE` |
| `open_scan_upload` | `/scan-upload` | `NAVIGATE` |
| `open_wardrobe` | `/scan-upload` | `NAVIGATE` |
| `go_home` | `/landing` | `NAVIGATE_GO` |
| `open_chat` | `/chat` | `NAVIGATE` |
| `open_guide` | `/guide` | `NAVIGATE` |
| `play_promo_video` | `/promo-video` | `NAVIGATE` |
| `open_merchant_home` | `/home` | `NAVIGATE_GO` |
| `open_wifi_settings` | `/wifi` | `NAVIGATE` |

#### 2.7 解析与降级行为（影响出参）

- 路由顺序：真实优惠/库存保护（`truthful_commerce`）→ 确定性意图规则 → 关键词页面导航 → 商品已唯一绑定的 `inspect_product / try_on / purchase_interest` 快速路由 → LLM Function Call（工具 `route_agent_command`）→ 确定性规则降级 → 默认寒暄。
- 无 `LLM_API_KEY` 时全部走确定性规则，不调 LLM。
- LLM 超时/格式错误/工具参数非法时自动降级到确定性结果；模型回复不携带合法 `product_id`（不在 `context` 商品集合内）的引用会被丢弃。
- 错误：LLM 上游非 2xx → `HTTPException`（透传状态码）；其余异常 → 500。

---

## 3. 商品点评（详情页 Agent 点评）

### `POST /mirror4b/chat/product-review`

为商品详情页生成“版型/搭配/场景 + 综述”点评。无鉴权。调用 `settings.LLM_*`；未配置 Key 或调用异常时返回本地兜底点评（HTTP 200，`debug_note` 标识来源）。

请求体 `AgentProductReviewRequest`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 商品名 |
| `product_id` | int? | 否 | 商品 ID |
| `category` | string | 否 | 品类 |
| `selling_points` | string | 否 | 卖点 |
| `applicable_scenarios` | string | 否 | 适用场景 |
| `style_tags` | string[] | 否 | 风格标签 |
| `styling_advice` | string | 否 | 搭配建议 |
| `fit` | string | 否 | 版型 |
| `material` | string | 否 | 材质 |
| `season` | string | 否 | 季节 |
| `description` | string | 否 | 描述 |
| `selling_point_image_url` | string | 否 | 特点图 URL（仅影响是否告知可看图，不据此推断材质） |
| `brand` | string | 否 | 品牌 |
| `price` | float | 否 | 价格 |
| `user_intent` | string? | 否 | 承接近期有效的场景/搭配对象 |

返回 `AgentProductReviewResponse`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | string | `"SUCCESS"` |
| `points` | `[{label, text}]` | 固定 3 条，label 依次 `版型 / 搭配 / 场景` |
| `review` | string | 综合点评 |
| `debug_note` | string? | `llm_product_review` 或 `fallback_*` 标识 |

约束：LLM 输出被归一化为 3 条 `points`、点评截断 180 字；条目数不符/缺失时用本地兜底补齐。

---

## 4. 直接调用搭配推荐 LLM 的接口（附）

> 这两个接口不在 `services/mirror4b_agent` 内，是独立“搭配推荐”封装：调用专用推荐服务
> `http://8.147.118.107:8001/v1/chat/completions`（无需 API Key，服务端固定模型，`model` 字段仅为占位）。

### `POST /mirror4b/recommend/mall-mode`（商城模式：人物图 → 推荐列表）

允许匿名（推荐商品时使用 `Authorization` 可选的商家鉴权过滤在售商品）。

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `messages` | OpenAI 风格数组 | 是 | 需含 `type: image_url` 的人物图消息 |
| `model` | string | 否 | 占位 |
| `temperature` | float | 否 | 默认 0.7 |

返回：`Outfit[]`（`OutfitRead`），每项：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | 序号 |
| `generated_image_url` | string | 主图（走存储公开 URL） |
| `items` | `[{id, name, category?, image_url?}]` | 单品列表 |

兜底：未找到人物图 / 服务失败或超时（60s→重试 120s）时返回商品库随机兜底。

### `POST /mirror4b/recommend/wardrobe-mode`（衣橱模式：全身照 → 搭配建议）

必须登录：`Authorization: Bearer <access_token>`。请求为 `multipart/form-data`。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `person_image` | file | 是 | 用户全身照（上传前压缩到最长边 800px / JPEG q85） |
| `description` | string | 否 | 穿搭偏好，空时默认 `帮我看看这身怎么搭比较好？` |

返回：

```json
{
  "advice": "整体搭配建议文本",
  "outfits": [
    { "name": "搭配名称", "description": "适用场景", "item_indices": [1, 3], "preview_image_url": "首件单品主图 URL" }
  ]
}
```

兜底：服务超时 45s / 非 2xx / 无有效 `outfits_data` 时返回基于库存的基础搭配结果。
