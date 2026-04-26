import { Edit2, FileText, FolderOpen, MoreVertical, Plus, RotateCcw, Search, ShieldCheck, Trash2, UploadCloud, Zap } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

const cardTabs = ['Method', 'Metric', 'Result', 'Failure Mode', 'Limitation', 'Claim'] as const;

function cardLabel(type: string): string {
  const labels: Record<string, string> = {
    Method: '方法',
    Metric: '指标',
    Result: '结果',
    'Failure Mode': '失败模式',
    Limitation: '局限性',
    Claim: '主张',
    Evidence: '证据',
    Problem: '问题',
    Object: '研究对象',
    Variable: '变量',
    Interpretation: '解释',
    'Practical Tip': '实践建议',
  };
  return labels[type] || type;
}

function parseLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待解析',
    parsing: '解析中',
    parsed: '已解析',
    error: '解析失败',
  };
  return labels[status] || status;
}

type InitialLoadStatus = 'loading' | 'ready' | 'error';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export default function App(): JSX.Element {
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [activeSpace, setActiveSpace] = useState<Space | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
  const [passages, setPassages] = useState<Passage[]>([]);
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [activeTab, setActiveTab] = useState<(typeof cardTabs)[number]>('Method');
  const [activeView, setActiveView] = useState<'library' | 'search'>('library');
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  
  // 空间模态框状态
  const [isSpaceModalOpen, setIsSpaceModalOpen] = useState(false);
  const [editingSpace, setEditingSpace] = useState<Space | null>(null);
  const [newSpaceName, setNewSpaceName] = useState('');
  const [newSpaceDescription, setNewSpaceDescription] = useState('');
  
  // 删除确认模态框状态
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);
  const [spaceToDelete, setSpaceToDelete] = useState<string | null>(null);
  
  const [notice, setNotice] = useState('');
  const [initialLoadStatus, setInitialLoadStatus] = useState<InitialLoadStatus>('loading');
  const [initialLoadError, setInitialLoadError] = useState('');

  const visibleCards = useMemo(
    () => cards.filter((card) => card.card_type === activeTab),
    [cards, activeTab],
  );

  async function refresh(options: { initial?: boolean } = {}): Promise<void> {
    if (options.initial) {
      setInitialLoadStatus('loading');
      setInitialLoadError('');
    }

    try {
      const loadedSpaces = await api.listSpaces();
      setSpaces(loadedSpaces);

      let active: Space | null = null;
      try {
        active = await api.getActiveSpace();
      } catch {
        active = loadedSpaces[0] || null;
        if (active) {
          await api.setActiveSpace(active.id);
        }
      }
      setActiveSpace(active);

      const [loadedPapers, status]: [Paper[], AgentStatus] = active
        ? await Promise.all([
            api.listPapers(),
            api.agentStatus(),
          ])
        : [[], await api.agentStatus()];
      setPapers(loadedPapers);
      setAgentStatus(status);
      if (options.initial) {
        setInitialLoadStatus('ready');
      }
    } catch (err) {
      console.error('刷新数据失败:', err);
      if (options.initial) {
        setInitialLoadError(errorMessage(err));
        setInitialLoadStatus('error');
      }
    }
  }

  async function openPaper(paper: Paper): Promise<void> {
    setSelectedPaper(paper);
    const [paperPassages, paperCards] = await Promise.all([
      api.listPassages(paper.id),
      api.listCards(paper.id),
    ]);
    setPassages(paperPassages);
    setCards(paperCards);
  }

  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(''), 3000);
      return () => clearTimeout(timer);
    }
  }, [notice]);

  useEffect(() => {
    void refresh({ initial: true });
  }, []);

  // 空间 CRUD 逻辑
  function openCreateModal(): void {
    setEditingSpace(null);
    setNewSpaceName('');
    setNewSpaceDescription('');
    setIsSpaceModalOpen(true);
  }

  function openEditModal(e: React.MouseEvent, space: Space): void {
    e.stopPropagation();
    setEditingSpace(space);
    setNewSpaceName(space.name);
    setNewSpaceDescription(space.description || '');
    setIsSpaceModalOpen(true);
  }

  async function saveSpace(): Promise<void> {
    if (!newSpaceName.trim()) {
      setNotice('请输入空间名称。');
      return;
    }
    
    if (editingSpace) {
      await api.updateSpace(editingSpace.id, newSpaceName.trim(), newSpaceDescription.trim());
      setNotice('空间已更新。');
    } else {
      const space = await api.createSpace(newSpaceName.trim(), newSpaceDescription.trim());
      await api.setActiveSpace(space.id);
      setNotice('已创建并打开空间。');
    }
    
    setEditingSpace(null);
    setNewSpaceName('');
    setNewSpaceDescription('');
    setIsSpaceModalOpen(false);
    await refresh();
  }

  async function confirmDelete(): Promise<void> {
    if (!spaceToDelete) return;
    await api.deleteSpace(spaceToDelete);
    if (activeSpace?.id === spaceToDelete) {
      setActiveSpace(null);
      setSelectedPaper(null);
    }
    setNotice('空间已删除。');
    setIsDeleteConfirmOpen(false);
    setSpaceToDelete(null);
    await refresh();
  }

  function openDeleteConfirm(e: React.MouseEvent, spaceId: string): void {
    e.stopPropagation();
    setSpaceToDelete(spaceId);
    setIsDeleteConfirmOpen(true);
  }

  async function setActive(space: Space): Promise<void> {
    await api.setActiveSpace(space.id);
    setSelectedPaper(null);
    setPassages([]);
    setCards([]);
    await refresh();
  }

  async function upload(file: File): Promise<void> {
    await api.uploadPaper(file);
    setNotice('论文已导入。');
    await refresh();
  }

  async function runSearch(): Promise<void> {
    if (!query.trim()) return;
    const searchResults = await api.search(query.trim());
    setResults(searchResults);
    setActiveView('search');
  }

  async function parseSelected(): Promise<void> {
    if (!selectedPaper) return;
    const parsed = await api.parsePaper(selectedPaper.id);
    setNotice(`解析完成：${parsed.passage_count} 个片段。`);
    const updated = await api.getPaper(selectedPaper.id);
    await openPaper(updated);
    await refresh();
  }

  async function extractSelected(): Promise<void> {
    if (!selectedPaper) return;
    const extracted = await api.extractCards(selectedPaper.id);
    setNotice(extracted.message || `启发式抽取完成：${extracted.card_count} 张卡片。`);
    const paperCards = await api.listCards(selectedPaper.id);
    setCards(paperCards);
  }

  async function toggleAgent(): Promise<void> {
    const enabled = agentStatus ? !agentStatus.enabled : true;
    await api.setAgentStatus(enabled);
    setAgentStatus(await api.agentStatus());
  }

  if (initialLoadStatus !== 'ready') {
    return (
      <main className="startup-shell">
        <div className="startup-panel">
          <div className="brand-mark startup-mark">P</div>
          <h1>论文知识引擎</h1>
          {initialLoadStatus === 'loading' ? (
            <>
              <div className="startup-spinner" />
              <p>正在启动论文知识引擎</p>
            </>
          ) : (
            <>
              <p>启动失败：{initialLoadError || '无法连接本地 API'}</p>
              <button className="btn-secondary" onClick={() => void refresh({ initial: true })}>
                <RotateCcw size={14} />
                重试
              </button>
            </>
          )}
        </div>
      </main>
    );
  }

  return (
    <>
      <main className="app-shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark">P</div>
            <div>
              <h1>论文知识引擎</h1>
              <p>Idea Space 工作台</p>
            </div>
          </div>

          <div className="sidebar-actions">
            <button className="btn-new-space" onClick={openCreateModal}>
              <Plus size={18} />
              <span>新建空间</span>
            </button>
          </div>

          <nav className="space-list">
            {spaces.map((space) => (
              <div
                key={space.id}
                className={space.id === activeSpace?.id ? 'space-item-wrapper active' : 'space-item-wrapper'}
                onClick={() => void setActive(space)}
              >
                <div className="space-item-main">
                  <FolderOpen size={16} />
                  <span>{space.name}</span>
                </div>
                <div className="space-item-actions">
                  <button onClick={(e) => openEditModal(e, space)} title="编辑空间">
                    <Edit2 size={12} />
                  </button>
                  <button onClick={(e) => openDeleteConfirm(e, space.id)} title="删除空间">
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </nav>
        </aside>

        <section className="workspace">
          <header className="topbar">
            <div>
              <p className="eyebrow">当前工作空间</p>
              <h2>{activeSpace?.name || '未选择空间'}</h2>
            </div>
            <div className="topbar-actions">
              <button 
                className={agentStatus?.enabled ? 'status enabled' : 'status'} 
                onClick={() => void toggleAgent()}
                disabled={!activeSpace}
              >
                <Zap size={14} fill={agentStatus?.enabled ? 'currentColor' : 'none'} />
                {agentStatus?.enabled ? '智能代理已启用' : '智能代理已禁用'}
              </button>
            </div>
          </header>

          {activeSpace ? (
            <>
              <div className="workspace-nav">
                <button 
                  className={activeView === 'library' ? 'nav-item active' : 'nav-item'} 
                  onClick={() => setActiveView('library')}
                >
                  资源库
                </button>
                <button 
                  className={activeView === 'search' ? 'nav-item active' : 'nav-item'} 
                  onClick={() => setActiveView('search')}
                >
                  深度检索
                </button>
              </div>

              {activeView === 'library' ? (
                <div className="view-container library-view">
                  <div className="view-header">
                    <div className="view-title">
                      <h3>我的论文</h3>
                      <span className="badge">{papers.length}</span>
                    </div>
                    <label className="btn-upload">
                      <UploadCloud size={16} />
                      <span>导入 PDF</span>
                      <input type="file" accept="application/pdf,.pdf" onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} />
                    </label>
                  </div>

                  <div className="paper-grid">
                    {papers.length > 0 ? (
                      papers.map((paper) => (
                        <button key={paper.id} className={selectedPaper?.id === paper.id ? 'paper-card active' : 'paper-card'} onClick={() => void openPaper(paper)}>
                          <div className="paper-card-icon">
                            <FileText size={24} />
                          </div>
                          <div className="paper-card-content">
                            <strong>{paper.title || '未命名论文'}</strong>
                            <div className="paper-card-meta">
                              <span>{paper.authors || '作者未知'}</span>
                              <span className="dot">·</span>
                              <span>{parseLabel(paper.parse_status)}</span>
                            </div>
                          </div>
                        </button>
                      ))
                    ) : (
                      <div className="empty-state">
                        <p>该空间下暂无论文，请先导入。</p>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="view-container search-view">
                  <div className="search-interface">
                    <div className="search-input-wrapper">
                      <Search size={20} className="text-tertiary" />
                      <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && void runSearch()} placeholder="搜索方法、指标、结果或局限性..." autoFocus />
                      <button className="btn-primary" onClick={() => void runSearch()}>开始检索</button>
                    </div>
                  </div>

                  <div className="search-results-list">
                    <div className="results-info">
                      检索到 {results.length} 条相关片段
                    </div>
                    {results.length > 0 ? (
                      results.map((result) => (
                        <article key={result.passage_id} className="search-result-card">
                          <div className="result-source">
                            <FileText size={14} />
                            <span>{result.paper_title || result.paper_id}</span>
                            <span className="dot">·</span>
                            <span>第 {result.page_number} 页</span>
                          </div>
                          <p dangerouslySetInnerHTML={{ __html: result.snippet }} />
                          <div className="result-footer">
                            {result.section || '正文章节'}
                          </div>
                        </article>
                      ))
                    ) : (
                      <div className="empty-state">
                        <p>输入关键词并检索以查看结果。</p>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (

            <div className="empty-state" style={{ marginTop: '10%', textAlign: 'center' }}>
              <FolderOpen size={64} style={{ marginBottom: '24px', opacity: 0.1 }} />
              <h3 style={{ marginBottom: '8px', color: 'var(--text-secondary)' }}>开启您的研究之旅</h3>
              <p style={{ maxWidth: '300px', margin: '0 auto', color: 'var(--text-tertiary)' }}>
                请在左侧选择一个现有的研究空间，或者点击“新建空间”开始管理您的文献。
              </p>
            </div>
          )}
        </section>

        <aside className={isInspectorOpen ? 'inspector' : 'inspector collapsed'}>
          <button className="inspector-toggle" onClick={() => setIsInspectorOpen(!isInspectorOpen)}>
            {isInspectorOpen ? '→' : '←'}
          </button>

          {isInspectorOpen && (
            <div className="inspector-content">
              {!activeSpace ? (
                <div className="empty-state" style={{ marginTop: '40%' }}>
                  <p>请先激活一个研究空间</p>
                </div>
              ) : selectedPaper ? (
                <>
                  <div className="inspector-header">
                    <div className="paper-status-tag">{parseLabel(selectedPaper.parse_status)}</div>
                    <h2>{selectedPaper.title || '未命名论文'}</h2>
                    <p className="paper-authors">{selectedPaper.authors || '作者未知'}</p>
                  </div>

                  <div className="ai-supercharge">
                    <button className="btn-ai-extract" onClick={() => void extractSelected()}>
                      <Zap size={16} fill="white" />
                      <span>AI 深度抽取知识卡片</span>
                    </button>
                    <button className="btn-text-only" onClick={() => void parseSelected()}>
                      重新解析原文
                    </button>
                  </div>

                  <div className="inspector-section">
                    <div className="section-title">核心元数据</div>
                    <div className="meta-pills">
                      <div className="meta-pill">
                        <label>出版年份</label>
                        <span>{selectedPaper.year || '未知'}</span>
                      </div>
                      <div className="meta-pill">
                        <label>研究关系</label>
                        <span>{selectedPaper.relation_to_idea || '未定义'}</span>
                      </div>
                    </div>
                  </div>

                  <div className="inspector-section">
                    <div className="section-title">知识图谱卡片</div>
                    <div className="tabs-container">
                      <div className="tabs">
                        {cardTabs.map((tab) => (
                          <button key={tab} className={tab === activeTab ? `tab-btn active ${tab.toLowerCase().replace(' ', '-')}` : 'tab-btn'} onClick={() => setActiveTab(tab)}>
                            {cardLabel(tab)}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="card-list">
                      {visibleCards.length > 0 ? (
                        visibleCards.map((card) => (
                          <article key={card.id} className={`knowledge-card-fancy ${card.card_type.toLowerCase().replace(' ', '-')}`}>
                            <div className="card-type-indicator">{cardLabel(card.card_type)}</div>
                            <p>{card.summary}</p>
                            <div className="card-footer">
                              <span>置信度 {(card.confidence * 100).toFixed(0)}%</span>
                              {card.source_passage_id && <span className="ai-badge">AI 提取</span>}
                            </div>
                          </article>
                        ))
                      ) : (
                        <div className="empty-state-small">
                          <p>暂无此类知识点，尝试“AI 深度抽取”</p>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="inspector-section">
                    <div className="section-title">关键原文片段</div>
                    <div className="passage-previews">
                      {passages.slice(0, 3).map((passage) => (
                        <div key={passage.id} className="passage-card-mini">
                          {passage.original_text}
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              ) : (
                <div className="empty-state" style={{ marginTop: '40%' }}>
                  <FileText size={48} style={{ marginBottom: '16px', opacity: 0.1 }} />
                  <h3>准备就绪</h3>
                  <p>在左侧选择一篇论文<br/>开始深度分析</p>
                </div>
              )}
            </div>
          )}
        </aside>

      </main>

      {isSpaceModalOpen && (
        <div className="modal-overlay" onClick={() => setIsSpaceModalOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{editingSpace ? '编辑研究空间' : '新建研究空间'}</h2>
            <div className="form-group">
              <label>空间名称</label>
              <input 
                value={newSpaceName} 
                onChange={(e) => setNewSpaceName(e.target.value)} 
                placeholder="例如：大模型推理优化" 
                autoFocus
              />
            </div>
            <div className="form-group">
              <label>空间描述</label>
              <textarea 
                value={newSpaceDescription} 
                onChange={(e) => setNewSpaceDescription(e.target.value)} 
                placeholder="描述此空间的研究目标、关注点或特定的研究假设..." 
                rows={4}
              />
            </div>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setIsSpaceModalOpen(false)}>取消</button>
              <button className="btn-primary" onClick={() => void saveSpace()}>
                {editingSpace ? '保存修改' : '创建并进入'}
              </button>
            </div>
          </div>
        </div>
      )}
      {isDeleteConfirmOpen && (
        <div className="modal-overlay" onClick={() => setIsDeleteConfirmOpen(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>确认删除空间？</h2>
            <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', lineHeight: '1.6' }}>
              此操作将使该空间及其关联的论文在列表中不可见。虽然数据仍保留在数据库中，但在当前界面下将无法访问。
            </p>
            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setIsDeleteConfirmOpen(false)}>取消</button>
              <button className="btn-danger" onClick={() => void confirmDelete()}>确定删除</button>
            </div>
          </div>
        </div>
      )}

      {notice && (
        <div className="notice" onClick={() => setNotice('')}>
          {notice}
        </div>
      )}
    </>
  );
}
