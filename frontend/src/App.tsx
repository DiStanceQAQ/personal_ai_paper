import { useEffect, useMemo, useState } from 'react';
import { api } from './api';
import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

// Layout Components
import { Sidebar } from './components/layout/Sidebar';
import { Workspace } from './components/layout/Workspace';
import { Inspector } from './components/layout/Inspector';

// UI Components
import { LoadingOverlay } from './components/ui/LoadingOverlay';
import { Toast } from './components/ui/Toast';

// Modals
import { SettingsModal } from './components/modals/SettingsModal';
import { SpaceModal } from './components/modals/SpaceModal';
import { ConfirmModal } from './components/modals/ConfirmModal';
import { MCPGuideModal } from './components/modals/MCPGuideModal';

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

export default function App(): JSX.Element {
  // --- State ---
  const [isAppReady, setIsAppReady] = useState(false);
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
  const [isProcessing, setIsProcessing] = useState(false);
  
  // Modals
  const [isSpaceModalOpen, setIsSpaceModalOpen] = useState(false);
  const [editingSpace, setEditingSpace] = useState<Space | null>(null);
  const [newSpaceName, setNewSpaceName] = useState('');
  const [newSpaceDescription, setNewSpaceDescription] = useState('');
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);
  const [spaceToDelete, setSpaceToDelete] = useState<string | null>(null);
  const [isPaperDeleteConfirmOpen, setIsPaperDeleteConfirmOpen] = useState(false);
  const [paperToDelete, setPaperToDelete] = useState<string | null>(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isMCPGuideOpen, setIsMCPGuideOpen] = useState(false);
  const [projectRoot, setProjectRoot] = useState<string>('');

  const [llmConfig, setLlmConfig] = useState({
    llm_provider: 'openai',
    llm_base_url: 'https://api.openai.com/v1',
    llm_model: 'gpt-4o',
    llm_api_key: '',
    has_api_key: false
  });
  
  const [notice, setNotice] = useState<{ message: string, type: 'success' | 'error' } | null>(null);

  // --- Derived State ---
  const visibleCards = useMemo(
    () => cards.filter((card) => card.card_type === activeTab),
    [cards, activeTab],
  );

  // --- Effects ---
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(null), 3500);
      return () => clearTimeout(timer);
    }
  }, [notice]);

  useEffect(() => {
    async function init() {
      let retryCount = 0;
      while (retryCount < 20) {
        try {
          await api.health();
          break; 
        } catch {
          await new Promise(r => setTimeout(r, 500));
          retryCount++;
        }
      }
      await Promise.all([refresh(), loadLlmConfig(), loadAppInfo()]);
      setIsAppReady(true);
    }
    init();
  }, []);

  // --- Actions ---
  async function loadAppInfo(): Promise<void> {
    try {
      const info = await api.getAppInfo();
      setProjectRoot(info.project_root);
    } catch {
      console.warn('无法获取应用信息。');
    }
  }

  async function refresh(): Promise<void> {
    try {
      const loadedSpaces = await api.listSpaces();
      setSpaces(loadedSpaces);
      try {
        const active = await api.getActiveSpace();
        setActiveSpace(active);
        const [loadedPapers, status] = await Promise.all([
          api.listPapers(),
          api.agentStatus(),
        ]);
        setPapers(loadedPapers);
        setAgentStatus(status);
      } catch {
        setActiveSpace(null);
      }
    } catch (err) {
      console.error('连接后端失败:', err);
      setNotice({ message: '无法连接到后端 Sidecar，请检查应用状态。', type: 'error' });
    }
  }

  async function openPaper(paper: Paper): Promise<void> {
    setSelectedPaper(paper);
    try {
      const [paperPassages, paperCards] = await Promise.all([
        api.listPassages(paper.id),
        api.listCards(paper.id),
      ]);
      setPassages(paperPassages);
      setCards(paperCards);
    } catch {
      setNotice({ message: '获取论文详情失败。', type: 'error' });
    }
  }

  async function loadLlmConfig(): Promise<void> {
    try {
      const config = await api.getAgentConfig();
      setLlmConfig({ ...config, llm_api_key: '' });
    } catch {
      console.warn('无法加载 LLM 配置。');
    }
  }

  async function saveLlmConfig(): Promise<void> {
    try {
      await api.updateAgentConfig(llmConfig);
      setNotice({ message: 'LLM 配置保存成功。', type: 'success' });
      setIsSettingsOpen(false);
      await loadLlmConfig();
    } catch {
      setNotice({ message: '保存配置失败。', type: 'error' });
    }
  }

  async function copyToClipboard(text: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setNotice({ message: '配置已复制到剪贴板。', type: 'success' });
    } catch {
      setNotice({ message: '复制失败。', type: 'error' });
    }
  }

  async function saveSpace(): Promise<void> {
    if (!newSpaceName.trim()) {
      setNotice({ message: '请输入空间名称。', type: 'error' });
      return;
    }
    try {
      if (editingSpace) {
        await api.updateSpace(editingSpace.id, newSpaceName.trim(), newSpaceDescription.trim());
        setNotice({ message: '空间更新成功。', type: 'success' });
      } else {
        const space = await api.createSpace(newSpaceName.trim(), newSpaceDescription.trim());
        await api.setActiveSpace(space.id);
        setNotice({ message: '新空间已创建并激活。', type: 'success' });
      }
      setIsSpaceModalOpen(false);
      await refresh();
    } catch {
      setNotice({ message: '操作空间失败。', type: 'error' });
    }
  }

  async function confirmDelete(): Promise<void> {
    if (!spaceToDelete) return;
    try {
      await api.deleteSpace(spaceToDelete);
      if (activeSpace?.id === spaceToDelete) {
        setActiveSpace(null);
        setSelectedPaper(null);
      }
      setNotice({ message: '研究空间已删除。', type: 'success' });
    } catch {
      setNotice({ message: '删除空间失败。', type: 'error' });
    } finally {
      setIsDeleteConfirmOpen(false);
      await refresh();
    }
  }

  async function confirmPaperDelete(): Promise<void> {
    if (!paperToDelete) return;
    try {
      await api.deletePaper(paperToDelete);
      if (selectedPaper?.id === paperToDelete) {
        setSelectedPaper(null);
        setPassages([]);
        setCards([]);
      }
      setNotice({ message: '论文已从库中移除。', type: 'success' });
    } catch {
      setNotice({ message: '移除论文失败。', type: 'error' });
    } finally {
      setIsPaperDeleteConfirmOpen(false);
      setPaperToDelete(null);
      await refresh();
    }
  }

  async function setActive(space: Space): Promise<void> {
    if (activeSpace?.id === space.id) return;
    try {
      await api.setActiveSpace(space.id);
      setSelectedPaper(null);
      setPassages([]);
      setCards([]);
      await refresh();
    } catch {
      setNotice({ message: '切换空间失败。', type: 'error' });
    }
  }

  async function upload(file: File): Promise<void> {
    setIsProcessing(true);
    setNotice({ message: '正在导入并预处理 PDF 文件...', type: 'success' });
    try {
      await api.uploadPaper(file);
      setNotice({ message: '导入成功。', type: 'success' });
      await refresh();
    } catch (err: any) {
      setNotice({ message: err.message || '文件导入失败。', type: 'error' });
    } finally {
      setIsProcessing(false);
    }
  }

  async function runSearch(): Promise<void> {
    if (!query.trim()) return;
    try {
      const searchResults = await api.search(query.trim());
      setResults(searchResults);
      setActiveView('search');
    } catch {
      setNotice({ message: '搜索请求失败。', type: 'error' });
    }
  }

  async function extractSelected(): Promise<void> {
    if (!selectedPaper) return;
    const config = await api.getAgentConfig();
    if (!config.has_api_key && config.llm_provider !== 'ollama') {
      setNotice({ message: '请先在左下角完成 LLM 配置（API Key）后再执行解析。', type: 'error' });
      setIsSettingsOpen(true);
      return;
    }
    setIsProcessing(true);
    try {
      setNotice({ message: '正在进行 PDF 物理切片和 RAG 预处理...', type: 'success' });
      await api.parsePaper(selectedPaper.id);
      setNotice({ message: '正在调用内置 Agent 进行深度语义分析...', type: 'success' });
      const result = await api.runDeepAnalysis(selectedPaper.id);
      setNotice({ message: `AI 解析成功！识别了元数据并提取了 ${result.card_count} 张卡片。`, type: 'success' });
      const [updatedPaper, paperCards] = await Promise.all([
        api.getPaper(selectedPaper.id),
        api.listCards(selectedPaper.id),
      ]);
      setSelectedPaper(updatedPaper);
      setCards(paperCards);
      await refresh();
    } catch (err: any) {
      setNotice({ message: `AI 解析失败: ${err.message || '请检查模型配置'}`, type: 'error' });
    } finally {
      setIsProcessing(false);
    }
  }

  async function toggleAgent(): Promise<void> {
    try {
      const enabled = agentStatus ? !agentStatus.enabled : true;
      await api.setAgentStatus(enabled);
      const newStatus = await api.agentStatus();
      setAgentStatus(newStatus);
    } catch {
      setNotice({ message: 'MCP状态切换失败。', type: 'error' });
    }
  }

  if (!isAppReady) {
    return (
      <div style={{ height: '100vh', display: 'grid', placeItems: 'center', background: 'var(--bg-main)' }}>
        <div style={{ textAlign: 'center' }}>
          <div className="spinner" style={{ margin: '0 auto 20px auto' }}></div>
          <p style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>正在拉起本地 AI 引擎...</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <main className="app-shell">
        <Sidebar
          spaces={spaces}
          activeSpace={activeSpace}
          onSelectSpace={setActive}
          onOpenCreateModal={() => { setEditingSpace(null); setNewSpaceName(''); setNewSpaceDescription(''); setIsSpaceModalOpen(true); }}
          onOpenEditModal={(e, space) => { e.stopPropagation(); setEditingSpace(space); setNewSpaceName(space.name); setNewSpaceDescription(space.description || ''); setIsSpaceModalOpen(true); }}
          onOpenDeleteConfirm={(e, id) => { e.stopPropagation(); setSpaceToDelete(id); setIsDeleteConfirmOpen(true); }}
          onOpenSettings={() => setIsSettingsOpen(true)}
        />

        <Workspace
          activeSpace={activeSpace}
          agentStatus={agentStatus}
          onToggleAgent={toggleAgent}
          onOpenMCPGuide={() => setIsMCPGuideOpen(true)}
          activeView={activeView}
          setActiveView={setActiveView}
          papers={papers}
          selectedPaper={selectedPaper}
          onSelectPaper={openPaper}
          onDeletePaper={(e, id) => { e.stopPropagation(); setPaperToDelete(id); setIsPaperDeleteConfirmOpen(true); }}
          onUpload={upload}
          query={query}
          setQuery={setQuery}
          onSearch={runSearch}
          results={results}
          parseLabel={parseLabel}
        />

        <Inspector
          isOpen={isInspectorOpen}
          onToggle={() => setIsInspectorOpen(!isInspectorOpen)}
          selectedPaper={selectedPaper}
          activeSpace={activeSpace}
          agentStatus={agentStatus}
          onToggleAgent={toggleAgent}
          onExtract={extractSelected}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          visibleCards={visibleCards}
          cardTabs={cardTabs}
          cardLabel={cardLabel}
          parseLabel={parseLabel}
        />
      </main>

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        onSave={saveLlmConfig}
        config={llmConfig}
        setConfig={setLlmConfig}
      />

      <SpaceModal
        isOpen={isSpaceModalOpen}
        onClose={() => setIsSpaceModalOpen(false)}
        onSave={saveSpace}
        isEditing={!!editingSpace}
        name={newSpaceName}
        setName={setNewSpaceName}
        description={newSpaceDescription}
        setDescription={setNewSpaceDescription}
      />

      <ConfirmModal
        isOpen={isDeleteConfirmOpen}
        title="确认删除空间？"
        message="此空间内所有的论文、解析结果和知识卡片将不再显示。数据仍保留在数据库中，但当前界面将无法访问。"
        onConfirm={confirmDelete}
        onCancel={() => setIsDeleteConfirmOpen(false)}
      />

      <ConfirmModal
        isOpen={isPaperDeleteConfirmOpen}
        title="确认从库中移除这篇论文？"
        message="该操作将删除该论文的所有物理分片、搜索索引和已提取的卡片，并删除磁盘上的 PDF 文件。"
        onConfirm={confirmPaperDelete}
        onCancel={() => setIsPaperDeleteConfirmOpen(false)}
      />

      <MCPGuideModal
        isOpen={isMCPGuideOpen}
        onClose={() => setIsMCPGuideOpen(false)}
        onCopy={copyToClipboard}
        projectPath={projectRoot}
      />

      <LoadingOverlay isVisible={isProcessing} message={notice?.message || ''} />

      <Toast 
        message={notice?.message || ''} 
        type={notice?.type || 'success'}
        isVisible={!!notice && !isProcessing} 
        onClose={() => setNotice(null)} 
      />
    </>
  );
}
