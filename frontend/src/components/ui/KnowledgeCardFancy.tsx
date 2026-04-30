import React, { useState, useRef, useEffect } from 'react';
import { BookOpen, X, Check, Edit2 } from 'lucide-react';
import type { KnowledgeCard, KnowledgeCardEvidence, KnowledgeCardOrigin } from '../../types';

interface KnowledgeCardFancyProps {
  card: KnowledgeCard;
  cardLabel: (type: string) => string;
  onDelete: (cardId: string) => void;
  onUpdate?: (cardId: string, summary: string) => Promise<void>;
  onOpenSource?: (pageNumber: number, passageId: string) => void;
  sourcePageById?: Record<string, number>;
}

function parseEvidence(rawEvidence: string): KnowledgeCardEvidence {
  try {
    const parsed: unknown = JSON.parse(rawEvidence || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { source_passage_ids: [] };
    }

    const payload = parsed as Record<string, unknown>;
    const sourcePassageIds = Array.isArray(payload.source_passage_ids)
      ? payload.source_passage_ids.filter((id): id is string => typeof id === 'string' && id.length > 0)
      : [];

    return {
      ...payload,
      source_passage_ids: sourcePassageIds,
      evidence_quote: typeof payload.evidence_quote === 'string' ? payload.evidence_quote : undefined,
      reasoning_summary: typeof payload.reasoning_summary === 'string' ? payload.reasoning_summary : undefined,
      metadata: payload.metadata && typeof payload.metadata === 'object' && !Array.isArray(payload.metadata)
        ? payload.metadata as Record<string, unknown>
        : undefined,
    };
  } catch {
    return { source_passage_ids: [] };
  }
}

function sourceIdsForCard(card: KnowledgeCard, evidence: KnowledgeCardEvidence): string[] {
  const ids = evidence.source_passage_ids.length > 0 ? evidence.source_passage_ids : [];
  if (ids.length > 0 || !card.source_passage_id) return ids;
  return [card.source_passage_id];
}

function originLabel(origin: KnowledgeCardOrigin, userEdited: number): 'AI' | 'Heuristic' | 'Manual' {
  if (userEdited === 1 || origin === 'user') return 'Manual';
  if (origin === 'ai') return 'AI';
  return 'Heuristic';
}

function originClassName(origin: KnowledgeCardOrigin, userEdited: number): KnowledgeCardOrigin {
  return userEdited === 1 || origin === 'user' ? 'user' : origin;
}

function formatSourceCount(count: number): string {
  return count === 1 ? '1 source' : `${count} sources`;
}

function formatConfidence(confidence: number): string {
  if (!Number.isFinite(confidence)) return '未知';
  const normalized = confidence <= 1 ? confidence * 100 : confidence;
  return `${Math.round(Math.min(100, Math.max(0, normalized)))}%`;
}

export const KnowledgeCardFancy: React.FC<KnowledgeCardFancyProps> = ({ 
  card, 
  cardLabel, 
  onDelete,
  onUpdate,
  onOpenSource,
  sourcePageById = {},
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editedSummary, setEditedSummary] = useState(card.summary);
  const [isSaving, setIsSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const evidence = parseEvidence(card.evidence_json);
  const sourceIds = sourceIdsForCard(card, evidence);
  const primarySourceId = sourceIds[0] || null;
  const primaryPageNumber = primarySourceId ? sourcePageById[primarySourceId] : undefined;
  const hasPrimaryPage = primaryPageNumber !== undefined && primaryPageNumber !== null;
  const canOpenSource = !!onOpenSource && !!primarySourceId && hasPrimaryPage;
  const evidenceEntries = [
    evidence.evidence_quote && { label: '证据摘录', text: evidence.evidence_quote },
    evidence.reasoning_summary && { label: '推理摘要', text: evidence.reasoning_summary },
  ].filter((entry): entry is { label: string; text: string } => Boolean(entry));

  useEffect(() => {
    if (isEditing && textareaRef.current) {
      textareaRef.current.focus();
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = textareaRef.current.scrollHeight + 'px';
    }
  }, [isEditing]);

  const handleSave = async () => {
    if (editedSummary === card.summary) {
      setIsEditing(false);
      return;
    }
    if (onUpdate) {
      setIsSaving(true);
      try {
        await onUpdate(card.id, editedSummary);
        setIsEditing(false);
      } catch (err) {
        console.error('Failed to update card:', err);
      } finally {
        setIsSaving(false);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSave();
    }
    if (e.key === 'Escape') {
      setEditedSummary(card.summary);
      setIsEditing(false);
    }
  };

  return (
    <article className={`knowledge-card-fancy ${card.card_type.toLowerCase().replace(' ', '-')} ${isEditing ? 'editing' : ''}`}>
      <div className="card-header-actions">
        <div className="card-type-indicator">{cardLabel(card.card_type)}</div>
        <div className="card-actions-group">
          {!isEditing && (
            <button className="btn-card-action" onClick={() => setIsEditing(true)} title="编辑卡片" aria-label="编辑卡片">
              <Edit2 size={12} />
            </button>
          )}
          <button className="btn-card-action delete" onClick={() => onDelete(card.id)} title="删除卡片" aria-label="删除卡片">
            <X size={12} />
          </button>
        </div>
      </div>

      {isEditing ? (
        <div className="card-edit-container">
          <textarea
            ref={textareaRef}
            className="card-edit-textarea"
            value={editedSummary}
            onChange={(e) => {
              setEditedSummary(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = e.target.scrollHeight + 'px';
            }}
            onKeyDown={handleKeyDown}
            disabled={isSaving}
          />
          <div className="card-edit-actions">
            <span className="kb-hint">Cmd + Enter 保存</span>
            <div className="btn-group">
              <button className="btn-save-minimal" onClick={handleSave} disabled={isSaving} title="保存" aria-label="保存">
                {isSaving ? <div className="spinner-tiny" /> : <Check size={14} />}
              </button>
              <button className="btn-cancel-minimal" onClick={() => { setEditedSummary(card.summary); setIsEditing(false); }} title="取消" aria-label="取消">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : (
        <>
          <p className="card-summary-text" onClick={() => setIsEditing(true)}>{card.summary}</p>
          <div className="card-source-summary">
            <span className={`card-origin-badge ${originClassName(card.created_by, card.user_edited)}`}>
              {originLabel(card.created_by, card.user_edited)}
            </span>
            <span>{formatSourceCount(sourceIds.length)}</span>
            <span>{hasPrimaryPage ? `p. ${primaryPageNumber}` : 'p. 未知'}</span>
            <span>置信 {formatConfidence(card.confidence)}</span>
            {canOpenSource && primarySourceId && (
              <button
                type="button"
                className="card-source-open"
                onClick={() => onOpenSource(primaryPageNumber, primarySourceId)}
                aria-label={`打开第 ${primaryPageNumber} 页原文`}
              >
                <BookOpen size={11} />
                原文
              </button>
            )}
          </div>
          {evidenceEntries.length > 0 && (
            <details className="card-evidence-details">
              <summary>证据</summary>
              <div className="card-evidence-body">
                {evidenceEntries.map((entry) => (
                  <div className="card-evidence-entry" key={entry.label}>
                    <span>{entry.label}</span>
                    <p>{entry.text}</p>
                  </div>
                ))}
              </div>
            </details>
          )}
        </>
      )}
    </article>
  );
};
