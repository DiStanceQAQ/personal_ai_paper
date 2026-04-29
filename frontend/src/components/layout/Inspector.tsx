import React, { useEffect, useState } from 'react';
import {
  AlertTriangle,
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
import type { Paper, KnowledgeCard, AgentStatus, PaperBackgroundTask, Space } from '../../types';
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
  activeTab: string;
  setActiveTab: (tab: any) => void;
  visibleCards: KnowledgeCard[];
  analysisTask: PaperBackgroundTask | null;
  cardTabs: readonly string[];
  cardLabel: (type: string) => string;
  parseLabel: (status: string) => string;
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

function aiStatusLabel(task: PaperBackgroundTask | null): string {
  if (!task) return 'AI 未开始';
  if (task.phase === 'parsing') return '等待 PDF';
  if (task.phase === 'analyzing') return 'AI 运行中';
  if (task.phase === 'completed') return 'AI 已完成';
  if (task.phase === 'cancelled') return 'AI 已取消';
  return 'AI 失败';
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
  activeTab,
  setActiveTab,
  visibleCards,
  analysisTask,
  cardTabs,
  cardLabel,
  parseLabel,
}) => {
  const [newCardText, setNewCardText] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [sourcePageById, setSourcePageById] = useState<Record<string, number>>({});
  const parseDiagnostics = selectedPaper?.parse_diagnostics;
  const selectedPaperId = selectedPaper?.id;
  const extractBusy = analysisTask?.phase === 'parsing' || analysisTask?.phase === 'analyzing';
  const canCancelAnalysis = analysisTask?.phase === 'analyzing' && !!analysisTask.analysis_run_id;
  const ToggleIcon = isOpen ? PanelRightClose : PanelRightOpen;
  const toggleLabel = isOpen ? '收起详情栏' : '展开详情栏';

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
                  <div className="paper-status-strip">
                    <div className={`paper-status-tag parse-${selectedPaper.parse_status}`}>
                      PDF {parseLabel(selectedPaper.parse_status)}
                    </div>
                    <div className={`paper-status-tag metadata-${selectedPaper.metadata_status}`}>
                      {metadataLabel(selectedPaper.metadata_status)}
                    </div>
                    <div className={`paper-status-tag ai-${analysisTask?.phase || 'idle'}`}>
                      {aiStatusLabel(analysisTask)}
                    </div>
                  </div>
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

              {selectedPaper.abstract && (
                <div className="inspector-section">
                  <div className="abstract-card">
                    <p>{selectedPaper.abstract}</p>
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

              {analysisTask && (
                <div className={`task-progress-card ${analysisTask.phase}`}>
                  <div className="task-progress-header">
                    <span>后台任务</span>
                    <div className="task-progress-actions">
                      {canCancelAnalysis && analysisTask.analysis_run_id && (
                        <button
                          type="button"
                          className="btn-task-cancel"
                          onClick={() => {
                            if (analysisTask.analysis_run_id) {
                              onCancelAnalysis(analysisTask.analysis_run_id);
                            }
                          }}
                          title="取消 AI 深度分析"
                          aria-label="取消 AI 深度分析"
                        >
                          <XCircle size={14} />
                        </button>
                      )}
                      <span>{analysisTask.progress}%</span>
                    </div>
                  </div>
                  <div className="task-progress-bar" aria-hidden="true">
                    <div
                      className={`task-progress-fill ${analysisTask.phase}`}
                      style={{ width: `${analysisTask.progress}%` }}
                    />
                  </div>
                  <p className="task-progress-message">{analysisTask.message}</p>
                  {analysisTask.error_detail && (
                    <p className="task-progress-error">{analysisTask.error_detail}</p>
                  )}
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
                  <div className={parseDiagnostics?.warning_count ? 'parse-quality-item has-warning' : 'parse-quality-item'}>
                    <div className="item-label-group">
                      <AlertTriangle size={14} />
                      <label>警告</label>
                    </div>
                    <span>{formatCount(parseDiagnostics?.warning_count)}</span>
                  </div>
                  <div className="parse-quality-item">
                    <div className="item-label-group">
                      <Database size={14} />
                      <label>切片</label>
                    </div>
                    <span>{formatCount(parseDiagnostics?.passage_count)}</span>
                  </div>
                  <div className="parse-quality-item">
                    <div className="item-label-group">
                      <Table2 size={14} />
                      <label>表格</label>
                    </div>
                    <span>{formatCount(parseDiagnostics?.table_count)}</span>
                  </div>
                  <div className="parse-quality-item wide">
                    <div className="item-label-group">
                      <Clock3 size={14} />
                      <label>最后解析</label>
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
