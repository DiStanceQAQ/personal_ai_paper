import React, { useState } from 'react';
import { Cpu, Zap, FileText, Globe, BookOpen, Plus, Edit2 } from 'lucide-react';
import type { Paper, KnowledgeCard, AgentStatus, Passage } from '../../types';
import { KnowledgeCardFancy } from '../ui/KnowledgeCardFancy';

interface InspectorProps {
  isOpen: boolean;
  onToggle: () => void;
  selectedPaper: Paper | null;
  activeSpace: any;
  agentStatus: AgentStatus | null;
  onToggleAgent: () => void;
  onExtract: () => void;
  onDeleteCard: (id: string) => void;
  onAddManualCard: (type: string, summary: string) => void;
  onOpenEditPaper: () => void;
  activeTab: string;
  setActiveTab: (tab: any) => void;
  visibleCards: KnowledgeCard[];
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

export const Inspector: React.FC<InspectorProps> = ({
  isOpen,
  onToggle,
  selectedPaper,
  activeSpace,
  agentStatus,
  onToggleAgent,
  onExtract,
  onDeleteCard,
  onAddManualCard,
  onOpenEditPaper,
  activeTab,
  setActiveTab,
  visibleCards,
  cardTabs,
  cardLabel,
  parseLabel,
}) => {
  const [newCardText, setNewCardText] = useState('');
  const [isAdding, setIsAdding] = useState(false);

  const handleAddManual = () => {
    if (!newCardText.trim()) return;
    onAddManualCard(activeTab, newCardText.trim());
    setNewCardText('');
    setIsAdding(false);
  };

  return (
    <aside className={isOpen ? 'inspector' : 'inspector collapsed'}>
      <button className="inspector-toggle" onClick={onToggle}>
        {isOpen ? '→' : '←'}
      </button>
      {isOpen && (
        <div className="inspector-content">
          {selectedPaper ? (
            <>
              <div className="inspector-header">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div className="paper-status-tag">{parseLabel(selectedPaper.parse_status)}</div>
                  <button
                    className="btn-icon-secondary"
                    onClick={onOpenEditPaper}
                    title="编辑元数据"
                    style={{ width: '28px', height: '28px', borderRadius: '50%', background: '#f3f4f6' }}
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
                <button className="btn-ai-extract" onClick={onExtract}>
                  <Cpu size={16} />
                  <span>一键执行 AI 深度解析</span>
                </button>
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
                    <KnowledgeCardFancy key={card.id} card={card} cardLabel={cardLabel} onDelete={onDeleteCard} />
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
