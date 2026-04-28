import React from 'react';
import { UploadCloud, Search, FileText, FolderOpen, Zap, Info } from 'lucide-react';
import DOMPurify from 'dompurify';
import type { AgentStatus, Paper, SearchResult, SearchStatus, Space } from '../../types';
import { PaperCard } from '../ui/PaperCard';

interface WorkspaceProps {
  activeSpace: Space | null;
  agentStatus: AgentStatus | null;
  onToggleAgent: () => void;
  onOpenMCPGuide: () => void;
  activeView: 'library' | 'search';
  setActiveView: (view: 'library' | 'search') => void;
  papers: Paper[];
  selectedPaper: Paper | null;
  onSelectPaper: (paper: Paper) => void;
  onDeletePaper: (e: React.MouseEvent, paperId: string) => void;
  onUpload: (file: File) => void;
  query: string;
  setQuery: (query: string) => void;
  onSearch: () => void;
  results: SearchResult[];
  searchStatus: SearchStatus;
  searchError: string;
  parseLabel: (status: string) => string;
}

export const Workspace: React.FC<WorkspaceProps> = ({
  activeSpace,
  agentStatus,
  onToggleAgent,
  onOpenMCPGuide,
  activeView,
  setActiveView,
  papers,
  selectedPaper,
  onSelectPaper,
  onDeletePaper,
  onUpload,
  query,
  setQuery,
  onSearch,
  results,
  searchStatus,
  searchError,
  parseLabel,
}) => {
  const canSearch = query.trim().length > 0 && searchStatus !== 'loading';

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
          >
            <Zap size={14} fill={agentStatus?.enabled ? 'currentColor' : 'none'} />
            {agentStatus?.enabled ? 'MCP已启用' : 'MCP已禁用'}
          </button>
          
          <button 
            className="btn-icon-secondary" 
            title="查看连接指南"
            onClick={onOpenMCPGuide}
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
            <div className="view-container library-view">
              <div className="view-header">
                <div className="view-title">
                  <h3>我的论文</h3>
                  <span className="badge">{papers.length}</span>
                </div>
                <label className="btn-upload">
                  <UploadCloud size={16} />
                  <span>导入 PDF</span>
                  <input
                    type="file"
                    accept="application/pdf,.pdf"
                    onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
                  />
                </label>
              </div>

              <div className="paper-grid">
                {papers.length > 0 ? (
                  papers.map((paper) => (
                    <PaperCard
                      key={paper.id}
                      paper={paper}
                      isSelected={selectedPaper?.id === paper.id}
                      onSelect={onSelectPaper}
                      onDelete={onDeletePaper}
                      parseLabel={parseLabel}
                    />
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
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && canSearch && onSearch()}
                    placeholder="在海量论文中深度检索知识..."
                  />
                  <button className="btn-search-main" onClick={onSearch} disabled={!canSearch}>
                    <span>{searchStatus === 'loading' ? '检索中' : '立即检索'}</span>
                  </button>
                </div>
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
                    <article key={result.passage_id} className="search-result-card">
                      <div className="result-source">
                        <FileText size={16} />
                        <span>{result.paper_title || result.paper_id}</span>
                      </div>

                      <p dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(result.snippet) }} />

                      <div className="result-meta">
                        <span className="result-section-badge">{result.section || '正文'}</span>
                        <span className="dot">·</span>
                        <span>第 {result.page_number} 页</span>
                      </div>
                    </article>
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
