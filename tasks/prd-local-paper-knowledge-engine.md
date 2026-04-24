# PRD: 本地论文知识引擎

## 1. Introduction / Overview

本地论文知识引擎是一个本地优先的论文管理与文献上下文服务。产品以 research idea 为中心组织论文，每个 idea space 都拥有独立的论文、解析结果、全文索引、结构化知识卡片和 agent 访问上下文。

第一版不做科研 agent，也不替用户生成最终科研结论。它负责把用户已有论文处理成可检索、可追溯、可供外部 agent 调用的知识基础。外部 agent 可以是 Codex、Claude Code、Cursor、ChatGPT Desktop、NotebookLM 或其他用户习惯的平台。

MVP 技术假设：使用本地 Web UI、FastAPI 本地服务、SQLite/FTS5、PyMuPDF PDF 解析和本地 MCP Server。向量检索、复杂 PDF 解析器和联网元数据补全作为可选后续能力，不阻塞第一版完成。

## 2. Goals

- 允许用户围绕一个 research idea 创建独立 idea space。
- 支持向当前 space 导入 PDF，并保存原始文件和基础元数据。
- 将 PDF 解析为可追溯 passage，包含论文、章节、页码和原文片段。
- 使用 SQLite FTS5 提供可靠的全文检索。
- 从 passage 中抽取或维护 knowledge cards，包含 claim、method、metric、result、limitation、failure mode 等类型。
- 在 UI 中提供论文、passage、card 和结构化文献视图。
- 通过本地 MCP Server 让外部 agent 查询当前 active space 的文献知识。
- 保证 agent 返回的每条文献信息都能追溯到具体论文和原文 passage。

## 3. Non-Goals

- 不运行或托管科研 agent。
- 不直接执行实验或管理实验数据生命周期。
- 不做云同步、账号系统、团队协作或手机端。
- 不做公开论文平台、推荐流或社交功能。
- 不做完整 Zotero 替代品。
- 不做复杂自动系统综述生成器。
- 不要求用户使用 CLI 或手动导出文件。
- 第一版不强依赖向量数据库、GROBID、Docling、marker 或外部 LLM API。

## 4. User Stories

### US-001: Bootstrap local application foundation

**Description:** 作为个人研究者，我希望能启动一个本地应用，以便在浏览器中管理论文知识库。

**Acceptance Criteria:**
- [ ] 创建本地应用结构，包含 FastAPI 服务、基础 Web UI 入口和项目运行说明。
- [ ] 提供健康检查接口，返回服务状态。
- [ ] 提供一个可在本机启动的开发命令。
- [ ] 基础测试命令可以运行。
- [ ] Typecheck passes.

### US-002: Create SQLite storage foundation

**Description:** 作为系统维护者，我需要稳定的本地数据库结构，以便保存 spaces、papers、passages、cards 和索引状态。

**Acceptance Criteria:**
- [ ] 初始化 SQLite 数据库文件和应用数据目录。
- [ ] 建立 spaces、papers、passages、knowledge_cards、notes 和 app_state 表。
- [ ] 为每个 space 相关表保存 space_id，后续查询可以按 space 隔离。
- [ ] 数据库初始化可以重复执行且不会破坏已有数据。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-003: Manage idea spaces through API

**Description:** 作为研究者，我希望创建、打开、重命名、归档和删除 idea space，以便把不同研究方向隔离管理。

**Acceptance Criteria:**
- [ ] 提供 create、list、get、rename、archive、delete space 的 API。
- [ ] 每个 space 保存名称、描述、状态、创建时间和更新时间。
- [ ] 提供设置和读取 active space 的 API。
- [ ] 删除或归档 space 不会影响其他 space 的数据。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-004: Manage idea spaces in the UI

**Description:** 作为研究者，我希望在本地 UI 中管理 idea spaces，以便不需要使用 CLI。

**Acceptance Criteria:**
- [ ] UI 显示所有 idea spaces 及其状态。
- [ ] 用户可以创建、打开、重命名、归档和删除 space。
- [ ] 当前 active space 在 UI 中明确可见。
- [ ] Space 描述可以编辑，并保存为后续相关性判断的输入。
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-005: Import and store PDF papers

**Description:** 作为研究者，我希望向当前 space 导入 PDF，以便系统保存论文并开始解析。

**Acceptance Criteria:**
- [ ] UI 支持选择或拖拽 PDF 到当前 active space。
- [ ] 后端将原始 PDF 保存到当前 space 的 papers 目录。
- [ ] 系统记录 paper id、文件路径、文件哈希、导入时间和解析状态。
- [ ] 同一 space 内重复 PDF 可以被识别并阻止重复导入。
- [ ] Tests pass.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-006: Edit paper metadata

**Description:** 作为研究者，我希望查看和编辑论文元数据，以便在自动解析失败时手动修正。

**Acceptance Criteria:**
- [ ] Paper 支持标题、作者、年份、DOI、arXiv ID、PubMed ID、会议/期刊、摘要、引用信息、用户标签和与 idea 的关系字段。
- [ ] UI 提供 paper metadata 编辑表单。
- [ ] 用户可以通过 DOI、arXiv、PubMed 或 BibTeX 文本补充标识字段和引用字段，第一版不要求联网自动补全。
- [ ] 保存后 paper 列表和详情页显示最新元数据。
- [ ] Tests pass.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-007: Parse PDF into passages

**Description:** 作为研究者，我希望系统把 PDF 解析成可引用的 passage，以便检索结果可以回到原文。

**Acceptance Criteria:**
- [ ] 使用 PyMuPDF 或等价本地解析方式提取 PDF 页文本。
- [ ] 按页、章节线索和段落切分生成 passages。
- [ ] Passage 保存 paper id、space id、章节、页码、段落序号、原文文本、解析置信度和 passage 类型。
- [ ] 解析失败时保留 paper 记录和错误状态，用户可以重试。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-008: View papers and passages

**Description:** 作为研究者，我希望查看论文详情和 passage 原文，以便核对解析结果。

**Acceptance Criteria:**
- [ ] UI 显示当前 space 的论文列表和论文详情页。
- [ ] 论文详情页显示元数据、解析状态和 passages。
- [ ] Passage 显示章节、页码、类型和原文文本。
- [ ] 用户可以从 passage 回到对应 paper。
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-009: Build full-text search with SQLite FTS5

**Description:** 作为研究者，我希望精确搜索方法名、数据集名、指标名和实验条件，以便快速找到相关论文片段。

**Acceptance Criteria:**
- [ ] 为 passages 建立 SQLite FTS5 索引。
- [ ] Paper 解析完成后自动写入或更新 FTS 索引。
- [ ] 搜索 API 限定在 active space 或指定 space 内。
- [ ] 搜索结果包含 paper、章节、页码、原文片段和匹配分数。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-010: Expose literature search in the UI

**Description:** 作为研究者，我希望在 UI 中搜索当前 idea space 的论文内容，以便人工检查和定位文献证据。

**Acceptance Criteria:**
- [ ] UI 提供当前 space 的搜索输入。
- [ ] 搜索结果显示 paper 标题、章节、页码、片段和匹配提示。
- [ ] 搜索结果可以打开对应 paper 和 passage。
- [ ] 搜索空状态和无结果状态清晰可见。
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-011: Create and edit knowledge cards

**Description:** 作为研究者，我希望系统保存结构化 knowledge cards，以便 agent 能检索方法、结果、限制和证据。

**Acceptance Criteria:**
- [ ] Knowledge card 支持 Problem、Claim、Evidence、Method、Object、Variable、Metric、Result、Failure Mode、Interpretation、Limitation 和 Practical Tip 类型。
- [ ] 每张 card 保存 space id、paper id、source passage id、摘要、置信度和用户编辑标记。
- [ ] UI 支持查看、创建、编辑和删除 card。
- [ ] 用户能从 card 打开来源 passage。
- [ ] Tests pass.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-012: Extract initial knowledge cards from passages

**Description:** 作为研究者，我希望系统从 passage 中生成初始 cards，以便减少手工整理负担。

**Acceptance Criteria:**
- [ ] 提供 card extraction job，按 paper 或 space 触发。
- [ ] 在没有外部 LLM 配置时，使用可测试的本地规则抽取方法、结果、限制和指标候选 cards。
- [ ] 每张自动生成 card 必须绑定 source passage，并保存置信度和抽取来源。
- [ ] 抽取失败不影响全文检索和 passage 查看。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-013: Provide structured literature views

**Description:** 作为研究者，我希望按方法、指标、结果、失败模式和局限性浏览当前 space，以便快速理解文献结构。

**Acceptance Criteria:**
- [ ] UI 提供 Methods、Metrics、Results、Failure Modes、Limitations 和 Claims 视图。
- [ ] 每个视图只显示当前 active space 的 cards。
- [ ] 每个 card 显示来源 paper、页码和可打开的 passage 链接。
- [ ] 用户可以按 paper、card 类型、标签或与 idea 的关系过滤。
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

### US-014: Implement core MCP server tools

**Description:** 作为外部 agent，我希望通过本地 MCP Server 查询当前 idea space 的论文知识，以便在分析问题时获得文献上下文。

**Acceptance Criteria:**
- [ ] 提供本地 MCP Server 启动入口。
- [ ] 实现 list_spaces、get_active_space、list_papers、search_literature、get_paper_summary 和 get_citation 工具。
- [ ] 所有工具默认限定在 active space，除非显式传入可访问的 space id。
- [ ] search_literature 返回来源 paper、章节、页码、passage 文本和 card 信息。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-015: Implement specialized MCP evidence tools

**Description:** 作为外部 agent，我希望按科研任务查询方法、指标、局限性、失败模式、类似结果和 claim 证据，以便把用户问题映射到文献依据。

**Acceptance Criteria:**
- [ ] 实现 get_methods、get_metrics、get_limitations、find_failure_modes、find_similar_results、compare_with_literature 和 get_evidence_for_claim 工具。
- [ ] 每个工具返回结构化结果和可追溯来源，不生成最终科研结论。
- [ ] 工具结果明确包含 paper id、paper title、card id 或 passage id、页码和原文片段。
- [ ] 查询只返回当前 active space 的内容。
- [ ] Tests pass.
- [ ] Typecheck passes.

### US-016: Add agent access controls and reliability checks

**Description:** 作为研究者，我希望清楚知道 agent 当前访问哪个 space，并确保检索不会跨项目污染。

**Acceptance Criteria:**
- [ ] UI 提供启用或禁用本地 agent 接入的状态控制。
- [ ] UI 显示当前 MCP 访问的 active space 和本地连接信息。
- [ ] 后端和 MCP 查询都通过测试证明不会返回其他 space 的 papers、passages 或 cards。
- [ ] 所有 agent 可见结果都包含来源信息；缺少来源的结果不得返回。
- [ ] Tests pass.
- [ ] Typecheck passes.
- [ ] Verify in browser using dev-browser skill.

## 5. Functional Requirements

- FR-1: 系统必须允许用户创建、打开、重命名、归档和删除 idea space。
- FR-2: 系统必须为每个 space 隔离 papers、passages、cards、notes 和索引。
- FR-3: 系统必须允许用户导入 PDF 并保存原始文件。
- FR-4: 系统必须允许用户查看和编辑 paper metadata。
- FR-5: 系统必须将 PDF 解析成 passage，并为每个 passage 保存来源。
- FR-6: 系统必须为 passage 建立全文索引。
- FR-7: 系统必须允许用户搜索当前 active space 的文献内容。
- FR-8: 系统必须允许用户查看、创建、编辑和删除 knowledge cards。
- FR-9: 系统必须提供按 card 类型组织的结构化文献视图。
- FR-10: 系统必须提供本地 MCP Server，让外部 agent 查询当前 active space。
- FR-11: MCP 工具返回内容必须包含具体 paper、section、page 和原文片段。
- FR-12: MCP 工具不得默认跨 space 查询。

## 6. Design Considerations

- 产品第一屏应是实际工作区，而不是营销 landing page。
- UI 应面向重复使用和扫描，优先清晰、密集、有组织的信息结构。
- Space 切换、active space 状态和 agent 接入状态必须明显，避免用户误把一个研究项目的文献用于另一个项目。
- 搜索结果和 card 必须把来源信息放在显眼位置。
- 空状态要引导用户创建 space、导入 PDF 或选择 active space。

## 7. Technical Considerations

- 本地数据目录建议使用 `app-data/spaces/<space-id>/` 结构保存 space 文件和 PDF。
- SQLite 保存全局 space 列表、paper metadata、passages、cards 和状态。
- SQLite FTS5 是第一版检索核心。
- PyMuPDF 是第一版 PDF 文本解析默认方案。
- Knowledge card extraction 必须有无外部 API 时可测试的本地 fallback。
- MCP Server 工具应该和后端服务共享相同的数据访问层，避免 space 隔离逻辑分叉。

## 8. Success Metrics

- 用户可以创建一个 idea space，并导入至少一篇 PDF。
- 用户可以在 UI 中看到 paper metadata、passages 和初始 cards。
- 用户可以搜索到论文中的方法、指标、结果、局限性或失败模式。
- 外部 agent 可以通过 MCP 查询当前 active space 的文献知识。
- MCP 返回的每条证据都能追溯到 paper、page 和 passage。
- 多个 space 同时存在时，检索和 MCP 查询不会返回其他 space 的内容。

## 9. Open Questions

- 第一版是否需要联网元数据补全，还是只保留 DOI/arXiv/PubMed/BibTeX 字段供用户手动维护。
- 第一版是否需要向量检索，还是在 FTS5 稳定后作为第二阶段实现。
- Knowledge card 自动抽取是否接入用户自带 LLM API，还是先使用本地规则和手动修正流程。
