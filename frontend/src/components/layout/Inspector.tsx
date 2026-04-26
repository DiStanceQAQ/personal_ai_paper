import React from 'react';
import { Cpu, Zap, FileText } from 'lucide-react';
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
  activeTab: string;
  setActiveTab: (tab: any) => void;
  visibleCards: KnowledgeCard[];
  cardTabs: readonly string[];
  cardLabel: (type: string) => string;
  parseLabel: (status: string) => string;
}

export const Inspector: React.FC<InspectorProps> = ({
  isOpen,
  onToggle,
  selectedPaper,
  activeSpace,
  agentStatus,
  onToggleAgent,
  onExtract,
  activeTab,
  setActiveTab,
  visibleCards,
  cardTabs,
  cardLabel,
  parseLabel,
}) => {
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
                <div className="paper-status-tag">{parseLabel(selectedPaper.parse_status)}</div>
                <h2>{selectedPaper.title || '未命名论文'}</h2>
                <p className="paper-authors">{selectedPaper.authors || '作者未知'}</p>
              </div>
              <div className="ai-supercharge">
                <button className="btn-ai-extract" onClick={onExtract}>
                  <Cpu size={16} fill="white" />
                  <span>一键执行 AI 深度解析</span>
                </button>
              </div>
              <div className="inspector-section">
                <div className="section-title">核心元数据</div>
                <div className="meta-pills">
                  <div className="meta-pill">
                    <label>出版年份</label>
                    <span>{selectedPaper.year || '未知'}</span>
                  </div>
                  <div className="meta-pill">
                    <label>研究关系</label>
                    <span>{selectedPaper.relation_to_idea}</span>
                  </div>
                </div>
              </div>
              <div className="inspector-section">
                <div className="section-title">知识卡片</div>
                <div className="tabs">
                  <div className="tabs-list" style={{ display: 'flex', gap: '2px' }}>
                    {cardTabs.map((tab) => (
                      <button
                        key={tab}
                        className={tab === activeTab ? 'tab-btn active' : 'tab-btn'}
                        onClick={() => setActiveTab(tab)}
                      >
                        {cardLabel(tab)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="card-list">
                  {visibleCards.map((card) => (
                    <KnowledgeCardFancy key={card.id} card={card} cardLabel={cardLabel} />
                  ))}
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
