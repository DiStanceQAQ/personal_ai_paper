import React, { useState, useRef, useEffect } from 'react';
import { X, Check, Edit2 } from 'lucide-react';
import type { KnowledgeCard } from '../../types';

interface KnowledgeCardFancyProps {
  card: KnowledgeCard;
  cardLabel: (type: string) => string;
  onDelete: (cardId: string) => void;
  onUpdate?: (cardId: string, summary: string) => Promise<void>;
}

export const KnowledgeCardFancy: React.FC<KnowledgeCardFancyProps> = ({ 
  card, 
  cardLabel, 
  onDelete,
  onUpdate 
}) => {
  const [isEditing, setIsEditing] = useState(false);
  const [editedSummary, setEditedSummary] = useState(card.summary);
  const [isSaving, setIsSaving] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

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
            <button className="btn-card-action" onClick={() => setIsEditing(true)}>
              <Edit2 size={12} />
            </button>
          )}
          <button className="btn-card-action delete" onClick={() => onDelete(card.id)}>
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
              <button className="btn-save-minimal" onClick={handleSave} disabled={isSaving}>
                {isSaving ? <div className="spinner-tiny" /> : <Check size={14} />}
              </button>
              <button className="btn-cancel-minimal" onClick={() => { setEditedSummary(card.summary); setIsEditing(false); }}>
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : (
        <p className="card-summary-text" onClick={() => setIsEditing(true)}>{card.summary}</p>
      )}
    </article>
  );
};
