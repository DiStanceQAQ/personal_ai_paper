# 本地论文知识引擎

一个本地优先的论文知识管理工具，用 Idea Space 组织论文、片段、知识卡片和检索结果。项目由 FastAPI 后端、React/Vite 前端和 Tauri 桌面壳组成。

## 环境要求

- Python 3.11 或更高版本
- Node.js 和 npm
- Rust/Cargo：仅运行或打包 Tauri 桌面端时需要

## 首次安装

在项目根目录执行：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,pdf-advanced]"

npm install
npm --prefix frontend install
```

也可以用 Makefile 安装 Python 和前端依赖；桌面端还需要根目录的 npm 依赖：

```bash
make install
make frontend-install
npm install
```

## PDF 解析后端

仓库默认安装和 `npm run tauri dev/build` 启动前预处理都会确保 Docling 可用，因此首次打开设置页时不应该再提示缺少本地高级解析依赖。单独补装时可执行：

```bash
pip install -e ".[pdf-advanced]"
```

解析路由会根据 PDF 质量自动选择 PyMuPDF4LLM、Docling、LlamaParse 或旧版 PyMuPDF；GROBID 可作为可选的论文元数据和参考文献增强服务。详细配置、隐私影响和后端取舍见 [docs/pdf-ingestion.md](docs/pdf-ingestion.md)。

## 启动后端 Web 版

这是最简单的启动方式，适合先确认 API 和内置静态页面可用：

```bash
source .venv/bin/activate
make dev
```

打开：

```txt
http://127.0.0.1:8000
```

健康检查地址：

```txt
http://127.0.0.1:8000/health
```

API 文档地址：

```txt
http://127.0.0.1:8000/docs
```

## 启动 Tauri 桌面开发版

桌面端会自动启动 React/Vite 前端，并在启动前依次检查 Docling 依赖、嵌入模型和 API sidecar：

```bash
source .venv/bin/activate
make tauri-dev
```

如果看到类似 `Unable to resolve API sidecar` 的错误，通常说明 sidecar 预处理失败，或当前平台的构建依赖（如 Python/Rust）不完整。

## 前端开发

React 前端的开发服务器端口是 `127.0.0.1:1420`：

```bash
npm run frontend:dev
```

前端默认请求 `http://127.0.0.1:8000`。如果只在浏览器里打开 Vite 页面，需要先启动后端：

```bash
source .venv/bin/activate
make dev
```

桌面端开发通常直接用 `make tauri-dev`，不需要手动单独启动前端。

## 打包

macOS 打包：

```bash
source .venv/bin/activate
make package-macos
```

打包产物在：

```txt
src-tauri/target/release/bundle/dmg/
```

也可以只执行通用 Tauri 构建：

```bash
make tauri-build
```

更多打包说明见 [docs/packaging.md](docs/packaging.md)。

## 数据目录

默认数据写入项目内的：

```txt
app-data/
```

可以通过环境变量改到其他位置：

```bash
PAPER_ENGINE_DATA_DIR=/path/to/data make dev
```

Tauri 桌面端会使用系统应用数据目录，并把该路径传给后端 sidecar。

## 常用检查命令

```bash
make test
make typecheck
npm run frontend:typecheck
```

完整后端检查：

```bash
make check
```

## 常见问题

- `uvicorn: command not found`：先激活 `.venv`，并执行 `pip install -e ".[dev]"`。
- `tauri: command not found`：先执行根目录的 `npm install`。
- 桌面端找不到 sidecar：执行 `make build-sidecars` 后再启动。
- 端口 `8000` 被占用：可以直接运行 `uvicorn paper_engine.api.app:app --reload --host 127.0.0.1 --port 其他端口`，前端请求地址需要同步调整。
