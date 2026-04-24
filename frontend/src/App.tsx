import { FileText, FolderOpen, Plug, Search, ShieldCheck, UploadCloud } from 'lucide-react';
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
  const [newSpaceName, setNewSpaceName] = useState('');
  const [newSpaceDescription, setNewSpaceDescription] = useState('');
  const [notice, setNotice] = useState('');

  const visibleCards = useMemo(
    () => cards.filter((card) => card.card_type === activeTab),
    [cards, activeTab],
  );

  async function refresh(): Promise<void> {
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
      setPapers([]);
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
    void refresh();
  }, []);

  async function createSpace(): Promise<void> {
    if (!newSpaceName.trim()) {
      setNotice('请输入空间名称。');
      return;
    }
    const space = await api.createSpace(newSpaceName.trim(), newSpaceDescription.trim());
    await api.setActiveSpace(space.id);
    setNewSpaceName('');
    setNewSpaceDescription('');
    setNotice('已创建并打开空间。');
    await refresh();
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

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">P</div>
          <div>
            <h1>本地论文知识引擎</h1>
            <p>Idea Space 文献工作台</p>
          </div>
        </div>

        <section className="new-space">
          <label>新空间</label>
          <input value={newSpaceName} onChange={(event) => setNewSpaceName(event.target.value)} placeholder="例如：小样本鲁棒性" />
          <textarea value={newSpaceDescription} onChange={(event) => setNewSpaceDescription(event.target.value)} placeholder="研究目标、假设或约束" />
          <button onClick={() => void createSpace()}>创建空间</button>
        </section>

        <nav className="space-list">
          {spaces.map((space) => (
            <button
              key={space.id}
              className={space.id === activeSpace?.id ? 'space-item active' : 'space-item'}
              onClick={() => void setActive(space)}
            >
              <FolderOpen size={16} />
              <span>{space.name}</span>
            </button>
          ))}
        </nav>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">当前空间</p>
            <h2>{activeSpace?.name || '未选择空间'}</h2>
          </div>
          <div className="topbar-actions">
            <button className={agentStatus?.enabled ? 'status enabled' : 'status'} onClick={() => void toggleAgent()}>
              <Plug size={16} />
              {agentStatus?.enabled ? '智能代理已启用' : '智能代理未启用'}
            </button>
          </div>
        </header>

        <section className="command-row">
          <label className="dropzone">
            <UploadCloud size={20} />
            <span>拖拽或选择 PDF</span>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => event.target.files?.[0] && void upload(event.target.files[0])} />
          </label>
          <div className="searchbox">
            <Search size={18} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && void runSearch()} placeholder="搜索方法、指标、结果或局限性" />
            <button onClick={() => void runSearch()}>检索</button>
          </div>
        </section>

        {notice ? <div className="notice">{notice}</div> : null}

        <section className="content-grid">
          <div className="panel papers-panel">
            <div className="panel-header">
              <h3>论文列表</h3>
              <span>{papers.length} 篇</span>
            </div>
            <div className="paper-list">
              {papers.map((paper) => (
                <button key={paper.id} className={selectedPaper?.id === paper.id ? 'paper-row active' : 'paper-row'} onClick={() => void openPaper(paper)}>
                  <FileText size={18} />
                  <span>
                    <strong>{paper.title || '未命名论文'}</strong>
                    <small>{paper.authors || '作者未填写'} · {parseLabel(paper.parse_status)}</small>
                  </span>
                </button>
              ))}
            </div>
          </div>

          <div className="panel results-panel">
            <div className="panel-header">
              <h3>文献检索</h3>
              <span>{results.length} 条结果</span>
            </div>
            <div className="result-list">
              {results.map((result) => (
                <article key={result.passage_id} className="result-item">
                  <strong>{result.paper_title || result.paper_id}</strong>
                  <p dangerouslySetInnerHTML={{ __html: result.snippet }} />
                  <small>第 {result.page_number} 页 · {result.section}</small>
                </article>
              ))}
            </div>
          </div>
        </section>
      </section>

      <aside className="inspector">
        <div className="inspector-header">
          <p className="eyebrow">论文 Inspector</p>
          <h2>{selectedPaper?.title || '选择一篇论文'}</h2>
        </div>
        {selectedPaper ? (
          <>
            <div className="meta-list">
              <span>作者：{selectedPaper.authors || '未填写'}</span>
              <span>年份：{selectedPaper.year || '未填写'}</span>
              <span>关系：{selectedPaper.relation_to_idea}</span>
              <span>状态：{parseLabel(selectedPaper.parse_status)}</span>
            </div>
            <div className="inspector-actions">
              <button onClick={() => void parseSelected()}>解析 PDF</button>
              <button onClick={() => void extractSelected()}>
                <ShieldCheck size={16} />
                启发式抽取
              </button>
            </div>
            <div className="tabs">
              {cardTabs.map((tab) => (
                <button key={tab} className={tab === activeTab ? 'active' : ''} onClick={() => setActiveTab(tab)}>
                  {cardLabel(tab)}
                </button>
              ))}
            </div>
            <div className="card-list">
              {visibleCards.map((card) => (
                <article key={card.id} className="knowledge-card">
                  <strong>{cardLabel(card.card_type)}</strong>
                  <p>{card.summary}</p>
                  <small>置信度 {card.confidence.toFixed(2)} · {card.source_passage_id ? '有来源' : '手动卡片'}</small>
                </article>
              ))}
            </div>
            <div className="passage-preview">
              <h3>原文片段</h3>
              {passages.slice(0, 6).map((passage) => (
                <p key={passage.id}>{passage.original_text}</p>
              ))}
            </div>
          </>
        ) : (
          <p className="empty">从论文列表选择一篇论文查看详情。</p>
        )}
      </aside>
    </main>
  );
}
