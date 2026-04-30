import React, { useState } from 'react';
import { CheckCircle2, Clock3, Search, UploadCloud, FileText, FolderOpen, Zap, Info, XCircle, Sparkles, ChevronDown } from 'lucide-react';
import DOMPurify from 'dompurify';
import type {
  AgentStatus,
  Paper,
  PaperBackgroundTask,
  SearchMode,
  SearchResult,
  SearchStatus,
  SearchWarmupState,
  Space,
  UploadQueueItem,
} from '../../types';
import { PaperCard } from '../ui/PaperCard';

interface WorkspaceProps {
  activeSpace: Space | null;
  agentStatus: AgentStatus | null;
  onToggleAgent: () => void;
  onOpenMCPGuide: () => void;
  activeView: 'library' | 'search';
  setActiveView: (view: 'library' | 'search') => void;
  papers: Paper[];
  backgroundTasks: Record<string, PaperBackgroundTask>;
  selectedPaper: Paper | null;
  onSelectPaper: (paper: Paper) => void;
  onDeletePaper: (e: React.MouseEvent, paperId: string) => void;
  onUpload: (files: File[]) => void;
  query: string;
  setQuery: (query: string) => void;
  searchMode: SearchMode;
  setSearchMode: (mode: SearchMode) => void;
  onSearch: () => void;
  results: SearchResult[];
  searchStatus: SearchStatus;
  searchError: string;
  searchWarmup: SearchWarmupState | null;
  parseLabel: (status: string) => string;
  embeddingLabel: (status: Paper['embedding_status']) => string;
  uploadQueue: UploadQueueItem[];
  selectedSearchResult: SearchResult | null;
  onOpenSearchResult: (result: SearchResult) => void;
}

export const Workspace: React.FC<WorkspaceProps> = ({
  activeSpace,
  agentStatus,
  onToggleAgent,
  onOpenMCPGuide,
  activeView,
  setActiveView,
  papers,
  backgroundTasks,
  selectedPaper,
  onSelectPaper,
  onDeletePaper,
  onUpload,
  query,
  setQuery,
  searchMode,
  setSearchMode,
  onSearch,
  results,
  searchStatus,
  searchError,
  searchWarmup,
  parseLabel,
  embeddingLabel,
  uploadQueue,
  selectedSearchResult,
  onOpenSearchResult,
}) => {
  const canSearch = query.trim().length > 0 && searchStatus !== 'loading';
  const uploadSucceeded = uploadQueue.filter((item) => item.status === 'success').length;
  const uploadFailed = uploadQueue.filter((item) => item.status === 'failed').length;
  const uploadInProgress = uploadQueue.some((item) => item.status === 'uploading');
  const [isDraggingPdf, setIsDraggingPdf] = useState(false);
  const [isModeMenuOpen, setIsModeMenuOpen] = useState(false);
  const warmupStatusLabel = (() => {
    if (searchMode !== 'hybrid') return null;
    if (!searchWarmup) return '语义检索将在后台预热';
    if (searchWarmup.status === 'warming') return '语义模型准备中，首次搜索会更顺滑';
    if (searchWarmup.status === 'ready') return '语义检索已就绪';
    if (searchWarmup.status === 'skipped') return '当前空间暂无语义索引';
    if (searchWarmup.status === 'failed') return '预热失败，仍可直接搜索';
    return '语义检索尚未预热';
  })();

  const handleDragOver = (event: React.DragEvent<HTMLDivElement>) => {
    if (!activeSpace) return;
    event.preventDefault();
    setIsDraggingPdf(true);
  };

  const handleDragLeave = (event: React.DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setIsDraggingPdf(false);
    }
  };

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDraggingPdf(false);
    const files = Array.from(event.dataTransfer.files || []).filter((file) =>
      file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'),
    );
    if (files.length > 0) onUpload(files);
  };

  return (
    <section className="workspace">
      <header className="topbar">
        <div>
          <p className="eyebrow">当前工作空间</p>
          <h2>{activeSpace?.name || '未选择空间'}</h2>
        </div>
        <div className="topbar-actions" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            className={agentStatus?.enabled ? 'status enabled' : 'status'}
            onClick={onToggleAgent}
            disabled={!activeSpace}
            aria-label={agentStatus?.enabled ? '禁用 MCP 连接' : '启用 MCP 连接'}
          >
            <Zap size={14} fill={agentStatus?.enabled ? 'currentColor' : 'none'} />
            {agentStatus?.enabled ? 'MCP已启用' : 'MCP已禁用'}
          </button>
          
          <button 
            className="btn-icon-secondary" 
            title="查看连接指南"
            onClick={onOpenMCPGuide}
            aria-label="查看 MCP 连接指南"
            style={{ width: '32px', height: '32px', borderRadius: '50%', background: '#f3f4f6', color: '#6b7280' }}
          >
            <Info size={16} />
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
            <div
              className={isDraggingPdf ? 'view-container library-view drag-active' : 'view-container library-view'}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
            >
              <div className="view-header">
                <div className="view-title">
                  <h3>我的论文</h3>
                  <span className="badge">{papers.length}</span>
                </div>
                <label className="btn-upload">
                  <UploadCloud size={16} />
                  <span>批量导入 PDF</span>
                  <input
                    type="file"
                    aria-label="导入 PDF 文件"
                    accept="application/pdf,.pdf"
                    multiple
                    onChange={(e) => {
                      const files = Array.from(e.target.files || []);
                      if (files.length > 0) onUpload(files);
                      e.currentTarget.value = '';
                    }}
                  />
                </label>
              </div>

              {papers.length > 0 ? (
                <div className="paper-grid">
                  {papers.map((paper) => (
                    <PaperCard
                      key={paper.id}
                      paper={paper}
                      backgroundTask={backgroundTasks[paper.id] || null}
                      isSelected={selectedPaper?.id === paper.id}
                      onSelect={onSelectPaper}
                      onDelete={onDeletePaper}
                      parseLabel={parseLabel}
                      embeddingLabel={embeddingLabel}
                    />
                  ))}
                </div>
              ) : (
                <div className="library-empty-state">
                  <p>该空间下暂无论文，请先导入。</p>
                </div>
              )}
            </div>
          ) : (
            <div className="view-container search-view">
              <div className="search-interface">
                <div className="search-input-wrapper">
                  <Search size={20} className="text-tertiary" />
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && canSearch && onSearch()}
                    placeholder="在海量论文中深度检索知识..."
                  />

                  <div className="search-btn-group">
                    <button
                      className="btn-search-main-combined"
                      onClick={onSearch}
                      disabled={!canSearch}
                    >
                      {searchMode === 'fts' ? <Zap size={16} /> : <Sparkles size={16} />}
                      <span>{searchStatus === 'loading' ? '检索中' : '立即检索'}</span>
                    </button>

                    <button
                      className="btn-search-mode-trigger"
                      onClick={() => setIsModeMenuOpen(!isModeMenuOpen)}
                      aria-label="切换检索模式"
                      title="切换检索模式"
                    >
                      <ChevronDown size={16} style={{ transform: isModeMenuOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }} />
                    </button>

                    {isModeMenuOpen && (
                      <>
                        <div
                          className="dropdown-overlay"
                          onClick={() => setIsModeMenuOpen(false)}
                        />
                        <div className="search-mode-menu animation-fadeIn" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className={searchMode === 'fts' ? 'menu-item active' : 'menu-item'}
                            onClick={() => { setSearchMode('fts'); setIsModeMenuOpen(false); }}
                          >
                            <div className="menu-icon"><Zap size={14} /></div>
                            <div className="menu-item-content">
                              <strong>快速关键词检索</strong>
                              <span>默认模式，速度最快，适合查明确术语。</span>
                            </div>
                            {searchMode === 'fts' && <CheckCircle2 size={14} className="check-mark" />}
                          </button>

                          <button
                            type="button"
                            className={searchMode === 'hybrid' ? 'menu-item active' : 'menu-item'}
                            onClick={() => { setSearchMode('hybrid'); setIsModeMenuOpen(false); }}
                          >
                            <div className="menu-icon"><Sparkles size={14} /></div>
                            <div className="menu-item-content">
                              <strong>语义深度检索</strong>
                              <span>启用 embedding 语义召回，更聪明但会慢一些。</span>
                            </div>
                            {searchMode === 'hybrid' && <CheckCircle2 size={14} className="check-mark" />}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </div>
                {warmupStatusLabel && (
                  <div className={`search-warmup-banner ${searchWarmup?.status || 'idle'}`} aria-live="polite">
                    {searchWarmup?.status === 'warming' ? (
                      <div className="spinner-tiny" aria-hidden="true" />
                    ) : searchWarmup?.status === 'ready' ? (
                      <CheckCircle2 size={14} />
                    ) : searchWarmup?.status === 'failed' ? (
                      <XCircle size={14} />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    <span>{warmupStatusLabel}</span>
                    {searchWarmup?.elapsed_ms != null && searchWarmup.status === 'ready' && (
                      <small>{Math.max(1, Math.round(searchWarmup.elapsed_ms / 1000))}s</small>
                    )}
                  </div>
                )}
              </div>
              <div className="search-results-list">
                {searchStatus === 'idle' && (
                  <div className="search-state">
                    <Search size={22} />
                    <h3>输入关键词开始检索</h3>
                    <p>支持按方法、指标、结论或论文片段查找当前空间中的内容。</p>
                  </div>
                )}

                {searchStatus === 'loading' && (
                  <div className="search-state">
                    <div className="spinner" aria-hidden="true" />
                    <h3>正在检索</h3>
                    <p>正在匹配论文片段和相关上下文。</p>
                  </div>
                )}

                {searchStatus === 'empty' && (
                  <div className="search-state">
                    <Search size={22} />
                    <h3>没有匹配结果</h3>
                    <p>换一个更具体的术语，或先导入并解析更多论文。</p>
                  </div>
                )}

                {searchStatus === 'error' && (
                  <div className="search-state search-state-error">
                    <Search size={22} />
                    <h3>检索失败</h3>
                    <p>{searchError || '检索请求失败，请稍后重试。'}</p>
                  </div>
                )}

                {searchStatus === 'success' && (
                  results.map((result) => (
                    <button
                      type="button"
                      key={result.passage_id}
                      className={
                        selectedSearchResult?.passage_id === result.passage_id
                          ? 'search-result-card active'
                          : 'search-result-card'
                      }
                      onClick={() => onOpenSearchResult(result)}
                      aria-label={`打开 ${result.paper_title || result.paper_id} 第 ${result.page_number} 页的搜索来源`}
                    >
                      <div className="result-source">
                        <FileText size={16} />
                        <span>{result.paper_title || result.paper_id}</span>
                        <small>打开来源</small>
                      </div>

                      <p dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(result.snippet) }} />

                      <div className="result-meta">
                        <span className="result-section-badge">{result.section || '正文'}</span>
                        <span className="dot">·</span>
                        <span>第 {result.page_number} 页</span>
                      </div>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}
        </>
      ) : (
        <div className="empty-state" style={{ marginTop: '10%' }}>
          <FolderOpen size={64} style={{ marginBottom: '24px', opacity: 0.1 }} />
          <h3>开启您的研究之旅</h3>
          <p>请在左侧选择或新建一个研究空间。</p>
        </div>
      )}
    </section>
  );
};
