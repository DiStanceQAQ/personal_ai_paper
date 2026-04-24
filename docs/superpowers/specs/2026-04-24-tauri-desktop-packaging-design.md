# Tauri 桌面分发迁移设计

## 1. 目标

将当前本地论文知识引擎从 `FastAPI + static/index.html` 的本地 Web UI，迁移为可分发的 Tauri 桌面应用。

迁移后的应用应支持：

- macOS 打包为 `.dmg`。
- Windows 打包为 `.exe` 或安装器。
- 用户安装后无需手动启动 FastAPI 服务。
- 桌面应用启动时自动启动本地 Python 后端。
- 退出应用时自动停止本地 Python 后端。
- 保留现有 FastAPI API、SQLite 数据、PDF 解析、FTS 检索、knowledge cards 和 MCP 逻辑。
- UI 从单文件 Web UI 重构为更高级、更适合桌面工作的中文界面。

## 2. 非目标

本次迁移不做以下事情：

- 不重写 Python 后端为 Rust。
- 不重写 MCP 协议逻辑。
- 不改变论文、space、passage、knowledge card 的核心数据模型。
- 不引入云同步、账号系统或多人协作。
- 不做复杂自动更新系统。
- 不在第一阶段完成正式代码签名和 notarization。
- 不保证在 macOS 本机直接交叉构建 Windows 安装包。

## 3. 推荐架构

采用 `Tauri 2 + React + Vite + TypeScript + Python sidecar`。

```txt
Local Paper Knowledge Engine
  |
  |-- src-tauri/
  |     |-- Tauri app shell
  |     |-- sidecar process control
  |     |-- bundling config
  |
  |-- frontend/
  |     |-- React/Vite UI
  |     |-- desktop-first Chinese interface
  |     |-- API client for local backend
  |
  |-- backend Python app
        |-- main.py / routes_*.py
        |-- SQLite / PyMuPDF / FTS
        |-- packaged by PyInstaller
```

Tauri 负责桌面窗口、打包、sidecar 启停和本地文件能力。React 负责 UI。Python 后端继续提供 HTTP API。MCP server 继续作为独立入口供外部 agent 配置使用。

## 4. 进程模型

### 开发模式

开发时运行两个服务：

- Vite dev server。
- FastAPI dev server。

Tauri dev 模式加载 Vite 页面，React 通过 `http://127.0.0.1:<port>` 调用 FastAPI。

### 打包模式

打包后应用包含：

- Tauri 可执行程序。
- React 静态资源。
- PyInstaller 打出来的 Python API sidecar。
- 可选的 MCP server sidecar。

应用启动流程：

1. Tauri 启动。
2. Tauri 选择一个本地空闲端口，或使用固定 localhost 端口并处理端口占用。
3. Tauri 启动 Python API sidecar，并传入端口、数据目录等环境变量。
4. React UI 轮询 `/health`，直到后端可用。
5. UI 进入主界面。

应用退出流程：

1. Tauri 收到窗口关闭事件。
2. Tauri 停止 Python API sidecar。
3. Tauri 正常退出。

## 5. 数据目录

当前项目默认使用项目根目录下的 `app-data/`。桌面分发版本不能继续依赖项目目录。

打包版本应使用系统应用数据目录：

- macOS：`~/Library/Application Support/<app-name>/`
- Windows：`%APPDATA%/<app-name>/`
- Linux：`~/.local/share/<app-name>/`

Tauri 启动 sidecar 时设置：

```txt
PAPER_ENGINE_DATA_DIR=<system app data dir>
```

Python 后端继续通过 `PAPER_ENGINE_DATA_DIR` 读取数据目录，不需要改数据模型。

## 6. 后端打包

Python 后端使用 PyInstaller 打包为 sidecar 可执行文件。

建议拆出两个入口：

- API sidecar：启动 FastAPI/uvicorn。
- MCP sidecar：启动 `mcp_server.py`。

示例目标：

```txt
dist/
  paper-engine-api
  paper-engine-mcp
```

Tauri `externalBin` 配置将这些二进制包含进应用包。

第一阶段重点保证 API sidecar 随桌面应用自动启动。MCP sidecar 可以先随包分发，并在产品说明中展示给用户配置到 Claude Code / Codex 的路径。

## 7. UI 重构方向

当前 `static/index.html` 是单页管理界面。迁移后前端改为 React/Vite。

桌面布局：

- 左侧栏：Idea spaces、创建空间、归档状态。
- 顶栏：当前空间、智能代理访问状态、后端连接状态、全局搜索入口。
- 中间主区：论文列表、文献检索、结构化文献视图。
- 右侧 inspector：当前论文详情、元数据编辑、解析状态、knowledge cards、passages。

视觉风格：

- 高级研究工作台，而不是网页后台。
- 中文优先，信息密度适合科研工作。
- 浅色主界面，纸白、墨黑、冷灰为基础色。
- 使用少量青绿色或钴蓝表达 active、parsed、agent enabled 等状态。
- 减少大面积卡片堆叠，采用分栏、列表、表格、inspector、状态徽标。
- 拖拽导入 PDF 要成为第一屏可见的核心操作。

产品表达需要明确区分“领域无关的论文结构管理”和“自动理解所有科研领域”。UI 中不应暗示系统能自动懂所有学科。自动抽取能力应标注为“启发式抽取”，并提示用户需要检查和修正结果。

结构化视图继续使用领域无关的 card 类型，例如方法、指标、结果、局限性、失败模式、证据、变量和研究对象。不要引入某个单一领域的专用 workflow 作为默认主界面。

## 8. API 边界

React UI 继续调用现有 HTTP API：

- `/api/spaces`
- `/api/papers`
- `/api/search`
- `/api/cards`
- `/api/agent`
- `/health`

短期不通过 Tauri command 重写业务 API。这样可以最大限度复用现有测试和后端逻辑。

Tauri command 只负责桌面能力：

- 启动 sidecar。
- 停止 sidecar。
- 提供后端端口。
- 提供应用数据目录。
- 后续可支持打开文件选择器、打开 PDF 所在位置等桌面能力。

## 9. 打包策略

### macOS

第一阶段目标：

- 能在本机生成 `.app` 和 `.dmg`。
- 能在未签名环境下本地安装和测试。

正式分发前需要：

- Apple Developer ID 证书。
- app signing。
- notarization。

否则其他用户可能遇到 Gatekeeper 安全提示。

### Windows

第一阶段目标：

- 配置 Windows 构建脚本。
- 预留 GitHub Actions Windows runner 构建流程。

Windows 安装包建议在 Windows 环境构建。目标可以是：

- `.exe` installer。
- `.msi` 或 NSIS installer。

正式分发前需要：

- Windows code signing certificate。
- CI 中安全注入签名证书。

## 10. 迁移步骤

### 阶段一：前端工程化

- 新建 `frontend/`。
- 使用 Vite + React + TypeScript。
- 将当前 API 调用封装到 `frontend/src/api.ts`。
- 实现桌面三栏 UI。
- 将自动抽取相关 UI 文案改为“启发式抽取”，避免暗示系统自动理解所有领域。
- 保留现有 `static/index.html` 作为临时 fallback，直到 Tauri UI 验证通过。

### 阶段一补充：抽取逻辑定位修正

- 保留通用 `Knowledge Card` schema。
- 将当前偏 AI/ML 的关键词抽取描述为低置信度启发式结果。
- 扩展关键词覆盖更通用的科研表达，例如 sample、cohort、intervention、protocol、measurement、assay、synthesis、yield、stability、statistical test 等。
- 不为第一版添加复杂领域模板。
- 所有自动抽取出的 card 保持可编辑，并继续保留来源 passage。

### 阶段二：Tauri 壳

- 新建 `src-tauri/`。
- 配置 Tauri 2。
- 连接 Vite dev/build。
- 验证 `tauri dev` 能打开 React UI。

### 阶段三：Python API sidecar

- 增加 API sidecar 启动入口。
- 增加 PyInstaller 配置或脚本。
- 将 API sidecar 加入 Tauri external binary。
- Tauri 启动时自动拉起 API sidecar。
- UI 等待 `/health` 成功后再进入主界面。

### 阶段四：打包

- 生成 macOS `.dmg`。
- 验证安装后可创建 space、上传 PDF、解析、搜索、创建 card。
- 验证关闭应用后 sidecar 被停止。
- 验证数据写入系统应用数据目录。

### 阶段五：Windows 构建

- 添加 Windows 构建说明。
- 添加 GitHub Actions Windows build。
- 验证 Windows 安装包中的 Python sidecar 和 PyMuPDF 依赖可运行。

## 11. 测试与验证

必须保留并继续运行：

- Python pytest。
- Python mypy。
- 前端 TypeScript typecheck。
- 前端 build。
- Tauri dev smoke test。
- Tauri build smoke test。

新增验证：

- 桌面应用启动后后端自动可用。
- 桌面应用关闭后后端进程退出。
- 数据目录位于系统应用数据目录。
- 打包后的 app 能解析 PDF。
- 打包后的 app 能执行全文检索。
- MCP sidecar 可以被外部 agent 单独启动。

## 12. 风险

### Python sidecar 体积

PyMuPDF 和 FastAPI 打包后会增加安装包体积。第一阶段接受体积增加，优先保证可用。

### 端口冲突

固定端口可能被占用。更稳妥的方案是 Tauri 找空闲端口并通过环境变量传给 sidecar。

### macOS 安全限制

未签名/未 notarize 的 `.dmg` 给其他用户安装时可能出现安全提示。正式分发需要签名和 notarization。

### Windows 构建环境

Windows 安装包最好在 Windows runner 上构建。macOS 本机不作为 Windows 正式构建来源。

### MCP 配置路径

打包后 MCP sidecar 路径会变成应用包内部路径。需要在 UI 或文档中给出可复制的配置方式。

## 13. 需要用户确认

进入实现前需要确认：

- 第一阶段是否只要求 macOS `.dmg` 可打包，Windows 先保留 CI/脚本方案。
- 是否接受 Python 后端作为 sidecar 随应用分发，安装包体积会明显增加。
- UI 是否按“高级中文研究工作台”的三栏布局实现。
- MCP sidecar 第一阶段是否只随包提供，不自动写入外部 agent 配置。
