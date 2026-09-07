# StyleMate：对话式 AI 服装导购

StyleMate 是一个基于真实商品元数据、SQLite 商品数据库、DeepSeek 需求解析和 C 模块混合排序的对话式服装导购 Demo。用户可以用自然语言描述品类、颜色、季节、尺码、预算、品牌和材质，并通过多轮对话继续修改条件。

## 主要功能

- 中文自然语言购物需求解析，输出意图、置信度、解析来源和澄清状态
- 识别推荐、浏览、换款、询价、库存、材质、商品解释、越界和未知意图
- 检测价格、颜色、材质和品牌条件冲突，信息不足时主动澄清
- 多轮条件记忆，例如“换成蓝色”“尺码改成 XL”
- 25 万余条服装、鞋包、首饰商品检索
- 品类、价格、颜色、尺码、季节、品牌、材质和库存硬过滤
- 明确排除颜色、材质、品牌及商品ID
- 软负向颜色降权，不误做硬过滤
- E5 语义双塔与规则评分按 35% / 65% 融合排序
- 基于实际商品字段生成匹配依据
- 推荐理由逐字段核验，输出 `PASS / REWRITE / BLOCK` 和原始 Claims
- 有结果、无结果和库存不足原因反馈
- DeepSeek API 原始 `usage` 完整打印和保存
- 响应式 Next.js 商品导购界面

## 技术栈

- 前端：Next.js 15、React 19、TypeScript
- 后端：FastAPI、Pydantic、HTTPX
- 数据库：SQLite
- 模型：DeepSeek Chat（需求解析）；multilingual-e5-small + C模块双塔投影（语义排序）
- 数据源：Amazon Reviews 2023 — Clothing, Shoes and Jewelry；Fashion200K 图文子集

## 项目结构

```text
backend/                 FastAPI、数据库查询、DeepSeek 接入
backend/c_recommender/   C模块语义双塔、规则评分与融合排序
configs/                 推荐排序权重、禁用话术和安全规则
frontend/                Next.js 网页
scripts/                 数据导入、模型下载和商品向量构建脚本
tests/                   后端测试
data/                    本地数据库与处理报告
data/semantic/           257,384条商品向量与构建元数据，不提交GitHub
models/c_recommender/    E5基础模型与正式双塔投影
amazon-data/             本地 Amazon 原始数据，不提交 GitHub
logs/                    API usage 日志，不提交 GitHub
```

## C 模块接入状态

当前已完成两阶段接入：

- 第一阶段：SQLite硬过滤、规则软评分、商品排重、事实型推荐理由和模拟字段来源标识。
- 第二阶段：为257,384条商品离线生成128维向量；查询时只编码用户需求，并对硬过滤候选集执行“语义35% + 规则65%”融合排序。
- 图片与品类严格审计：1,677条不符合严格质量闸门的Fashion200K商品通过 `product_moderation` 逻辑下架；当前可推荐商品为255,707条。

商品库包含252,413条Amazon商品，以及4,971条具备精确名称映射、真实Fashion200K图片和非空颜色的C模块Demo商品。商品语义文本只使用数据库内的标题、品类、品牌、颜色、材质、特征和描述；模拟价格、尺码、季节及库存不进入商品语义向量。`amazon-data/C模块交付包_20260819` 仅作为原始交付包保存，运行代码不从该目录导入。

严格质量闸门仅保留原颜色审计为 `PASS`、单色目标像素比例不低于7%、且图片描述类别未与商品类别明确冲突的Fashion200K商品，共保留3,294条。当前向量元数据记录的数据库 SHA-256 为 `d35b32124767764aaba2f3af5e2dfcb89488c11690b1a32ed2a4a1d5f8bb5e41`，正式投影模型 SHA-256 为 `900d3083aee1a94be01fc9f5bfbdff3aecdf744c86dcce474b32c00cbe268b2d`。

## 意图理解与 SafetyGuard

`/api/chat` 使用规则优先项与 DeepSeek 结构化解析融合，返回固定意图枚举、置信度、解析来源、标准槽位、冲突、警告和澄清问题。事实型询问缺少具体商品上下文时只请求澄清，不生成价格、库存或材质答案。DeepSeek 请求失败会直接报错，不执行 fallback。

每条推荐理由由独立的 ProductExplanation 根据商品标题、中文品类、颜色、品牌、材质、商品特征及用户命中条件生成，再经过 SafetyGuard。系统核验标题、品类、价格、材质、库存、品牌、颜色和特征，检测虚假最低价、虚假稀缺、绝对化效果和不当身体评价，并返回原理由、安全理由、结构化 Claims、数据库字段支持率以及 `PASS / REWRITE / BLOCK`。模拟尺码、价格、季节和库存必须明确标记为演示数据。

## 本地运行

### 1. 配置后端

```bash
conda create -n YouCook2 python=3.10
conda activate YouCook2
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`，填写自己的 DeepSeek API Key：

```text
DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

启动后端：

```bash
uvicorn backend.app:app --port 8000
```

### 2. 配置前端

打开另一个终端：

```bash
cd frontend
npm install
npm run dev
```

浏览器访问 <http://localhost:3000>。

## 测试

```bash
conda run -n YouCook2 env PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -p no:cacheprovider -q

cd frontend
npm run build
```

## 数据构建

大型数据文件和 SQLite 数据库不提交 GitHub。下载 Amazon Reviews 2023 的 `Clothing_Shoes_and_Jewelry` 商品元数据后，可使用：

```bash
python scripts/build_filtered_product_database.py \
  --source amazon-data/meta_Clothing_Shoes_and_Jewelry.jsonl.gz.part \
  --database data/products_complete.db \
  --report data/rebuild_report.json \
  --expected-records 252413
```

当前 Demo 只保留具有真实商品 ID、标题、品类、Amazon 图片 URL 和明确颜色字段的商品。颜色不推断、不模拟；品牌、材质等缺失时保持为空。缺失的价格、尺码、季节和库存使用确定性演示值，并以 `synthetic_demo` 标记来源。模拟数据仅用于演示，不代表 Amazon 实时商品信息或真实库存。

## 语义模型与向量重建

基础模型固定为 `intfloat/multilingual-e5-small` 的提交 `614241f622f53c4eeff9890bdc4f31cfecc418b3`。在目标目录不存在时下载：

```bash
python -m scripts.download_semantic_model \
  --output models/c_recommender/multilingual-e5-small \
  --cache .cache/huggingface
```

重新生成向量前，应先移走现有的三个 `data/semantic/product_*` 产物；构建脚本不会覆盖已有文件。然后执行：

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

## 安全说明

- `.env`、API usage 日志、数据库、原始数据、构建缓存均已加入 `.gitignore`。
- 不要将 DeepSeek API Key 提交到 GitHub。
- 若密钥曾公开，应在发布项目前撤销并重新生成。

## 数据许可

项目代码可单独开源；Amazon 数据的使用和再分发应遵守数据集官方许可与条款。建议仓库只提供下载与生成说明，不直接提交原始商品数据或生成数据库。
