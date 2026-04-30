import React, { useState, useEffect } from 'react';
import { X, FileText } from 'lucide-react';
import type { Paper } from '../../types';
import { Select } from '../ui/Select';
import { DialogShell } from './DialogShell';

interface EditPaperModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (paperId: string, data: Partial<Paper>) => void;
  paper: Paper | null;
}

export const EditPaperModal: React.FC<EditPaperModalProps> = ({ isOpen, onClose, onSave, paper }) => {
  const [formData, setFormData] = useState<Partial<Paper>>({});

  useEffect(() => {
    if (paper) {
      setFormData({
        title: paper.title,
        authors: paper.authors,
        year: paper.year,
        venue: paper.venue,
        doi: paper.doi,
        relation_to_idea: paper.relation_to_idea,
        abstract: paper.abstract,
      });
    }
  }, [paper, isOpen]);

  if (!isOpen || !paper) return null;

  const handleChange = (field: keyof Paper, value: any) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  return (
    <DialogShell
      isOpen={isOpen}
      onClose={onClose}
      labelledBy="edit-paper-modal-title"
      className="settings-modal"
      style={{ maxWidth: '600px' }}
    >
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="brand-mark" style={{ width: '28px', height: '28px', fontSize: '14px' }}>
              <FileText size={16} />
            </div>
            <h2 id="edit-paper-modal-title">编辑论文元数据</h2>
          </div>
          <button className="btn-icon-close" onClick={onClose} aria-label="关闭编辑论文元数据">
            <X size={20} />
          </button>
        </div>

        <div className="form-scroll-area" style={{ marginTop: '20px' }}>
          <div className="form-group">
            <label>论文标题</label>
            <textarea 
              value={formData.title || ''} 
              onChange={(e) => handleChange('title', e.target.value)}
              rows={2}
              style={{ minHeight: '60px' }}
            />
          </div>

          <div className="form-group">
            <label>作者 (多个作者请用逗号分隔)</label>
            <input 
              value={formData.authors || ''} 
              onChange={(e) => handleChange('authors', e.target.value)}
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
            <div className="form-group">
              <label>出版年份</label>
              <input 
                type="number"
                value={formData.year || ''} 
                onChange={(e) => handleChange('year', parseInt(e.target.value) || 0)}
              />
            </div>
            <Select 
              label="研究关系"
              value={formData.relation_to_idea || 'unclassified'}
              onChange={(e) => handleChange('relation_to_idea', e.target.value)}
              options={[
                { value: 'baseline', label: '基础理论/基准' },
                { value: 'competing', label: '竞争方案' },
                { value: 'inspiring', label: '灵感来源' },
                { value: 'result_comparison', label: '对比分析' },
                { value: 'background', label: '背景资料' },
                { value: 'unclassified', label: '未分类' },
              ]}
            />
          </div>

          <div className="form-group">
            <label>发表渠道 (Venue)</label>
            <input 
              value={formData.venue || ''} 
              onChange={(e) => handleChange('venue', e.target.value)}
              placeholder="例如：Nature, CVPR 2024, arXiv"
            />
          </div>

          <div className="form-group">
            <label>DOI</label>
            <input 
              value={formData.doi || ''} 
              onChange={(e) => handleChange('doi', e.target.value)}
            />
          </div>

          <div className="form-group">
            <label>一句话摘要 (TL;DR)</label>
            <textarea 
              value={formData.abstract || ''} 
              onChange={(e) => handleChange('abstract', e.target.value)}
              rows={3}
            />
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>取消</button>
          <button className="btn-primary" onClick={() => onSave(paper.id, formData)}>保存修改</button>
        </div>
    </DialogShell>
  );
};
