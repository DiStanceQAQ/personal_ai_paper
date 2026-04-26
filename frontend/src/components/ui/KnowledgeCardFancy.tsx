import React from 'react';
import { X } from 'lucide-react';
import type { KnowledgeCard } from '../../types';

interface KnowledgeCardFancyProps {
  card: KnowledgeCard;
  cardLabel: (type: string) => string;
  onDelete: (cardId: string) => void;
}

export const KnowledgeCardFancy: React.FC<KnowledgeCardFancyProps> = ({ card, cardLabel, onDelete }) => {
  return (
    <article className={`knowledge-card-fancy ${card.card_type.toLowerCase().replace(' ', '-')}`}>
      <div className="card-header-actions">
        <div className="card-type-indicator">{cardLabel(card.card_type)}</div>
        <button className="btn-card-delete" onClick={() => onDelete(card.id)}>
          <X size={12} />
        </button>
      </div>
      <p>{card.summary}</p>
    </article>
  );
};
