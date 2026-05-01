<h1 align="center">Local Paper Knowledge Engine</h1>

<p align="center">
  本地优先的 AI 论文研究助手。
  <br />
  PDF -> 中文理解 -> 可溯源知识卡片 -> MCP。
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-0f766e"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776AB">
  <img alt="FastAPI" src="https://img.shields.io/badge/api-FastAPI-009688">
  <img alt="React" src="https://img.shields.io/badge/frontend-React%20%2B%20Vite-61DAFB">
  <img alt="Tauri" src="https://img.shields.io/badge/desktop-Tauri-FFC131">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-ready-111827">
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a>
</p>

Local Paper Knowledge Engine 是一个本地优先的桌面应用，用来把论文 PDF
变成可检索、可溯源、可被 AI 理解的研究知识库。它支持按研究主题划分空间，
把 PDF 解析成段落，建立本地检索索引，生成中文论文理解，沉淀稳定的知识卡片，
并通过 MCP Server 把当前工作空间开放给 Claude Code、Codex、Cursor 等外部
Agent 使用。

> 状态：活跃早期版本。核心本地工作流、桌面打包、AI 解析、检索和 MCP 访问
> 已经实现，并有测试覆盖。

## 功能

- 研究空间：按主题或项目隔离论文与知识。
- PDF 导入、批量导入、后台解析和解析诊断。
- 本地 SQLite 存储论文、段落、知识卡片、AI 解析记录和配置。
- 本地优先检索：支持 FTS，并可选语义检索加速。
- 中文 AI 论文理解：生成有原文证据支撑的结构化理解。
- 每篇论文生成 5 张稳定 AI 知识卡片：研究问题、方法、结果、结论、局限。
- PDF 原文查看：方便核对知识卡片对应的原文证据。
- MCP Server：让外部编码/研究 Agent 读取当前活动研究空间。
- Tauri 桌面壳：前端 UI、Python API 和后台 worker 作为本地 sidecar 运行。

## 架构

```text
React/Vite UI
    |
Tauri desktop shell
    |
FastAPI sidecar  ---- SQLite + local PDF files
    |
background worker sidecar
    |-- PDF parsing
    |-- embeddings
    |-- AI paper understanding
    |
MCP stdio server
```

当前 AI 解析主流程是：

```text
PDF -> passages -> metadata -> paper_understanding_zh -> 5 derived cards
```

生成的知识卡片会存入 `knowledge_cards`，每张卡片都保留来源 passage 证据。

## 环境要求

- Python 3.11 或更高版本。
- Node.js 和 npm。
- 如果要运行或打包 Tauri 桌面应用，需要 Rust/Cargo。

## 安装

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,pdf-advanced]"

npm install
npm --prefix frontend install
```

也可以使用 Makefile：

```bash
make install
make frontend-install
npm install
```

## 启动 API 开发服务

```bash
source .venv/bin/activate
make dev
```

打开：

```text
http://127.0.0.1:8000
```

常用地址：

```text
http://127.0.0.1:8000/health
http://127.0.0.1:8000/docs
```

## 启动 Tauri 桌面应用

```bash
source .venv/bin/activate
make tauri-dev
```

桌面开发命令会启动 React/Vite 前端，并在启动前检查本地 Python sidecar。

## 前端开发

```bash
npm run frontend:dev
```

Vite 开发服务地址：

```text
http://127.0.0.1:1420
```

如果直接在浏览器打开前端，也需要同时用 `make dev` 启动后端。

## 构建

macOS 桌面安装包：

```bash
source .venv/bin/activate
make package-macos
```

构建产物位于：

```text
src-tauri/target/release/bundle/
```

更多说明见：[docs/packaging.md](docs/packaging.md)。

## MCP Server

启动 MCP Server：

```bash
paper-engine-mcp
```

如果要连接 macOS 打包应用的数据目录：

```bash
PAPER_ENGINE_DATA_DIR="$HOME/Library/Application Support/com.local.paperknowledgeengine" paper-engine-mcp
```

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "paper-knowledge-engine": {
      "command": "/path/to/paper-engine-mcp"
    }
  }
}
```

MCP 访问默认关闭。连接外部 Agent 前，需要先在应用里开启 Agent Access。
MCP 工具只暴露当前 active idea space，不会跨空间读取数据。

## 数据与隐私

开发环境默认数据目录：

```text
app-data/
```

可以通过环境变量覆盖：

```bash
PAPER_ENGINE_DATA_DIR=/path/to/data make dev
```

不要提交本地数据或真实 API Key。请使用 `.env.example` 作为配置模板。

隐私说明见：[docs/privacy.md](docs/privacy.md)。

## PDF 解析

应用支持本地解析和服务型解析。Docling 在本地运行；MinerU 和 GROBID 是可选
HTTP 服务，如果配置启用，可能会接收 PDF 内容。

详见：[docs/pdf-ingestion.md](docs/pdf-ingestion.md)。

## 测试与质量检查

```bash
make test
make typecheck
npm run frontend:typecheck
npm run frontend:build
```

完整后端检查：

```bash
make check
```

## 样例数据

本仓库不包含第三方论文 PDF。测试数据说明见：
[docs/sample-data.md](docs/sample-data.md)。

## 许可证

MIT License。详见 [LICENSE](LICENSE)。
