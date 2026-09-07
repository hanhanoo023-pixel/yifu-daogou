# Mirror4B 应用 API 文档


## 1. 基本信息

### 1.1 图片地址


图片地址建议使用服务器上可以访问的图片，http和https均可。
可以参考如下
https://magic-mirror-tianmu.oss-cn-shanghai.aliyuncs.com/person-images/3a993f1e-e9bd-4181-b94a-e825c1224287.png

https://magic-mirror-tianmu.oss-cn-shanghai.aliyuncs.com/background-images/2c275d82-a50f-423b-a562-dc3fe83ee2db.png

https://magic-mirror-tianmu.oss-cn-shanghai.aliyuncs.com/phone-uploads/2851a9d8-647b-46d6-8e52-d188add2999d.png

https://magic-mirror-tianmu.oss-cn-shanghai.aliyuncs.com/merchant-1/products/962bcd67-b7c2-43dd-b549-9a05d8117155.jpg

### 1.2 路由分组

| 分组 | 地址前缀 | 用途 | 默认鉴权 |
|---|---|---|---|
| 商家后台 | `/mirror4b-admin` | 登录、商品、套装、门店、设备、运营数据管理 | 除登录外均需 Bearer Token |
| 魔镜端 | `/mirror4b` | 商品同步、聊天、推荐、试衣、人物图/背景图、手机上传 | 按接口分别要求，见下文 |
| 静态 H5 | `/mirror4b-admin-web` | 商家后台及手机上传/保存页面 | 无 |

通用请求头：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

上传接口使用 `multipart/form-data`，不要手动将 JSON 请求头用于上传请求。

通用错误结构为：

```json
{"detail": "错误描述"}
```

常见状态码：`400` 请求参数或业务校验失败，`401` 未登录或 Token 无效，`404` 资源不存在，`409` 重复资源，`502` 上游算法/LLM/图片服务失败，`503` 相关服务未配置，`504` 上游超时。

## 2. 认证接口

### 2.1 商家登录

```http
POST /mirror4b-admin/login
Content-Type: application/x-www-form-urlencoded
```

用途：校验商家账号密码并返回 JWT。

请求参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `username` | string | 是 | 商家用户名 |
| `password` | string | 是 | 商家密码 |

返回：

```json
{"access_token": "<JWT>", "token_type": "bearer"}
```

### 2.2 获取当前商家

```http
GET /mirror4b-admin/me
Authorization: Bearer <access_token>
```

返回：`{"id": 1, "username": "demo", "store_name": "示例门店"}`。

## 3. 商家后台接口

以下接口均需 `Authorization: Bearer <access_token>`。

### 3.1 商品管理 `/mirror4b-admin/products`

| 方法 | 地址 | 用途 | 请求 | 返回 |
|---|---|---|---|---|
| `POST` | `/analyze-image` | 根据商品图片分析商品字段 | JSON：`{"image_url":"https://..."}` | `category_data`、`feature_data`、`sales_data`、`raw_result` |
| `GET` | `/` | 分页查询商品 | Query：`skip` 默认 `0`、`limit` 默认 `100`、`status`、`search` | `{"items":[Product],"total":总数}` |
| `POST` | `/upload/image` | 上传商品主图到 OSS | multipart：`file` | `{"url":"图片地址"}` |
| `POST` | `/` | 创建商品 | JSON：`sku`、`name`、`main_image_url`、`category_data`、`feature_data`、`sales_data`、`stocks` | `Product` |
| `GET` | `/{product_id}` | 查询商品详情 | Path：`product_id` | `Product` |
| `PUT` | `/{product_id}` | 更新商品 | JSON：上述字段，均可选 | 更新后的 `Product` |
| `DELETE` | `/{product_id}` | 删除商品及关联库存/套装项 | Path：`product_id` | `{"ok":true}` |
| `POST` | `/batch-upload/images` | 批量上传商品图片并生成预览 | multipart：多个 `files` | `batch_code`、`items`、`total`；此时尚未入库 |
| `POST` | `/batch-upload/zip` | 上传 ZIP 并提取其中图片生成预览 | multipart：`file` | `batch_code`、`items`、`total`；此时尚未入库 |
| `POST` | `/batch-confirm` | 确认批量商品并正式入库 | JSON：`{"items":[{sku,name,main_image_url,category_data,feature_data,sales_data}]}` | `success_count`、`fail_count`、逐条 `results` |
| `POST` | `/sync/trigger` | 将当前商品标记为已同步 | 无 | `{"message":"Sync triggered successfully","synced_count":数量}` |

`Product` 主要结构：

```json
{
  "id": 1,
  "merchant_id": 1,
  "sku": "SKU001",
  "name": "白色衬衫",
  "main_image_url": "https://...",
  "category_data": {"category": "上衣", "brand": "..."},
  "feature_data": {"material": "棉", "style_tags": ["简约"]},
  "sales_data": {"price": 299, "total_stock": 10, "status": "在售"},
  "stocks": [{"id": 1, "product_id": 1, "size": "M", "quantity": 5}],
  "sync_status": false
}
```

### 3.2 AI 搭配与套装

| 方法 | 地址 | 用途 | 请求 | 返回 |
|---|---|---|---|---|
| `POST` | `/mirror4b-admin/outfits/upload-model` | 上传男/女模特图 | multipart：`file` | `{"url":"图片地址"}` |
| `POST` | `/mirror4b-admin/outfits/generate` | 创建 AI 搭配任务 | JSON：`requirements`，可选 `male_model_url`、`female_model_url` | `task_id`、`message`、`status` |
| `GET` | `/mirror4b-admin/outfit-sets/` | 分页查询商品套装 | Query：`skip`、`limit` | `{"items":[OutfitSet],"total":数量}` |
| `POST` | `/mirror4b-admin/outfit-sets/` | 创建套装 | JSON：`name`、可选 `description`、`product_ids`（至少两件且不能重复） | `OutfitSet` |
| `GET` | `/mirror4b-admin/outfit-sets/{outfit_set_id}` | 查询套装 | Path：`outfit_set_id` | `OutfitSet` |
| `PUT` | `/mirror4b-admin/outfit-sets/{outfit_set_id}` | 修改套装 | JSON：`name`、`description`、`product_ids`（均可选） | 更新后的 `OutfitSet` |
| `DELETE` | `/mirror4b-admin/outfit-sets/{outfit_set_id}` | 删除套装 | Path：`outfit_set_id` | `{"ok":true}` |

当前 `outfits/generate` 的代码实现返回模拟任务 ID，不能据此认为后台已完成真实异步生成。

### 3.3 门店管理

| 方法 | 地址 | 用途 | 请求/参数 | 返回 |
|---|---|---|---|---|
| `GET` | `/mirror4b-admin/stores/` | 查询门店 | Query：`region`、`search` | `items`、`total`、`regions` |
| `POST` | `/mirror4b-admin/stores/` | 创建门店 | JSON：`name`、`region`、可选 `address`、`status`、`opened_at` | `Store`，状态码 `201` |
| `PUT` | `/mirror4b-admin/stores/{store_id}` | 更新门店 | JSON：可选门店字段 | `Store` |
| `PUT` | `/mirror4b-admin/stores/{store_id}/metrics/{metric_date}` | 新增/更新某日经营指标 | JSON：`usage_count`、`try_on_started_count`、`try_on_completed_count`、`purchase_count` | `StoreMetric` |

### 3.4 设备管理

| 方法 | 地址 | 用途 | 请求/参数 | 返回 |
|---|---|---|---|---|
| `GET` | `/mirror4b-admin/devices/overview` | 设备汇总 | 无 | `total_devices`、在线/离线数量、`covered_stores`、`online_rate` 等 |
| `GET` | `/mirror4b-admin/devices/` | 查询设备 | Query：`store_id`、`region`、`status`、`search`、`attention_only` | `{"items":[Device],"total":数量}` |
| `POST` | `/mirror4b-admin/devices/` | 创建设备 | JSON：`store_id`、`device_code`、`sn`、版本字段、网络字段 | `Device`，状态码 `201` |
| `GET` | `/mirror4b-admin/devices/{device_id}` | 查询设备详情 | Path：`device_id` | `Device` |
| `PUT` | `/mirror4b-admin/devices/{device_id}` | 更新设备 | JSON：设备字段（可选） | `Device` |
| `POST` | `/mirror4b-admin/devices/{device_id}/heartbeat` | 上报设备心跳 | JSON：`status`、`network_type`，可选版本和 `reported_at` | 更新后的 `Device` |

设备状态：`online`、`offline`、`weak_online`、`pending_upgrade`；网络类型：`wifi`、`ethernet`、`4g`、`none`。

### 3.5 门店运营看板

| 方法 | 地址 | 用途 | 参数 | 返回 |
|---|---|---|---|---|
| `GET` | `/mirror4b-admin/store-dashboard/` | 查询经营总览、漏斗、门店表现 | `days` 默认 `7`（范围 1–365）、`region`、`search` | `period_start`、`period_end`、`overview`、`funnel`、`stores`、`regions` |
| `GET` | `/mirror4b-admin/store-dashboard/stores/{store_id}` | 查询单店详情 | `days` 默认 `7` | 门店信息、表现、设备列表、每日趋势 |

## 4. 魔镜端接口

### 4.1 数据同步

> 当前实现的三个同步接口都依赖商家 Bearer Token。返回内容只包含当前商家商品/套装数据。

| 方法 | 地址 | 用途 | 返回 |
|---|---|---|---|
| `GET` | `/mirror4b/sync/products` | 获取当前商家在售商品 | `Product[]` |
| `GET` | `/mirror4b/sync/outfits` | 获取后台收藏的 AI 搭配 | `Outfit[]`，含 `id`、`generated_image_url`、`items` |
| `GET` | `/mirror4b/sync/outfit-sets` | 获取后台保存的商品套装 | `OutfitSetSync[]`，含套装名、描述和 `items` |

### 4.2 聊天与 Agent

#### 普通聊天

```http
POST /mirror4b/chat
Content-Type: application/json
```

请求：

```json
{"message":"帮我推荐一件适合通勤的衣服","history":[{"role":"user","content":"..."}]}
```

`history` 可省略。返回：`{"reply":"..."}`。

#### Agent 聊天

```http
POST /mirror4b/chat/agent
Content-Type: application/json
```

请求字段：`text` 必填；`current_page`、`history`、`excluded_product_ids` 可选。

返回：

```json
{
  "status": "SUCCESS",
  "message": {
    "text": "...",
    "action": {"type": "NAVIGATE", "payload": {"route_name": "...", "intent": "...", "parameters": {}}},
    "recommended_products": [],
    "debug_note": null
  }
}
```

该接口可携带 Bearer Token；推荐商品时会按 Token 对应商家筛选商品，但接口本身不强制登录。

#### 商品点评

```http
POST /mirror4b/chat/product-review
Content-Type: application/json
```

请求字段：`name` 必填；`product_id`、`category`、`material`、`description`、`brand`、`price`、`user_intent` 可选。

返回：`status`、三个点评 `points`（标签通常为“版型/搭配/场景”）和 `review`；LLM 失败时返回本地兜底点评。

### 4.3 试衣与换背景

以下接口均使用 `multipart/form-data`。

| 方法 | 地址 | 必填字段 | 可选字段 | 用途/返回 |
|---|---|---|---|---|
| `POST` | `/mirror4b/fitting/try-on` | `person_image`、`garment_url` | 无 | 单件试衣，返回 `{"result_url":"图片地址"}` |
| `POST` | `/mirror4b/fitting/try-on-multiple` | `person_image`、`garment_urls` | 无 | 多件试衣；`garment_urls` 必须是 JSON 数组字符串，如 `["url1","url2"]`；返回 `result_url` |
| `POST` | `/mirror4b/fitting/recommend-and-try-on` | `person_image` | `description` | 推荐并试穿，返回 `advice` 和 `outfits[]`（每套含 `outfit_number`、`tryon_result_url`、`items`） |
| `POST` | `/mirror4b/fitting/change-background` | `image` | `prompt` 或 `bg_image` 至少一个 | 文字换背景或指定背景图换背景，返回 `{"result_url":"图片地址"}` |

`change-background` 规则：仅文字模式必须提供 `prompt`；上传 `bg_image` 后采用双图模式并忽略文字 prompt。上游超时返回 `504`，连接或响应格式异常返回 `502`。

### 4.4 推荐

#### 商城模式

```http
POST /mirror4b/recommend/mall-mode
Content-Type: application/json
```

请求：`messages` 必填（包含人物图和文字消息的 OpenAI 风格消息数组），`model`、`temperature` 可选。

返回：`Outfit[]`，每项含 `id`、`generated_image_url`、`items`。接口允许匿名调用；未找到人物图、推荐服务失败或无有效结果时使用商品库兜底结果。

#### 衣橱模式

```http
POST /mirror4b/recommend/wardrobe-mode
Content-Type: multipart/form-data
Authorization: Bearer <access_token>
```

字段：`person_image` 必填，`description` 可选。

返回：

```json
{
  "advice": "整体搭配建议",
  "outfits": [
    {
      "name": "搭配名称",
      "description": "适用场景简介",
      "item_indices": [1, 3],
      "preview_image_url": "https://..."
    }
  ]
}
```

推荐服务失败时返回基于当前商家商品的基础兜底结果。

### 4.5 人物图和背景图

这些接口当前不依赖登录，供魔镜端或无登录页面使用。

| 方法 | 地址 | 请求 | 返回 |
|---|---|---|---|
| `GET` | `/mirror4b/person-images/` | 无 | `[{"object_name":"person-images/...","url":"https://..."}]` |
| `POST` | `/mirror4b/person-images/upload` | multipart：`file`（图片） | `{"url":"...","object_name":"person-images/..."}` |
| `DELETE` | `/mirror4b/person-images/?object_name=person-images/...` | Query：`object_name` | `{"ok":true}` |
| `GET` | `/mirror4b/background-images/` | 无 | `[{"object_name":"background-images/...","url":"https://..."}]` |
| `POST` | `/mirror4b/background-images/upload` | multipart：`file`（图片） | `{"url":"...","object_name":"background-images/..."}` |
| `DELETE` | `/mirror4b/background-images/?object_name=background-images/...` | Query：`object_name` | `{"ok":true}` |

删除接口会校验对象名前缀，人物图只能删除 `person-images/`，背景图只能删除 `background-images/`。

### 4.6 手机扫码上传

#### 创建会话

```http
POST /mirror4b/phone-upload/create-session
```

返回：

```json
{
  "session_id": "uuid",
  "upload_url": "http://.../mirror4b-admin-web/phone_upload.html?session_id=uuid",
  "expires_at": "2026-07-23T12:00:00"
}
```

会话有效期为 30 分钟。若二维码需要让手机访问局域网地址，客户端应使用实际可访问的 `baseUrl` 自行拼接页面地址。

#### 手机上传

```http
POST /mirror4b/phone-upload/upload/{session_id}
Content-Type: multipart/form-data
```

字段：`file`（图片）。成功返回：`{"ok":true,"image_url":"https://...","session_id":"uuid"}`。一个会话只能成功上传一次。

#### 查询会话状态

```http
GET /mirror4b/phone-upload/session/{session_id}
```

返回字段：`session_id`、`status`、`image_url`、`created_at`、`uploaded_at`、`expires_at`。`status` 主要为 `pending`、`uploaded` 或 `expired`；不存在的会话返回 `404`。

### 4.7 保存试穿结果到手机

| 方法 | 地址 | 用途 | 返回 |
|---|---|---|---|
| `POST` | `/mirror4b/save-to-phone/prepare` | 上传图片并生成二维码页面地址 | `{"page_url":"...save_to_phone.html?image_url=...","image_url":"..."}` |
| `GET` | `/mirror4b/save-to-phone/proxy?url=<图片URL>` | H5 同源代理下载网络图片 | 二进制图片，`Content-Disposition: attachment`，文件名为 `魔镜试穿效果.jpg` |

`proxy` 只接受 `http://` 或 `https://` URL；服务端无法拉取图片时返回 `502`。静态页面地址为 `/mirror4b-admin-web/save_to_phone.html`。

### 4.8 语音

#### 获取阿里云 NLS 临时 Token

```http
GET /mirror4b/tts/nls-token
```

返回：`token`、`appkey`、`region`、`ws_url`、`expire_at_epoch`。未配置阿里云 NLS 凭据返回 `503`。

#### TTS 合成

```http
POST /mirror4b/tts/synthesize
Content-Type: application/json
```

请求：

```json
{"text":"欢迎使用魔镜","voice":"可选音色 ID"}
```

返回 `audio/mpeg` 二进制音频，响应头为 `Content-Disposition: inline; filename=tts.mp3`。未配置 CosyVoice 或上游失败时返回 `503`/`502`。

## 5. 静态页面地址

| 地址 | 用途 |
|---|---|
| `/mirror4b-admin-web/` | 重定向到商家后台首页 |
| `/mirror4b-admin-web/admin4b.html` | 商家后台首页 |
| `/mirror4b-admin-web/phone_upload.html?session_id=<session_id>` | 手机扫码上传页 |
| `/mirror4b-admin-web/save_to_phone.html?image_url=<图片URL>` | 保存试穿结果到手机页面 |

## 6. 最小调用示例

```bash
# 1. 登录
curl -X POST "{BASE_URL}/mirror4b-admin/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=demo" \
  --data-urlencode "password=demo-password"

# 2. 使用 Token 同步商品
curl "{BASE_URL}/mirror4b/sync/products" \
  -H "Authorization: Bearer <access_token>"

# 3. 单件试衣
curl -X POST "{BASE_URL}/mirror4b/fitting/try-on" \
  -F "person_image=@person.jpg" \
  -F "garment_url=https://example.com/garment.jpg"

# 4. 创建手机上传会话
curl -X POST "{BASE_URL}/mirror4b/phone-upload/create-session"
```

## 7. 代码依据

- 路由注册：`server/app/main.py`、`server/app/api/mirror4b/v1/api.py`
- 商家鉴权：`server/app/api/mirror4b/deps.py`
- 请求/返回模型：`server/app/schemas/mirror4b/`
- 详细业务实现：`server/app/api/mirror4b/v1/endpoints/`
- 功能设计说明：`docs/mirror4b.md`
