import React from 'react';
import { FileText, Trash2 } from 'lucide-react';
import type { Paper } from '../../types';

interface PaperCardProps {
  paper: Paper;
  isSelected: boolean;
  onSelect: (paper: Paper) => void;
  onDelete: (e: React.MouseEvent, paperId: string) => void;
  parseLabel: (status: string) => string;
}

export const PaperCard: React.FC<PaperCardProps> = ({
  paper,
  isSelected,
  onSelect,
  onDelete,
  parseLabel,
}) => {
  return (
    <div className={isSelected ? 'paper-card-wrapper active' : 'paper-card-wrapper'}>
      <button className="paper-card-main" onClick={() => onSelect(paper)}>
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
      <div className="paper-card-actions">
        <button
          className="btn-icon-danger"
          onClick={(e) => onDelete(e, paper.id)}
          title="删除论文"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
};
