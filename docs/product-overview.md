# Local Paper Knowledge Engine 产品说明

## 产品简介

Local Paper Knowledge Engine 是一个本地优先的论文知识管理工具。它以 research idea 为中心组织论文，把每个 idea 做成一个独立空间，并将论文解析为可检索、可追溯、可供外部 agent 调用的文献上下文。

产品本身不运行科研 agent，也不替用户执行实验。它负责管理论文、解析 PDF、生成 passage 和 knowledge card，并通过本地 MCP Server 把当前 idea space 的文献知识提供给 Codex、Claude Code、Cursor 等外部 agent。

## 桌面应用

当前产品迁移为 Tauri 桌面应用。桌面应用使用中文三栏研究工作台界面，并在启动时自动拉起本地 Python API sidecar。用户不需要手动启动 FastAPI 服务。

第一阶段 macOS 支持生成未签名 `.dmg`，Windows 预留 Windows 构建环境和安装包配置。正式对外分发前需要代码签名。

## 适用场景

- 围绕一个研究 idea 收集和管理相关论文。
- 在本地保存论文文件、元数据、解析文本和结构化卡片。
- 让外部 agent 在写代码、分析实验结果或设计后续实验时查询已有文献。
- 快速查找某个 idea 下的相关方法、指标、结果、局限性和失败模式。
- 保持不同 research idea 的论文上下文隔离，减少跨项目污染。

## 核心概念

### Idea Space

Idea Space 是产品的核心组织单位。一个 space 对应一个研究想法、课题方向或待验证假设。

每个 space 拥有独立的论文、解析结果、全文索引、knowledge cards 和 agent 暴露上下文。用户在 UI 中打开一个 space 后，上传、检索和 agent 访问默认都围绕当前 active space 进行。

### Paper

Paper 是导入到某个 idea space 的论文对象。当前版本支持通过 UI 上传 PDF，并允许用户手动编辑标题、作者、年份、DOI、arXiv ID、PubMed ID、venue、abstract、citation、标签和与 idea 的关系。

论文关系包括：

- supports
- refutes
- inspires
- baseline
- method_source
- background
- result_comparison
- unclassified

### Passage

Passage 是从 PDF 中解析出的原文片段。每个 passage 记录论文、章节、页码、段落序号、原文文本、解析置信度和类型。

Passage 是检索和引用溯源的基本单位。外部 agent 获取文献依据时，应优先依赖这些带来源的片段。

### Knowledge Card

Knowledge Card 是从论文 passage 中抽取或由用户手动创建的结构化知识单元。

支持的类型包括：

- Problem
- Claim
- Evidence
- Method
- Object
- Variable
- Metric
- Result
- Failure Mode
- Interpretation
- Limitation
- Practical Tip

每张 card 可以绑定来源 passage，用于保留文献出处。

## 当前功能

### Idea Space 管理

用户可以在本地 Web UI 中：

- 创建 idea space。
- 查看 space 列表。
- 设置 active space。
- 重命名 space。
- 编辑 space 描述。
- 归档或删除 space。

### 论文导入与元数据管理

当前版本支持：

- 上传 PDF 到当前 active space。
- 基于文件哈希检测同一 space 内的重复 PDF。
- 查看当前 space 下的论文列表。
- 编辑论文元数据。
- 设置论文与当前 idea 的关系。

### PDF 解析

用户可以对单篇论文触发解析。系统会：

- 使用 PyMuPDF 读取 PDF 文本。
- 按页和段落切分为 passages。
- 根据关键词粗略判断章节类型。
- 将 passages 写入 SQLite。
- 同步 passages 到 SQLite FTS5 全文索引。

### 全文检索

系统提供基于 SQLite FTS5 的本地全文检索。用户可以在当前 active space 中搜索论文正文，结果包含论文、章节、页码、段落和匹配信息。

该能力适合查找方法名、指标名、实验条件、专有术语和论文中的关键表述。

### Knowledge Card 管理

用户可以：

- 手动创建 knowledge card。
- 查看某篇论文的 cards。
- 删除 cards。
- 对已解析论文执行规则式自动抽取。

当前自动抽取是领域无关的启发式抽取。它会根据通用科研表达生成低置信度 knowledge cards，但不声明系统自动理解所有科研领域。用户应检查、编辑并保留有价值的卡片。

### 结构化文献视图

UI 提供按 card 类型聚合的结构化视图，帮助用户查看当前 idea space 中的方法、指标、结果、失败模式、局限性和 claims。

### Agent 接入

系统提供本地 MCP Server，供外部 agent 通过 stdio transport 调用。

当前 MCP 工具包括：

- `list_spaces`
- `get_active_space`
- `list_papers`
- `search_literature`
- `get_paper_summary`
- `get_citation`
- `get_methods`
- `get_metrics`
- `get_limitations`
- `find_failure_modes`
- `find_similar_results`
- `compare_with_literature`
- `get_evidence_for_claim`

UI 提供 Agent Access 开关。默认未启用时，MCP 工具会拒绝访问；启用后，外部 agent 可以查询本地文献知识。

为避免跨项目上下文污染，MCP 只暴露当前 active space。即使工具参数中传入其他 space id，服务也会拒绝访问非 active space 的论文、卡片和检索结果。

## 典型工作流

1. 启动本地 Web 应用。
2. 创建一个 idea space，例如 “small-sample robustness study”。
3. 打开该 space 作为 active space。
4. 上传相关论文 PDF。
5. 为论文补充标题、作者、年份、标签和关系。
6. 点击 Parse PDF 解析论文正文。
7. 执行 Auto-Extract 生成初始 knowledge cards。
8. 手动补充或修正重要 cards。
9. 在 UI 中启用 Agent Access。
10. 在外部 agent 中连接 MCP Server，让 agent 查询当前论文知识。

## 外部 Agent 可以如何使用

当用户要求 agent 分析科研问题时，agent 可以调用本产品查询文献上下文。

示例：

- 搜索某个方法在当前 idea 论文中的使用方式。
- 查找相关评价指标。
- 查找某类失败模式或局限性。
- 对用户实验观察调用 `compare_with_literature`，获取相似文献段落和相关 cards。
- 获取某篇论文的结构化摘要和引用信息。

产品返回的是文献证据和结构化上下文，而不是最终科研结论。最终分析仍由外部 agent 和用户共同完成。

## 本地运行

安装开发依赖：

```bash
pip install -e ".[dev]"
```

启动 Web 应用：

```bash
uvicorn paper_engine.api.app:app --reload --host 127.0.0.1 --port 8000
```

启动 MCP Server：

```bash
paper-engine-mcp
```

## 数据存储

当前默认数据目录为项目根目录下的 `app-data/`。也可以通过 `PAPER_ENGINE_DATA_DIR` 环境变量指定其他本地数据目录。

主要内容包括：

- SQLite 数据库：`app-data/paper_engine.db`
- space 文件目录：`app-data/spaces/`
- 每个 space 下的 PDF 文件：`app-data/spaces/<space-id>/papers/`

## 当前边界

当前版本不提供：

- 云同步。
- 多用户协作。
- 完整 Zotero 替代能力。
- DOI、arXiv、PubMed 在线元数据自动补全。
- 向量检索。
- LLM 语义抽取。
- 实验数据管理。
- 实验执行。
- 文件导出。
- CLI 工作流。

## 当前实现状态说明

当前实现已经覆盖 idea space、PDF 上传、元数据编辑、PDF 文本解析、SQLite FTS 检索、knowledge card 管理、结构化视图和 MCP Server 的基础能力。

需要注意的是，自动抽取仍是规则式启发，准确性有限；PDF 解析对复杂双栏、表格、公式和扫描件的支持有限；agent 接入应严格依赖 active space 和来源可追溯结果，避免跨项目上下文污染。
