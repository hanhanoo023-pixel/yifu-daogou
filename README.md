# StyleMate：对话式 AI 服装导购与虚拟试衣 Demo

StyleMate 是一个面向服装零售场景的对话式 AI 导购项目。用户可以像和真实导购聊天一样描述“想买黑色连衣裙”“换成春夏穿的”“预算 300 元以内”“想显高一点”等需求，系统会从商品数据库中筛选、排序并展示推荐商品，同时支持人物照片管理、AI 试穿、背景替换、结果保存、演示购物车和订单流程。

本仓库包含两个版本：

- 根目录版本：当前 StyleMate 主版本，保留阿里云 DashScope 试穿/换背景接口接入。
- `mirror4b/`：Mirror4B 改造版，在主项目基础上增加 Mirror4B 商品、Agent、购物车、试衣和商家 Token 适配。

仓库已经通过 Git LFS 附带当前运行所需的商品库、语义向量和 E5 基础模型。别人 clone 后只要拉取 LFS 文件并配置自己的 API Key，就可以运行当前项目。

## 完整功能

### 1. Mia 对话式动态导购

- 页面内置导购形象 Mia。
- 支持中文自然语言输入购物需求。
- 支持颜色、尺码、季节、场合、预算、品类、品牌、材质等条件解析。
- 支持“换成蓝色”“再便宜一点”“不要黑色”“尺码改成 L”这类连续修改。
- 支持开启新对话后清空当前会话上下文，避免上一轮试穿结果串到新对话。
- 支持继续聊天时保留导购头像和当前推荐上下文。

### 2. 意图识别与多轮会话

- `/api/chat` 融合规则解析和大模型结构化解析。
- 识别推荐、换款、浏览、商品解释、询价、库存、材质、尺码、颜色、品牌、越界和未知意图。
- 维护会话内的当前商品、已选商品、可见商品和过滤条件。
- 当事实型问题缺少商品上下文时主动澄清，不编造价格、库存、材质等信息。
- 支持删除 session，让新对话从空状态开始。

### 3. 商品推荐与排序

- 使用 SQLite 商品库检索当前可推荐商品。
- 当前可推荐商品量约 255,707 条。
- 商品库来源包括 Amazon 服装鞋包珠宝商品元数据，以及经过严格筛选的 Fashion200K 图文商品。
- 支持硬过滤：
  - 品类
  - 颜色
  - 尺码
  - 季节
  - 预算
  - 品牌
  - 材质
  - 库存
  - 排除商品 ID
- 支持软排序：
  - 语义相似度
  - 规则得分
  - 场景/风格匹配
  - 身材目标相关性
  - 库存和演示业务字段
- 当前排序策略为语义 35% + 规则 65% 融合。
- 商品推荐理由基于数据库字段生成，不使用不存在的商品信息。

### 4. 商品卡片与商品详情

- 每件商品展示标题、图片、价格、品类、颜色、尺码、库存等前端需要字段。
- 商品价格默认以人民币展示。
- 商品卡片支持查看详情。
- 商品卡片支持加入演示购物车。
- 点击某商品下方“加入演示购物车”时，弹出的购物车只聚焦该商品，用户可以选择尺码和数量。
- 点击页面顶部购物车时，展示所有已加入购物车的商品。

### 5. 人物照片中心

- 支持上传人物照片。
- 上传后不会自动选择该照片。
- 上传后不会自动把照片发送到对话界面。
- 点击照片可以选择该照片；再次点击可以取消选择。
- 只有主动选择人物照片并触发试穿时，才进入试穿流程。
- 支持删除人物照片。

### 6. AI 试穿

- 用户选择人物照片和商品后，可以创建 AI 试穿任务。
- 后端通过 `/api/try-on/tasks` 创建试穿任务。
- 前端通过任务轮询查看试穿状态和结果。
- 主版本使用 DashScope/阿里云相关接口配置。
- Mirror4B 改造版可接入 Mirror4B 试衣能力。
- 如果没有选择人物照片，不会自动试穿。

### 7. AI 换背景

- 支持对已有视觉结果发起背景替换任务。
- 后端通过 `/api/background/tasks` 创建背景任务。
- 前端轮询任务状态并展示结果。
- 背景替换依赖外部视觉 API，需要配置对应 API Key 和公网 HTTPS 访问地址。

### 8. 图片查看与结果保存

- 支持点击图片查看大图。
- 支持保存 AI 视觉结果。
- 保存结果按用户 profile 维度读取。
- 支持删除保存结果。

### 9. 演示购物车、订单和支付

- 支持加入演示购物车。
- 支持选择尺码和数量。
- 支持购物车商品删除。
- 支持创建演示订单。
- 支持演示支付状态流转。
- 支持读取用户历史演示订单。
- 购物车、订单和价格均为 Demo 流程，不连接真实支付。

### 10. 门店导购信息

- 支持展示门店导购 Mia 的演示联系信息。
- 可通过环境变量配置导购名称、服务渠道、联系方式和服务时间。
- 当前默认是演示门店服务，不代表真实门店客服系统已连接。

### 11. SafetyGuard 与推荐理由核验

- 推荐理由会经过安全核验。
- 检查标题、品类、价格、材质、库存、品牌、颜色、特征等字段是否有数据库依据。
- 阻止虚假最低价、虚假稀缺、绝对化效果承诺和不当身体评价。
- 支持 `PASS / REWRITE / BLOCK` 判定。
- 模拟价格、尺码、季节和库存必须标记为演示数据。

### 12. API usage 记录

- 所有大模型 API 调用代码需要打印 API 返回的原始 `usage`。
- 如果保存结果文件，也需要保存每一次有效 API 请求的原始 `usage`。
- `usage` 保持 API 返回原始结构，不改字段名、不压平、不删字段。

### 13. Mirror4B 改造版

`mirror4b/` 是从主项目复制出的独立改造版本，用于接入 Mirror4B 文档中的能力：

- Mirror4B 商品列表适配。
- Mirror4B Agent 对话接口适配。
- Mirror4B 商品字段到 StyleMate 商品卡片字段的转换。
- Mirror4B 购物车/订单相关演示逻辑。
- Mirror4B 试衣、换背景、保存手机等流程扩展预留。
- 默认币种为人民币 `CNY`。
- 需要商家 API 地址和 Bearer Token。

Mirror4B 版本的后端标题为 `StyleMate Mirror4B API`，可以在 `mirror4b/` 目录内独立运行。

## 技术栈

- 前端：Next.js 15、React 19、TypeScript
- 后端：FastAPI、Pydantic、HTTPX
- 数据库：SQLite
- 语义模型：`intfloat/multilingual-e5-small`
- 语义排序：E5 基础模型 + C 模块双塔投影 + 商品向量索引
- LLM：DeepSeek Chat，以及可配置的意图识别模型服务
- 视觉 API：DashScope/阿里云试穿与背景任务；Mirror4B 改造版支持 Mirror4B 接口适配
- 数据源：Amazon Reviews 2023 Clothing, Shoes and Jewelry；Fashion200K 图文子集

## 仓库结构

```text
backend/                         主版本 FastAPI 后端
backend/c_recommender/           语义编码、向量索引、规则排序和融合排序
configs/                         人设、意图、排序、安全、场景和币种配置
frontend/                        主版本 Next.js 前端
data/eval/                       小型评测草稿
data/experiments/                当前运行商品库和语义向量，使用 Git LFS 管理
models/c_recommender/            C 模块投影模型和 E5 基础模型，部分文件使用 Git LFS 管理
scripts/                         数据导入、清洗、审计、向量构建脚本
tests/                           主版本测试
mirror4b/                        Mirror4B 改造版
*.docx / *.pptx / 数据集000.md   项目说明、汇报和数据说明材料
```

不会上传真实密钥、运行日志、缓存、`node_modules`、原始 Amazon 大数据和本地临时目录。

## 运行数据与模型

为了让别人 clone 后能跑当前项目，本仓库通过 Git LFS 提供运行必需数据：

```text
data/experiments/catalog_hybrid_clean_v1.db
data/experiments/semantic_hybrid_clean_v1/product_embeddings.npy
data/experiments/semantic_hybrid_clean_v1/product_ids.txt
data/experiments/semantic_hybrid_clean_v1/product_embeddings.meta.json
models/c_recommender/multilingual-e5-small/
models/c_recommender/public_semantic_two_tower.pt
```

大文件包括：

```text
data/experiments/catalog_hybrid_clean_v1.db
data/experiments/semantic_hybrid_clean_v1/product_embeddings.npy
models/c_recommender/multilingual-e5-small/model.safetensors
```

如果 clone 后这些文件只有一百多字节，说明 Git LFS 文件没有拉下来，需要执行：

```bash
git lfs install
git lfs pull
```

## 本地启动：主版本

### 1. 克隆并拉取 LFS 文件

```bash
git clone https://github.com/hanhanoo023-pixel/yifu-daogou.git
cd yifu-daogou
git lfs install
git lfs pull
```

### 2. 后端环境

推荐使用 Python 3.10。

```bash
conda create -n YouCook2 python=3.10
conda activate YouCook2
pip install -r requirements.txt
```

复制环境变量模板：

```bash
cp .env.example .env
```

编辑 `.env`，填入自己的 API Key 和服务地址。不要提交 `.env`。

启动后端：

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/api/health
```

### 3. 前端环境

打开另一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器访问：

```text
http://localhost:3000
```

前端的 `frontend/next.config.ts` 已将 `/api/*` 转发到：

```text
http://127.0.0.1:8000/api/*
```

## 本地启动：Mirror4B 改造版

进入 Mirror4B 子项目：

```bash
cd mirror4b
```

后端环境可以复用主项目的 Python 环境：

```bash
conda activate YouCook2
pip install -r requirements.txt
cp .env.example .env
```

编辑 `mirror4b/.env`，至少配置：

```text
MIRROR4B_BASE_URL=https://your-mirror4b-domain.example
MIRROR4B_MERCHANT_TOKEN=your_mirror4b_merchant_token_here
MIRROR4B_CURRENCY=CNY
```

启动 Mirror4B 后端：

```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

启动 Mirror4B 前端：

```bash
cd frontend
npm install
npm run dev
```

Mirror4B 版仍然需要根项目同样的运行数据目录和模型目录。如果只在 `mirror4b/` 内单独部署，需要把根项目的 `data/experiments/` 和 `models/c_recommender/` 按相同结构准备好。

## 环境变量

根目录 `.env.example`：

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
STYLEMATE_INTENT_BASE_URL=https://api.siliconflow.cn/v1
STYLEMATE_INTENT_MODEL=openai-chat:Qwen/Qwen3.5-4B
STYLEMATE_INTENT_API_KEY=your_stylemate_intent_api_key_here
DASHSCOPE_API_KEY=your_beijing_dashscope_api_key_here
DASHSCOPE_BASE_URL=https://your-workspace.cn-beijing.maas.aliyuncs.com/api/v1
STYLEMATE_PUBLIC_BASE_URL=https://your-stylemate-domain.example
STYLEMATE_SALES_NAME=门店导购 Mia（演示）
STYLEMATE_SALES_CHANNEL=店内服务台
STYLEMATE_SALES_CONTACT=请向门店工作人员出示当前商品
STYLEMATE_SALES_HOURS=10:00–21:00（演示）
```

Mirror4B 额外变量：

```text
MIRROR4B_BASE_URL=https://your-mirror4b-domain.example
MIRROR4B_MERCHANT_TOKEN=your_mirror4b_merchant_token_here
MIRROR4B_CURRENCY=CNY
```

说明：

- `DEEPSEEK_API_KEY`：主对话生成使用。
- `STYLEMATE_INTENT_*`：意图识别模型服务使用。
- `DASHSCOPE_*`：阿里云试穿和背景任务使用。
- `STYLEMATE_PUBLIC_BASE_URL`：图片上传后生成公网可访问 URL 使用；试穿/换背景通常需要 HTTPS 公网地址。
- `MIRROR4B_*`：Mirror4B 改造版连接真实商家服务使用。
- 所有真实 Key/Token 都只能放 `.env`，不要提交 GitHub。

## 常用 API

主版本：

```text
GET    /api/health
POST   /api/chat
POST   /api/recommend
GET    /api/products/{product_id}
GET    /api/store/contact
GET    /api/cart/{profile_id}
POST   /api/cart/{profile_id}/items
DELETE /api/cart/{profile_id}/items/{product_id}
POST   /api/orders
GET    /api/orders/{profile_id}
POST   /api/orders/{order_id}/demo-pay
POST   /api/try-on/person-image
GET    /api/person-images/{profile_id}
DELETE /api/person-images/{profile_id}/{image_id}
POST   /api/try-on/tasks
GET    /api/try-on/tasks/{task_id}
POST   /api/background/tasks
GET    /api/background/tasks/{task_id}
POST   /api/saved-results
GET    /api/saved-results/{profile_id}
DELETE /api/sessions/{session_id}
```

Mirror4B 额外接口：

```text
GET  /api/mirror4b/products
POST /api/mirror4b/agent/chat
```

## 测试

后端测试：

```bash
conda activate YouCook2
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q
```

前端构建：

```bash
cd frontend
npm run build
```

Mirror4B 版测试：

```bash
cd mirror4b
PYTHONDONTWRITEBYTECODE=1 python -m pytest -p no:cacheprovider -q
```

## 数据构建与复现

当前仓库已经包含运行必需的数据库、向量和模型文件，因此 clone 后不需要重新构建数据就能运行推荐流程。

如果需要从原始 Amazon 数据重新构建商品库，可参考：

```bash
python scripts/build_filtered_product_database.py \
  --source amazon-data/meta_Clothing_Shoes_and_Jewelry.jsonl.gz.part \
  --database data/products_complete.db \
  --report data/rebuild_report.json \
  --expected-records 252413
```

重建语义向量：

```bash
python -m scripts.build_product_embeddings \
  --database data/products_complete.db \
  --base-model models/c_recommender/multilingual-e5-small \
  --projection models/c_recommender/public_semantic_two_tower.pt \
  --embeddings data/semantic/product_embeddings.npy \
  --product-ids data/semantic/product_ids.txt \
  --metadata data/semantic/product_embeddings.meta.json \
  --expected-records 257384 \
  --batch-size 64 \
  --device cpu
```

原始 Amazon 大数据不在本仓库内。当前 GitHub 仓库的目标是“clone 后能运行当前 Demo”，不是完整复现所有数据清洗过程。

## 安全与隐私

- `.env` 已加入 `.gitignore`，不要上传真实 API Key。
- 不要上传真实用户照片、真实订单、真实门店 Token。
- 试穿和背景任务依赖外部 API，调用前请确认图片授权和隐私合规。
- 本项目中的购物车、订单、价格、库存和门店联系流程均为 Demo。
- 如果任何 Key 曾经公开过，应立刻撤销并重新生成。

## 常见问题

### 1. clone 后推荐接口报数据库不存在

先检查 Git LFS：

```bash
git lfs install
git lfs pull
ls -lh data/experiments/catalog_hybrid_clean_v1.db
```

如果数据库只有一百多字节，说明拿到的是 LFS 指针，不是真文件。

### 2. 语义推荐报模型缺失

检查：

```bash
ls -lh models/c_recommender/multilingual-e5-small/model.safetensors
ls -lh data/experiments/semantic_hybrid_clean_v1/product_embeddings.npy
```

如果文件很小，重新执行：

```bash
git lfs pull
```

### 3. AI 试穿或换背景失败

检查 `.env`：

```text
DASHSCOPE_API_KEY
DASHSCOPE_BASE_URL
STYLEMATE_PUBLIC_BASE_URL
```

`STYLEMATE_PUBLIC_BASE_URL` 通常需要公网 HTTPS 地址，否则外部视觉服务无法读取上传图片。

### 4. Mirror4B 接口失败

检查 `mirror4b/.env`：

```text
MIRROR4B_BASE_URL
MIRROR4B_MERCHANT_TOKEN
MIRROR4B_CURRENCY
```

`MIRROR4B_MERCHANT_TOKEN` 是 Bearer Token，不要写进代码或 README。

### 5. 前端请求不到后端

确认后端运行在：

```text
http://127.0.0.1:8000
```

确认前端 `frontend/next.config.ts` 中 `/api/:path*` rewrite 指向同一个后端地址。

## 数据许可

项目代码可独立开源。Amazon Reviews 2023、Fashion200K、Mirror4B 商家数据和外部模型的使用、再分发、商用限制应遵守对应数据集、模型和 API 服务的官方许可与条款。

