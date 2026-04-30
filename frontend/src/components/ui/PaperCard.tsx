import React from 'react';
import { FileText, Gauge, Globe, Server, Trash2 } from 'lucide-react';
import type { Paper, PaperBackgroundTask } from '../../types';

interface PaperCardProps {
  paper: Paper;
  backgroundTask: PaperBackgroundTask | null;
  isSelected: boolean;
  onSelect: (paper: Paper) => void;
  onDelete: (e: React.MouseEvent, paperId: string) => void;
  parseLabel: (status: string) => string;
  embeddingLabel: (status: Paper['embedding_status']) => string;
}

export const PaperCard: React.FC<PaperCardProps> = ({
  paper,
  backgroundTask,
  isSelected,
  onSelect,
  onDelete,
  parseLabel,
  embeddingLabel,
}) => {
  const diagnostics = paper.parse_diagnostics;
  const qualityScore =
    diagnostics?.quality_score == null
      ? null
      : `${Math.round((diagnostics.quality_score <= 1 ? diagnostics.quality_score * 100 : diagnostics.quality_score))}%`;
  const displayStatus = (() => {
    if (backgroundTask?.phase === 'parsing' || backgroundTask?.phase === 'analyzing') return 'parsing';
    if (backgroundTask?.phase === 'failed' && backgroundTask.analysis_run_id) return 'error';
    if (backgroundTask?.phase === 'cancelled' && backgroundTask.analysis_run_id) return 'pending';
    return paper.parse_status;
  })();
  const displayLabel = (() => {
    if (backgroundTask?.phase === 'analyzing') return 'AI 解析中';
    if (backgroundTask?.phase === 'failed' && backgroundTask.analysis_run_id) return 'AI 解析失败';
    if (backgroundTask?.phase === 'cancelled' && backgroundTask.analysis_run_id) return 'AI 已取消';
    return `PDF ${parseLabel(displayStatus)}`;
  })();

  return (
    <div className={isSelected ? 'paper-card-wrapper active' : 'paper-card-wrapper'}>
      <button className="paper-card-main" onClick={() => onSelect(paper)}>
        <div className="paper-card-icon">
          <FileText size={20} />
        </div>
        <div className="paper-card-content">
          <strong className="paper-card-title">{paper.title || '未命名论文'}</strong>
          {paper.year && <span className="paper-card-year">({paper.year})</span>}
          
          <div className="paper-card-meta-line">
            <span className="paper-card-authors">{paper.authors || '作者未知'}</span>
          </div>

          <div className="paper-card-footer">
            <div className="paper-card-source">
              <Globe size={12} />
              <span>{paper.venue || '未知来源'}</span>
            </div>
            <div className="paper-card-status-group">
              <span className={`status-badge ${displayStatus}`}>
                {displayLabel}
              </span>
            </div>
          </div>

          {diagnostics && (
            <div className="paper-card-diagnostics" aria-label="解析质量摘要">
              <span className="diagnostic-chip" title="解析器">
                <Server size={11} />
                {diagnostics.parser_backend || '未知解析器'}
              </span>
              {qualityScore && (
                <span className="diagnostic-chip" title="质量评分">
                  <Gauge size={11} />
                  {qualityScore}
                </span>
              )}
            </div>
          )}
        </div>
      </button>
      <div className="paper-card-actions">
        <button
          className="btn-icon-danger"
          onClick={(e) => onDelete(e, paper.id)}
          title="删除论文"
          aria-label={`删除论文 ${paper.title || '未命名论文'}`}
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
};
