# 后端 API 测试文档

本文档基于当前 FastAPI 应用实际挂载的路由整理，用来手工验证后端功能。默认以 Web 开发后端 `http://127.0.0.1:8000` 为例；Tauri sidecar 默认端口是 `8765`。

## Review 摘要

- 当前主应用挂载了 `spaces`、`papers`、`cards`、`search`、`agent` 五组 router。`/api/cards` 只作为 active space 的只读汇总入口；新增、更新、删除仍走纸张作用域下的 `/api/papers/{paper_id}/cards`。
- 绝大多数业务接口依赖全局 `active_space`，这是本地单用户产品可以接受的模型，但测试时要先创建并激活 Space；切换 Space 后，旧 Space 的 Paper/Card/Search 会按 active scope 返回 `404` 或 `403`。
- 上传接口会把整个 PDF 读入内存，并主要按文件名后缀校验 `.pdf`。本地桌面场景问题不大；如果以后暴露到网络或处理超大 PDF，建议补充大小限制和内容嗅探。
- API route 层已经拆到 `paper_engine/api/routes/*`，但部分 service 文件仍保留 `APIRouter` 和 route decorator，容易让维护者误以为 service router 会被挂载。建议后续把 service 层收敛为纯业务函数。
- OpenAPI 当前返回类型多为 `dict[str, Any]` / `list[dict[str, Any]]`，够用但不利于客户端生成和响应契约测试。建议逐步补 Pydantic request/response schema。
- API 主进程现在默认不跑重后台任务；PDF parse、embedding、AI analysis 由独立 `paper-engine-worker` 进程消费队列。开发时只跑 `make dev` 可验证 HTTP 契约；要测完整导入/解析/向量化流程，需要同时启动 worker。

## 启动方式

推荐用独立数据目录测试，避免污染真实 `app-data`：

```bash
source .venv/bin/activate
PAPER_ENGINE_DATA_DIR="$(pwd)/app-data-api-test" make dev
```

如果只想验证 API 入参、状态码和队列创建，不想跑后台解析/分析：

```bash
source .venv/bin/activate
PAPER_ENGINE_DATA_DIR="$(pwd)/app-data-api-test" make dev
```

如果要在 Web 开发模式下测试完整后台流程，另开一个终端启动 worker：

```bash
source .venv/bin/activate
paper-engine-worker --data-dir "$(pwd)/app-data-api-test"
```

sidecar 方式：

```bash
source .venv/bin/activate
paper-engine-api --host 127.0.0.1 --port 8765 --data-dir "$(pwd)/app-data-api-test"
```

常用地址：

```txt
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/docs
GET http://127.0.0.1:8000/openapi.json
```

## 快速冒烟测试

下面示例依赖 `curl` 和 `jq`。如果没有 `jq`，也可以直接看原始 JSON。

```bash
BASE=http://127.0.0.1:8000

curl -s "$BASE/health" | jq

SPACE_ID=$(curl -s -X POST "$BASE/api/spaces" \
  -H "Content-Type: application/json" \
  -d '{"name":"API Test Space","description":"Manual backend API test"}' | jq -r '.id')

curl -s -X PUT "$BASE/api/spaces/active/$SPACE_ID" | jq
curl -s "$BASE/api/spaces/active" | jq

PDF_PATH="reference_paper/1-s2.0-S016819232500098X-main.pdf"
PAPER_ID=$(curl -s -X POST "$BASE/api/papers/upload" \
  -F "file=@$PDF_PATH;type=application/pdf" | jq -r '.id')

curl -s "$BASE/api/papers/$PAPER_ID" | jq '{id,title,parse_status,embedding_status,queued_parse_run_id}'
curl -s "$BASE/api/papers/$PAPER_ID/parse-runs" | jq
curl -s "$BASE/api/papers/$PAPER_ID/embedding-runs" | jq

curl -s -X POST "$BASE/api/papers/$PAPER_ID/parse" | jq
curl -s "$BASE/api/papers/$PAPER_ID/passages" | jq '.[0:3]'

curl -s "$BASE/api/search?q=crop&limit=5&mode=fts" | jq

CARD_ID=$(curl -s -X POST "$BASE/api/papers/$PAPER_ID/cards" \
  -H "Content-Type: application/json" \
  -d '{"card_type":"Method","summary":"Manual API test card","confidence":0.9}' | jq -r '.id')

curl -s "$BASE/api/papers/$PAPER_ID/cards/$CARD_ID" | jq
curl -s -X PATCH "$BASE/api/papers/$PAPER_ID/cards/$CARD_ID" \
  -H "Content-Type: application/json" \
  -d '{"summary":"Updated manual API test card"}' | jq
curl -s -X DELETE "$BASE/api/papers/$PAPER_ID/cards/$CARD_ID" | jq
```

如果要测试 AI analysis，先确认 paper 已解析完成且已有 passages：

```bash
curl -s "$BASE/api/papers/$PAPER_ID" | jq '{id,parse_status}'
curl -s "$BASE/api/papers/$PAPER_ID/passages" | jq 'length'

RUN_ID=$(curl -s -X POST "$BASE/api/papers/$PAPER_ID/analysis-runs" | jq -r '.id')
curl -s "$BASE/api/papers/$PAPER_ID/analysis-runs/$RUN_ID" | jq
```

## 通用响应和错误

成功响应基本都是 JSON。常见错误：

| 状态码 | 场景 |
| --- | --- |
| `400` | 没有 active space、上传非 PDF、PDF 文件在磁盘不存在 |
| `403` | 显式传入非 active space 的 `space_id` |
| `404` | 资源不存在，或资源不属于当前 active space |
| `409` | 分析前置条件未满足，例如 PDF 还没解析完成 |
| `422` | FastAPI 参数校验失败，或业务枚举/范围不合法 |

## 数据模型速览

`Space`：

```json
{
  "id": "uuid",
  "name": "API Test Space",
  "description": "",
  "status": "active",
  "created_at": "2026-04-30 10:00:00",
  "updated_at": "2026-04-30 10:00:00"
}
```

`Paper` 关键字段：

```json
{
  "id": "uuid",
  "space_id": "uuid",
  "title": "Paper title",
  "authors": "",
  "year": 2026,
  "doi": "",
  "venue": "",
  "abstract": "",
  "relation_to_idea": "unclassified",
  "file_path": "/path/to/pdf",
  "file_hash": "sha256",
  "parse_status": "pending",
  "embedding_status": "pending",
  "metadata_status": "empty",
  "queued_parse_run_id": "uuid"
}
```

`KnowledgeCard` 关键字段：

```json
{
  "id": "uuid",
  "space_id": "uuid",
  "paper_id": "uuid",
  "source_passage_id": null,
  "card_type": "Method",
  "summary": "text",
  "confidence": 0.9,
  "user_edited": 0,
  "created_by": "user",
  "analysis_run_id": null
}
```

## Health 与应用信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 返回静态 UI HTML；没有 UI 时返回简单 HTML |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/info` | 返回 `project_root` 和 `os` |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/openapi.json` | OpenAPI schema |

示例：

```bash
curl -s "$BASE/health" | jq
curl -s "$BASE/api/info" | jq
```

## Spaces

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/spaces` | `{"name": "...", "description": "..."}` | 创建 Space |
| `GET` | `/api/spaces` | 无 | 列出非 deleted Space，包含 archived |
| `GET` | `/api/spaces/active` | 无 | 读取当前 active Space |
| `PUT` | `/api/spaces/active/{space_id}` | 无 | 设置 active Space，只接受 `status=active` |
| `GET` | `/api/spaces/{space_id}` | 无 | 读取 Space，deleted 返回 `404` |
| `PATCH` | `/api/spaces/{space_id}` | `{"name": "...", "description": "..."}` | 更新名称或描述 |
| `PATCH` | `/api/spaces/{space_id}/archive` | 无 | 归档 Space；如果它是 active，会清空 active |
| `DELETE` | `/api/spaces/{space_id}` | 无 | 标记为 deleted；如果它是 active，会清空 active |

示例：

```bash
curl -s -X POST "$BASE/api/spaces" \
  -H "Content-Type: application/json" \
  -d '{"name":"Soil Water Modeling","description":"Papers for model comparison"}' | jq

curl -s "$BASE/api/spaces" | jq
curl -s -X PUT "$BASE/api/spaces/active/$SPACE_ID" | jq
curl -s -X PATCH "$BASE/api/spaces/$SPACE_ID" \
  -H "Content-Type: application/json" \
  -d '{"description":"Updated description"}' | jq
```

## Papers

Paper 接口都限制在 active space 内。`GET /api/papers?space_id=...` 只允许传当前 active space；传其他 Space 会返回 `403`。

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/papers/upload` | multipart `file` | 上传 PDF，创建 paper，并创建 queued parse run |
| `POST` | `/api/papers/upload/batch` | multipart `files` 多个 | 批量上传 PDF；每个文件独立返回成功/失败并创建 queued parse run |
| `GET` | `/api/papers` | `space_id?` | 列出 active space 的 papers |
| `GET` | `/api/papers/{paper_id}` | 无 | 获取 paper |
| `GET` | `/api/papers/{paper_id}/metadata` | 无 | 获取核心元数据和 provenance |
| `PATCH` | `/api/papers/{paper_id}` | 见下方字段 | 更新 paper 元数据 |
| `DELETE` | `/api/papers/{paper_id}` | 无 | 删除 paper、关联 passages/cards/notes/FTS，并删除磁盘 PDF |

`PATCH /api/papers/{paper_id}` 支持字段：

```json
{
  "title": "New title",
  "authors": "Author A; Author B",
  "year": 2026,
  "doi": "10.0000/example",
  "arxiv_id": "",
  "pubmed_id": "",
  "venue": "Journal",
  "abstract": "Abstract text",
  "citation": "Citation text",
  "user_tags": "tag1, tag2",
  "relation_to_idea": "supports"
}
```

`relation_to_idea` 允许值：

```txt
supports, refutes, inspires, baseline, method_source, background, result_comparison, unclassified
```

示例：

```bash
curl -s -X POST "$BASE/api/papers/upload" \
  -F "file=@$PDF_PATH;type=application/pdf" | jq

curl -s -X POST "$BASE/api/papers/upload/batch" \
  -F "files=@reference_paper/a.pdf;type=application/pdf" \
  -F "files=@reference_paper/b.pdf;type=application/pdf" | jq

curl -s "$BASE/api/papers" | jq
curl -s "$BASE/api/papers/$PAPER_ID/metadata" | jq

curl -s -X PATCH "$BASE/api/papers/$PAPER_ID" \
  -H "Content-Type: application/json" \
  -d '{"title":"Manual Test Title","relation_to_idea":"background"}' | jq
```

上传限制：

- 批量上传默认最多 `20` 个文件，可用 `PAPER_ENGINE_BATCH_UPLOAD_MAX_FILES` 调整。
- 单个 PDF 默认最大 `200MB`，可用 `PAPER_ENGINE_UPLOAD_MAX_BYTES` 调整。
- 批量上传中单个文件超限会作为该文件失败返回，不影响其他文件；文件数量超限会直接返回 `413`。

## PDF 解析与结构化内容

上传 PDF 会自动 queue 一个 parse run；也可以手动重新 queue。解析完成后会自动创建 queued embedding run，二者由 worker 进程异步消费。

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/papers/{paper_id}/parse` | 无 | 重新创建 queued parse run |
| `GET` | `/api/papers/{paper_id}/parse-runs` | 无 | 查看该 paper 的 parse runs |
| `GET` | `/api/papers/{paper_id}/embedding-runs` | 无 | 查看该 paper 的 embedding runs 和批处理统计 |
| `GET` | `/api/papers/{paper_id}/passages` | 无 | 查看解析后的文本 passages |
| `GET` | `/api/papers/{paper_id}/elements` | `type?`, `page?`, `limit?` | 查看结构化 document elements |
| `GET` | `/api/papers/{paper_id}/tables` | 无 | 查看结构化 tables |

`POST /parse` 响应示例：

```json
{
  "status": "queued",
  "paper_id": "uuid",
  "passage_count": 0,
  "parse_run_id": "uuid",
  "backend": "docling",
  "quality_score": null,
  "warnings": []
}
```

示例：

```bash
curl -s -X POST "$BASE/api/papers/$PAPER_ID/parse" | jq
curl -s "$BASE/api/papers/$PAPER_ID/parse-runs" | jq
curl -s "$BASE/api/papers/$PAPER_ID/embedding-runs" | jq
curl -s "$BASE/api/papers/$PAPER_ID/elements?type=paragraph&page=1&limit=5" | jq
curl -s "$BASE/api/papers/$PAPER_ID/tables" | jq
```

状态说明：

- `papers.parse_status`: `pending`、`parsing`、`parsed`、`error`。
- `papers.embedding_status`: `pending`、`running`、`completed`、`failed`、`skipped`。
- embedding 失败不会再回滚成功的 PDF parse；此时 `parse_status=parsed`，`embedding_status=failed`，可通过 `embedding-runs` 查看 `last_error`。

## Knowledge Cards

Cards 有两层入口：`/api/cards` 用于按 active space 只读汇总查询；`/api/papers/{paper_id}/cards` 用于某篇 paper 下的创建、读取、更新、删除。

Card type 允许值：

```txt
Problem, Claim, Evidence, Method, Object, Variable, Metric, Result, Failure Mode, Interpretation, Limitation, Practical Tip
```

`confidence` 必须在 `0.0` 到 `1.0` 之间。

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/cards` | `paper_id?`, `card_type?` | 列出 active space 下的 cards，可按 paper/type 过滤 |
| `GET` | `/api/cards/{card_id}` | 无 | 在 active space 内读取单个 card |
| `GET` | `/api/papers/{paper_id}/cards` | `card_type?` | 列出 paper cards |
| `POST` | `/api/papers/{paper_id}/cards` | `card_type`, `summary?`, `source_passage_id?`, `confidence?` | 创建用户 card |
| `GET` | `/api/papers/{paper_id}/cards/{card_id}` | 无 | 读取单个 card |
| `PATCH` | `/api/papers/{paper_id}/cards/{card_id}` | `summary?`, `card_type?`, `confidence?` | 更新 card，并标记为用户编辑 |
| `DELETE` | `/api/papers/{paper_id}/cards/{card_id}` | 无 | 删除 card |

示例：

```bash
curl -s -X POST "$BASE/api/papers/$PAPER_ID/cards" \
  -H "Content-Type: application/json" \
  -d '{"card_type":"Claim","summary":"The paper claims improved crop modeling.","confidence":0.8}' | jq

curl -s "$BASE/api/papers/$PAPER_ID/cards?card_type=Claim" | jq
curl -s "$BASE/api/cards?paper_id=$PAPER_ID&card_type=Claim" | jq
curl -s -X PATCH "$BASE/api/papers/$PAPER_ID/cards/$CARD_ID" \
  -H "Content-Type: application/json" \
  -d '{"confidence":0.95}' | jq
curl -s -X DELETE "$BASE/api/papers/$PAPER_ID/cards/$CARD_ID" | jq
```

## Search

Search 仅限 active space。显式传非 active `space_id` 会返回 `403`。

| 方法 | 路径 | Query | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/search` | `q`, `space_id?`, `limit?`, `mode?` | 检索 passages |

参数：

| 参数 | 说明 |
| --- | --- |
| `q` | 必填，最小长度 1 |
| `space_id` | 可选；只能是当前 active space |
| `limit` | 默认 `50`，范围 `1..200` |
| `mode` | 可选：`fts` 或 `hybrid` |

响应项关键字段：

```json
{
  "score": 1.0,
  "passage_id": "uuid",
  "paper_id": "uuid",
  "section": "method",
  "page_number": 1,
  "paragraph_index": 0,
  "snippet": "...",
  "original_text": "...",
  "paper_title": "..."
}
```

示例：

```bash
curl -s "$BASE/api/search?q=soil%20moisture&limit=10&mode=fts" | jq
curl -s "$BASE/api/search?q=crop&space_id=$SPACE_ID&mode=hybrid" | jq
```

## AI Analysis Runs

分析运行依赖已解析 paper：`parse_status` 必须是 `parsed`，且 passages 至少有 1 条。否则会返回 `409`。

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `POST` | `/api/papers/{paper_id}/analysis-runs` | 无 | 创建 queued analysis run，成功状态码 `202` |
| `GET` | `/api/papers/{paper_id}/analysis-runs` | 无 | 列出 analysis runs |
| `GET` | `/api/papers/{paper_id}/analysis-runs/{run_id}` | 无 | 获取单个 run |
| `POST` | `/api/papers/{paper_id}/analysis-runs/{run_id}/cancel` | 无 | 取消 queued/running run |

示例：

```bash
curl -s -X POST "$BASE/api/papers/$PAPER_ID/analysis-runs" | jq
curl -s "$BASE/api/papers/$PAPER_ID/analysis-runs" | jq
curl -s -X POST "$BASE/api/papers/$PAPER_ID/analysis-runs/$RUN_ID/cancel" | jq
```

## Agent 与配置

配置读取会隐藏 API key，只返回 `has_api_key` / `has_llamaparse_api_key` / `has_mineru_api_key`。

| 方法 | 路径 | Body / Query | 说明 |
| --- | --- | --- | --- |
| `GET` | `/api/agent/status` | 无 | 获取 MCP agent access 状态和 active space |
| `PUT` | `/api/agent/status` | `{"enabled": true}` | 设置 agent access |
| `PUT` | `/api/agent/enable` | 无 | 启用 agent access |
| `PUT` | `/api/agent/disable` | 无 | 禁用 agent access |
| `GET` | `/api/agent/config` | 无 | 读取 LLM/PDF parser 配置，密钥脱敏 |
| `PUT` | `/api/agent/config` | 见下方 | 更新 LLM/PDF parser 配置 |
| `POST` | `/api/agent/config/mineru/test` | 无 | 测试 MinerU 连接 |

`PUT /api/agent/config` 支持字段：

```json
{
  "llm_provider": "openai",
  "llm_base_url": "https://api.openai.com/v1",
  "llm_model": "gpt-4o",
  "llm_timeout_seconds": 180,
  "llm_api_key": "secret",
  "llamaparse_base_url": "https://api.cloud.llamaindex.ai",
  "llamaparse_api_key": "secret",
  "pdf_parser_backend": "docling",
  "mineru_base_url": "https://example.com",
  "mineru_api_key": "secret"
}
```

注意：

- `llm_timeout_seconds` 范围是 `5..600`。
- `pdf_parser_backend` 只允许 `docling` 或 `mineru`。
- 空字符串或 `null` API key 不会覆盖已有密钥。

示例：

```bash
curl -s "$BASE/api/agent/status" | jq
curl -s -X PUT "$BASE/api/agent/status" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true}' | jq

curl -s "$BASE/api/agent/config" | jq
curl -s -X PUT "$BASE/api/agent/config" \
  -H "Content-Type: application/json" \
  -d '{"llm_provider":"openai","llm_model":"gpt-4o","pdf_parser_backend":"docling"}' | jq
curl -s -X POST "$BASE/api/agent/config/mineru/test" | jq
```

## 负向测试清单

这些用例适合确认后端校验和 active space 隔离是否正常。

```bash
# 没有 active space 时上传 PDF，应返回 400。
curl -i -X POST "$BASE/api/papers/upload" \
  -F "file=@$PDF_PATH;type=application/pdf"

# 非 PDF 后缀，应返回 400。
curl -i -X POST "$BASE/api/papers/upload" \
  -F "file=@README.md;type=text/markdown"

# 无 active space 搜索，应返回 400。
curl -i "$BASE/api/search?q=test"

# 非法 search mode，应返回 422。
curl -i "$BASE/api/search?q=test&mode=semantic"

# 顶层 cards 写操作仍不开放，应返回 405。
curl -i -X POST "$BASE/api/cards" \
  -H "Content-Type: application/json" \
  -d '{"paper_id":"x","card_type":"Method","summary":"bad"}'

# 非法 card type，应返回 422。
curl -i -X POST "$BASE/api/papers/$PAPER_ID/cards" \
  -H "Content-Type: application/json" \
  -d '{"card_type":"Unknown","summary":"bad"}'

# 非法 confidence，应返回 422。
curl -i -X POST "$BASE/api/papers/$PAPER_ID/cards" \
  -H "Content-Type: application/json" \
  -d '{"card_type":"Method","summary":"bad","confidence":1.5}'

# PDF 未解析完成时创建 analysis run，应返回 409。
curl -i -X POST "$BASE/api/papers/$PAPER_ID/analysis-runs"
```

## 建议的后端自动化测试命令

快速跑 API 相关测试：

```bash
source .venv/bin/activate
.venv/bin/pytest -q \
  tests/test_main.py \
  tests/test_routes_spaces.py \
  tests/test_routes_papers.py \
  tests/test_routes_cards.py \
  tests/test_search.py \
  tests/test_agent.py
```

完整后端检查：

```bash
source .venv/bin/activate
make check
```
