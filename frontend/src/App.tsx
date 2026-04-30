import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { EmbeddingStatus, Paper, SearchMode, SearchResult, SearchStatus } from './types';

// Layout Components
import { Sidebar } from './components/layout/Sidebar';
import { Workspace } from './components/layout/Workspace';
import { Inspector } from './components/layout/Inspector';

// UI Components
import { Toast } from './components/ui/Toast';

// Modals
import { ModalsContainer } from './components/modals/ModalsContainer';

// Hooks
import { useModals } from './hooks/useModals';
import { useSpaces } from './hooks/useSpaces';
import { usePapers } from './hooks/usePapers';
import { useLlmConfig } from './hooks/useLlmConfig';

const cardTabs = ['Method', 'Metric', 'Result', 'Failure Mode', 'Limitation', 'Claim'] as const;

function cardLabel(type: string): string {
  const labels: Record<string, string> = {
    Method: '方法', Metric: '指标', Result: '结果', 'Failure Mode': '失败模式',
    Limitation: '局限性', Claim: '主张', Evidence: '证据', Problem: '问题',
    Object: '研究对象', Variable: '变量', Interpretation: '解释', 'Practical Tip': '实践建议',
  };
  return labels[type] || type;
}

function parseLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: '待解析', parsing: '解析中', parsed: '已解析', error: '解析失败',
  };
  return labels[status] || status;
}

function embeddingLabel(status: EmbeddingStatus): string {
  const labels: Record<EmbeddingStatus, string> = {
    pending: '待索引',
    running: '索引中',
    completed: '已就绪',
    failed: '索引失败',
    skipped: '未索引',
  };
  return labels[status] || status;
}

export default function App(): JSX.Element {
  // --- Global UI State ---
  const [initialLoadStatus, setInitialLoadStatus] = useState<'starting' | 'ready'>('starting');
  const [isProcessing, setIsProcessing] = useState(false);
  const [notice, setNotice] = useState<{ message: string, type: 'success' | 'error' } | null>(null);
  const [projectRoot, setProjectRoot] = useState<string>('');

  // --- View State ---
  const [activeView, setActiveView] = useState<'library' | 'search'>('library');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isInspectorOpen, setIsInspectorOpen] = useState(true);
  const [activeTab, setActiveTab] = useState<(typeof cardTabs)[number]>('Method');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [searchStatus, setSearchStatus] = useState<SearchStatus>('idle');
  const [searchError, setSearchError] = useState('');
  const [searchMode, setSearchMode] = useState<SearchMode>('fts');
  const [selectedSearchResult, setSelectedSearchResult] = useState<SearchResult | null>(null);

  // --- Custom Hooks (Logic Logic) ---
  const modals = useModals();
  const { spaces, activeSpace, loadSpaces, switchSpace, createOrUpdateSpace, deleteSpace } = useSpaces(setNotice);
  const {
    papers, selectedPaper, setSelectedPaper, passages, cards, setCards, agentStatus, setAgentStatus,
    loadPapers, openPaper, deletePaper: handleDeletePaper, uploadPaper, runDeepAnalysis,
    cancelAnalysisRun, backgroundTasks, uploadQueue, embeddingRunsByPaperId
  } = usePapers(activeSpace?.id, setNotice, setIsProcessing);
  const {
    llmConfig,
    setLlmConfig,
    loadLlmConfig,
    saveLlmConfig,
    mineruTestResult,
    testMineruConnection,
  } = useLlmConfig(setNotice);

  // --- Derived State ---
  const visibleCards = useMemo(() => cards.filter((card) => card.card_type === activeTab), [cards, activeTab]);
  const appShellClassName = [
    'app-shell',
    !isSidebarOpen ? 'sidebar-collapsed' : '',
    !isInspectorOpen ? 'inspector-collapsed' : '',
  ].filter(Boolean).join(' ');

  // --- Initial Mount ---
  useEffect(() => {
    async function init() {
      let retryCount = 0;
      while (retryCount < 20) {
        try { await api.health(); break; } catch { await new Promise(r => setTimeout(r, 500)); retryCount++; }
      }
      try {
        const info = await api.getAppInfo();
        setProjectRoot(info.project_root);
      } catch {}
      await Promise.all([loadSpaces(), loadLlmConfig()]);
      setInitialLoadStatus('ready');
    }
    init();
  }, [loadSpaces, loadLlmConfig]);

  useEffect(() => {
    if (activeSpace) loadPapers();
  }, [activeSpace, loadPapers]);

  useEffect(() => {
    setSelectedSearchResult(null);
  }, [activeSpace?.id]);

  useEffect(() => {
    setResults([]);
    setSearchError('');
    setSearchStatus('idle');
    setSelectedSearchResult(null);
  }, [searchMode]);

  // --- Common Handlers ---
  const handleToggleAgent = async () => {
    try {
      const enabled = agentStatus ? !agentStatus.enabled : true;
      await api.setAgentStatus(enabled);
      const newStatus = await api.agentStatus();
      setAgentStatus(newStatus);
    } catch { setNotice({ message: '智能代理状态切换失败。', type: 'error' }); }
  };

  const handleSearch = async () => {
    const trimmedQuery = query.trim();
    if (!trimmedQuery) {
      setResults([]);
      setSearchError('');
      setSearchStatus('idle');
      return;
    }

    setActiveView('search');
    setSearchStatus('loading');
    setSearchError('');
    setSelectedSearchResult(null);

    try {
      const searchResults = await api.search(trimmedQuery, searchMode);
      setResults(searchResults);
      setSearchStatus(searchResults.length > 0 ? 'success' : 'empty');
    } catch {
      setResults([]);
      setSearchError('检索请求失败，请检查本地服务后重试。');
      setSearchStatus('error');
      setNotice({ message: '搜索请求失败。', type: 'error' });
    }
  };

  const handleQueryChange = (nextQuery: string) => {
    setQuery(nextQuery);
    if (!nextQuery.trim()) {
      setResults([]);
      setSearchError('');
      setSearchStatus('idle');
      setSelectedSearchResult(null);
    }
  };

  const handleOpenSearchResult = async (result: SearchResult) => {
    const paper = papers.find((item) => item.id === result.paper_id);
    if (!paper) {
      setNotice({ message: '未在当前空间找到这篇论文。', type: 'error' });
      return;
    }

    setSelectedSearchResult(result);
    await openPaper(paper);
    setIsInspectorOpen(true);
    setNotice({ message: `已打开来源：第 ${result.page_number} 页。`, type: 'success' });
  };

  const handleUpdatePaper = async (paperId: string, data: Partial<Paper>) => {
    try {
      const updated = await api.updatePaper(paperId, data);
      setNotice({ message: '论文元数据已更新。', type: 'success' });
      setSelectedPaper(updated);
      modals.closeModal('editPaper');
      await loadPapers();
    } catch { setNotice({ message: '更新失败。', type: 'error' }); }
  };

  const handleDeleteCard = async (cardId: string) => {
    if (!selectedPaper) return;
    try {
      await api.deleteCard(selectedPaper.id, cardId);
      setNotice({ message: '卡片已移除。', type: 'success' });
      const paperCards = await api.listCards(selectedPaper.id);
      setCards(paperCards);
    } catch { setNotice({ message: '移除卡片失败。', type: 'error' }); }
  };

  const handleUpdateCard = async (cardId: string, summary: string) => {
    if (!selectedPaper) return;
    try {
      await api.updateCard(selectedPaper.id, cardId, { summary });
      const paperCards = await api.listCards(selectedPaper.id);
      setCards(paperCards);
    } catch {
      setNotice({ message: '更新卡片失败。', type: 'error' });
      throw new Error('Update failed');
    }
  };

  const handleAddManualCard = async (type: string, summary: string) => {
    if (!selectedPaper) return;
    try {
      await api.createCard(selectedPaper.id, { card_type: type, summary, confidence: 1.0 });
      setNotice({ message: '已手动添加知识卡片。', type: 'success' });
      const paperCards = await api.listCards(selectedPaper.id);
      setCards(paperCards);
    } catch { setNotice({ message: '添加卡片失败。', type: 'error' }); }
  };

  // --- Render Helpers ---
  if (initialLoadStatus !== 'ready') {
    return (
      <div style={{ height: '100vh', display: 'grid', placeItems: 'center', background: 'var(--bg-main)' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="spinner" style={{ margin: '0 auto 20px auto' }}></div>
          <p style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>正在启动论文知识引擎</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <main className={appShellClassName}>
        <Sidebar
          isOpen={isSidebarOpen}
          onToggle={() => setIsSidebarOpen((open) => !open)}
          spaces={spaces}
          activeSpace={activeSpace}
          onSelectSpace={switchSpace}
          onOpenCreateModal={() => modals.openModal('space')}
          onOpenEditModal={(e, space) => { e.stopPropagation(); modals.openModal('space', { editingSpace: space }); }}
          onOpenDeleteConfirm={(e, id) => { e.stopPropagation(); modals.openModal('deleteSpace', { spaceToDelete: id }); }}
          onOpenSettings={() => modals.openModal('settings')}
        />

        <Workspace
          activeSpace={activeSpace}
          agentStatus={agentStatus}
          onToggleAgent={handleToggleAgent}
          onOpenMCPGuide={() => modals.openModal('mcpGuide')}
          activeView={activeView}
          setActiveView={setActiveView}
          papers={papers}
          selectedPaper={selectedPaper}
          onSelectPaper={openPaper}
          onDeletePaper={(e, id) => { e.stopPropagation(); modals.openModal('deletePaper', { paperToDelete: id }); }}
          onUpload={uploadPaper}
          query={query}
          setQuery={handleQueryChange}
          searchMode={searchMode}
          setSearchMode={setSearchMode}
          onSearch={handleSearch}
          results={results}
          searchStatus={searchStatus}
          searchError={searchError}
          parseLabel={parseLabel}
          embeddingLabel={embeddingLabel}
          uploadQueue={uploadQueue}
          selectedSearchResult={selectedSearchResult}
          onOpenSearchResult={handleOpenSearchResult}
        />

        <Inspector
          isOpen={isInspectorOpen}
          onToggle={() => setIsInspectorOpen((open) => !open)}
          selectedPaper={selectedPaper}
          activeSpace={activeSpace}
          agentStatus={agentStatus}
          onToggleAgent={handleToggleAgent}
          onExtract={() => selectedPaper && runDeepAnalysis(selectedPaper.id)}
          onCancelAnalysis={(runId) => selectedPaper && cancelAnalysisRun(selectedPaper.id, runId)}
          onDeleteCard={handleDeleteCard}
          onUpdateCard={handleUpdateCard}
          onAddManualCard={handleAddManualCard}
          onOpenEditPaper={() => modals.openModal('editPaper')}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          visibleCards={visibleCards}
          selectedSearchResult={selectedSearchResult}
          analysisTask={selectedPaper ? backgroundTasks[selectedPaper.id] || null : null}
          embeddingRun={selectedPaper ? embeddingRunsByPaperId[selectedPaper.id] || null : null}
          cardTabs={cardTabs}
          cardLabel={cardLabel}
          parseLabel={parseLabel}
          embeddingLabel={embeddingLabel}
        />
      </main>

      <ModalsContainer
        modals={modals}
        llmConfig={llmConfig}
        setLlmConfig={setLlmConfig}
        saveLlmConfig={saveLlmConfig}
        mineruTestResult={mineruTestResult}
        testMineruConnection={testMineruConnection}
        createOrUpdateSpace={createOrUpdateSpace}
        deleteSpace={deleteSpace}
        handleUpdatePaper={handleUpdatePaper}
        handleDeletePaper={handleDeletePaper}
        selectedPaper={selectedPaper}
        projectRoot={projectRoot}
        setNotice={setNotice}
      />

      <Toast
        message={notice?.message || ''}
        type={notice?.type || 'success'}
        isVisible={!!notice}
        onClose={() => setNotice(null)}
      />
    </>
  );
}
