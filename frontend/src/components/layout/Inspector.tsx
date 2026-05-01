import React, { useEffect, useState } from 'react';
import {
  BrainCircuit,
  Clock3,
  Cpu,
  Database,
  Edit2,
  FileText,
  Gauge,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Server,
  Table2,
  XCircle,
} from 'lucide-react';
import DOMPurify from 'dompurify';
import type { Paper, KnowledgeCard, AgentStatus, PaperBackgroundTask, Space, EmbeddingRun, SearchResult } from '../../types';
import { api } from '../../api';
import { KnowledgeCardFancy } from '../ui/KnowledgeCardFancy';

export interface InspectorProps {
  isOpen: boolean;
  onToggle: () => void;
  selectedPaper: Paper | null;
  activeSpace: Space | null;
  agentStatus: AgentStatus | null;
  onToggleAgent: () => Promise<void>;
  onExtract: () => void;
  onCancelAnalysis: (runId: string) => void;
  onDeleteCard: (id: string) => void;
  onUpdateCard: (id: string, summary: string) => Promise<void>;
  onAddManualCard: (type: string, summary: string) => void;
  onOpenEditPaper: () => void;
  onOpenPdfReader: (paper: Paper, pageNumber?: number, sourceLabel?: string, passageId?: string) => void;
  activeTab: string;
  setActiveTab: (tab: any) => void;
  visibleCards: KnowledgeCard[];
  selectedSearchResult: SearchResult | null;
  analysisTask: PaperBackgroundTask | null;
  embeddingRun: EmbeddingRun | null;
  cardTabs: readonly string[];
  cardLabel: (type: string) => string;
  parseLabel: (status: string) => string;
  embeddingLabel: (status: Paper['embedding_status']) => string;
}

function relationLabel(rel: string): string {
  const labels: Record<string, string> = {
    baseline: '基础理论/基准',
    competing: '竞争方案',
    inspiring: '灵感来源',
    result_comparison: '对比分析',
    background: '背景资料',
    unclassified: '未分类',
  };
  return labels[rel] || rel;
}

function formatQualityScore(score: number | null | undefined): string {
  if (score == null) return '未知';
  const normalized = score <= 1 ? score * 100 : score;
  return `${Math.round(normalized)}%`;
}

function formatCount(count: number | null | undefined): string {
  return typeof count === 'number' ? count.toLocaleString() : '未知';
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '未知';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function metadataLabel(status: Paper['metadata_status']): string {
  const labels: Record<Paper['metadata_status'], string> = {
    empty: '元数据待提取',
    extracted: '元数据已提取',
    enriched: '元数据已增强',
    user_edited: '用户已编辑',
  };
  return labels[status] || status;
}

function embeddingStatusTone(status: Paper['embedding_status']): string {
  if (status === 'completed') return 'ready';
  if (status === 'failed') return 'failed';
  if (status === 'skipped') return 'muted';
  return 'running';
}

function embeddingRunMessage(
  paper: Paper,
  run: EmbeddingRun | null,
  embeddingLabel: (status: Paper['embedding_status']) => string,
): string {
  if (paper.embedding_status === 'completed') {
    const count = run?.embedded_count || run?.passage_count || paper.parse_diagnostics?.passage_count;
    return count ? `语义索引已就绪，${count} 个切片可用于深度检索。` : '语义索引已就绪，可用于深度检索。';
  }
  if (paper.embedding_status === 'failed') {
    return run?.last_error || '语义索引失败，搜索可能退回关键词匹配。';
  }
  if (paper.embedding_status === 'skipped') {
    return '该论文暂未生成语义索引。';
  }
  if (run?.status === 'running') {
    const total = run.passage_count || paper.parse_diagnostics?.passage_count || 0;
    const done = run.embedded_count + run.reused_count + run.skipped_count;
    return total > 0
      ? `语义索引运行中，已处理 ${Math.min(done, total)}/${total} 个切片。`
      : '语义索引正在后台运行。';
  }
  if (run?.status === 'queued') {
    return '语义索引已进入队列，等待后台 worker 处理。';
  }
  return `语义索引${embeddingLabel(paper.embedding_status)}。`;
}

function aiStatusLabel(task: PaperBackgroundTask | null): string {
  if (!task) return 'AI 未开始';
  if (task.phase === 'parsing') return '等待 PDF';
  if (task.phase === 'analyzing') return 'AI 运行中';
  if (task.phase === 'completed') return 'AI 已完成';
  if (task.phase === 'cancelled') return 'AI 已取消';
  return 'AI 失败';
}

function confidenceLabel(confidence: number | undefined): string {
  if (typeof confidence !== 'number' || !Number.isFinite(confidence)) return '';
  return `置信 ${Math.round(Math.min(1, Math.max(0, confidence)) * 100)}%`;
}

export const Inspector: React.FC<InspectorProps> = ({
  isOpen,
  onToggle,
  selectedPaper,
  activeSpace,
  agentStatus,
  onToggleAgent,
  onExtract,
  onCancelAnalysis,
  onDeleteCard,
  onUpdateCard,
  onAddManualCard,
  onOpenEditPaper,
  onOpenPdfReader,
  activeTab,
  setActiveTab,
  visibleCards,
  selectedSearchResult,
  analysisTask,
  embeddingRun,
  cardTabs,
  cardLabel,
  parseLabel,
  embeddingLabel,
}) => {
  const [newCardText, setNewCardText] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [sourcePageById, setSourcePageById] = useState<Record<string, number>>({});
  const parseDiagnostics = selectedPaper?.parse_diagnostics;
  const selectedPaperId = selectedPaper?.id;
  const extractBusy = analysisTask?.phase === 'parsing' || analysisTask?.phase === 'analyzing';
  const canCancelAnalysis = analysisTask?.phase === 'analyzing' && !!analysisTask.analysis_run_id;
  const visibleAnalysisTask = analysisTask?.phase === 'completed' ? null : analysisTask;
  const ToggleIcon = isOpen ? PanelRightClose : PanelRightOpen;
  const toggleLabel = isOpen ? '收起详情栏' : '展开详情栏';
  const embeddingTone = selectedPaper ? embeddingStatusTone(selectedPaper.embedding_status) : 'muted';
  const sourceResult = selectedPaper?.id === selectedSearchResult?.paper_id ? selectedSearchResult : null;
  const understanding = selectedPaper?.ai_understanding_zh || null;

  useEffect(() => {
    if (!selectedPaperId) {
      setSourcePageById({});
      return;
    }

    let isCurrent = true;
    api.listPassages(selectedPaperId)
      .then((paperPassages) => {
        if (!isCurrent) return;
        setSourcePageById(
          Object.fromEntries(
            paperPassages.map((passage) => [passage.id, passage.page_number]),
          ),
        );
      })
      .catch((error) => {
        console.error('Failed to load passage page numbers:', error);
        if (isCurrent) setSourcePageById({});
      });

    return () => {
      isCurrent = false;
    };
  }, [selectedPaperId]);

  const handleAddManual = () => {
    if (!newCardText.trim()) return;
    onAddManualCard(activeTab, newCardText.trim());
    setNewCardText('');
    setIsAdding(false);
  };

  return (
    <aside className={isOpen ? 'inspector' : 'inspector collapsed'}>
      <button
        type="button"
        className="inspector-toggle"
        onClick={onToggle}
        title={toggleLabel}
        aria-label={toggleLabel}
        aria-expanded={isOpen}
      >
        <ToggleIcon size={15} aria-hidden="true" />
      </button>
      {isOpen && (
        <div className="inspector-content">
          {selectedPaper ? (
            <>
              <div className="inspector-header">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <button
                    className="btn-icon-secondary"
                    onClick={onOpenEditPaper}
                    title="编辑元数据"
                    style={{ width: '28px', height: '28px', borderRadius: '50%' }}
                  >
                    <Edit2 size={14} />
                  </button>
                </div>
                <h2>{selectedPaper.title || '未命名论文'}</h2>
                <p className="paper-authors">{selectedPaper.authors || '作者未知'}</p>
              </div>

              {understanding && (
                <div className="inspector-section">
                  <div className="abstract-card ai-understanding-card">
                    <div className="ai-understanding-header">
                      <span>AI 中文理解</span>
                      {confidenceLabel(understanding.confidence) && (
                        <strong>{confidenceLabel(understanding.confidence)}</strong>
                      )}
                    </div>
                    {understanding.one_sentence && (
                      <p className="ai-understanding-one">{understanding.one_sentence}</p>
                    )}
                  </div>
                </div>
              )}

              <div className="ai-supercharge">
                <button className="btn-ai-extract" onClick={onExtract} disabled={extractBusy}>
                  <Cpu size={16} />
                  <span>
                    {analysisTask?.phase === 'parsing'
                      ? '后台解析中'
                      : analysisTask?.phase === 'analyzing'
                        ? '后台分析中'
                        : '一键执行 AI 深度解析'}
                  </span>
                </button>
              </div>

              {visibleAnalysisTask && (
                <div className={`task-progress-card ${visibleAnalysisTask.phase}`}>
                  <div className="task-progress-header">
                    <span>后台任务</span>
                    <div className="task-progress-actions">
                      {canCancelAnalysis && visibleAnalysisTask.analysis_run_id && (
                        <button
                          type="button"
                          className="btn-task-cancel"
                          onClick={() => {
                            if (visibleAnalysisTask.analysis_run_id) {
                              onCancelAnalysis(visibleAnalysisTask.analysis_run_id);
                            }
                          }}
                          title="取消 AI 深度分析"
                          aria-label="取消 AI 深度分析"
                        >
                          <XCircle size={14} />
                        </button>
                      )}
                      <span>{visibleAnalysisTask.progress}%</span>
                    </div>
                  </div>
                  <div className="task-progress-bar" aria-hidden="true">
                    <div
                      className={`task-progress-fill ${visibleAnalysisTask.phase}`}
                      style={{ width: `${visibleAnalysisTask.progress}%` }}
                    />
                  </div>
                  <p className="task-progress-message">{visibleAnalysisTask.message}</p>
                  {visibleAnalysisTask.error_detail && (
                    <p className="task-progress-error">{visibleAnalysisTask.error_detail}</p>
                  )}
                </div>
              )}

              {sourceResult && (
                <div className="search-source-card">
                  <div className="search-source-header">
                    <span>搜索命中来源</span>
                    <button
                      type="button"
                      className="search-source-open"
                      onClick={() =>
                        onOpenPdfReader(
                          selectedPaper,
                          sourceResult.page_number,
                          `搜索命中：第 ${sourceResult.page_number} 页`,
                          sourceResult.passage_id,
                        )
                      }
                    >
                      第 {sourceResult.page_number} 页 · 打开原文
                    </button>
                  </div>
                  <div className="search-source-meta">
                    <span>{sourceResult.section || '正文'}</span>
                    <span>段落 {sourceResult.paragraph_index + 1}</span>
                    <span>相关度 {Math.round(sourceResult.score * 100)}%</span>
                  </div>
                  <p dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(sourceResult.snippet) }} />
                </div>
              )}

              <div className="inspector-section">
                <div className="section-title">解析质量</div>
                <div className="parse-quality-grid">
                  <div className="parse-quality-item">
                    <div className="item-label-group">
                      <Server size={14} />
                      <label>解析器</label>
                    </div>
                    <span>{parseDiagnostics?.parser_backend || '未知'}</span>
                  </div>
                  <div className="parse-quality-item">
                    <div className="item-label-group">
                      <Gauge size={14} />
                      <label>质量评分</label>
                    </div>
                    <span>{formatQualityScore(parseDiagnostics?.quality_score)}</span>
                  </div>
                  <div className="parse-quality-item wide">
                    <div className="item-label-group">
                      <Clock3 size={14} />
                      <label>最后解析时间</label>
                    </div>
                    <span>{formatDateTime(parseDiagnostics?.last_parse_time)}</span>
                  </div>
                </div>
              </div>

              <div className="inspector-section">
                <div className="section-title">核心元数据</div>
                <div className="meta-pills-grid">
                  <div className="meta-pill"><label>出版年份</label><span>{selectedPaper.year || '未知'}</span></div>
                  <div className="meta-pill"><label>研究关系</label><span>{relationLabel(selectedPaper.relation_to_idea)}</span></div>
                  <div className="meta-pill"><label>发表渠道</label><span>{selectedPaper.venue || '未知'}</span></div>
                  <div className="meta-pill"><label>DOI / 标识符</label><span>{selectedPaper.doi || '无'}</span></div>
                </div>
              </div>

              <div className="inspector-section">
                <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span>知识卡片</span>
                  <button className="btn-add-inline" onClick={() => setIsAdding(!isAdding)} title="手动添加">
                    <Plus size={14} />
                  </button>
                </div>

                {isAdding && (
                  <div className="manual-card-form animation-fadeIn">
                    <textarea
                      placeholder={`手动输入一条[${cardLabel(activeTab)}]...`}
                      value={newCardText}
                      onChange={(e) => setNewCardText(e.target.value)}
                      rows={3}
                      autoFocus
                    />
                    <div className="form-actions-inline">
                      <button className="btn-secondary" onClick={() => setIsAdding(false)}>取消</button>
                      <button className="btn-primary" onClick={handleAddManual}>保存卡片</button>
                    </div>
                  </div>
                )}

                <div className="tabs">
                  <div className="tabs-list" style={{ display: 'flex', gap: '2px' }}>
                    {cardTabs.map((tab) => (
                      <button key={tab} className={tab === activeTab ? 'tab-btn active' : 'tab-btn'} onClick={() => setActiveTab(tab)}>{cardLabel(tab)}</button>
                    ))}
                  </div>
                </div>

                <div className="card-list">
                  {visibleCards.map((card) => (
                    <KnowledgeCardFancy 
                      key={card.id} 
                      card={card} 
                      cardLabel={cardLabel} 
                      onDelete={onDeleteCard}
                      onUpdate={onUpdateCard}
                      onOpenSource={
                        selectedPaper
                          ? (pageNumber, passageId) =>
                              onOpenPdfReader(
                                selectedPaper,
                                pageNumber,
                                `卡片证据：第 ${pageNumber} 页`,
                                passageId,
                              )
                          : undefined
                      }
                      sourcePageById={sourcePageById}
                    />
                  ))}
                  {visibleCards.length === 0 && !isAdding && (
                    <div className="empty-state-small">
                      <p>该分类下暂无内容</p>
                    </div>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <FileText size={48} style={{ opacity: 0.1, marginBottom: '16px' }} />
              <h3>选择论文查看详情</h3>
              <p>在左侧列表中点击一篇论文，即可在此查看 AI 提取的知识卡片和元数据。</p>
            </div>
          )}
        </div>
      )}
    </aside>
  );
};
