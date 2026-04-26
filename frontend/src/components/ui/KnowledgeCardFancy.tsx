import React from 'react';
import type { KnowledgeCard } from '../../types';

interface KnowledgeCardFancyProps {
  card: KnowledgeCard;
  cardLabel: (type: string) => string;
}

export const KnowledgeCardFancy: React.FC<KnowledgeCardFancyProps> = ({ card, cardLabel }) => {
  return (
    <article className={`knowledge-card-fancy ${card.card_type.toLowerCase().replace(' ', '-')}`}>
      <div className="card-type-indicator">{cardLabel(card.card_type)}</div>
      <p>{card.summary}</p>
    </article>
  );
};
