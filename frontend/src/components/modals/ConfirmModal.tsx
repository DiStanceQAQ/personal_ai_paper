import React from 'react';

interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
  confirmText?: string;
  isDanger?: boolean;
}

export const ConfirmModal: React.FC<ConfirmModalProps> = ({
  isOpen,
  title,
  message,
  onConfirm,
  onCancel,
  confirmText = '确定',
  isDanger = true,
}) => {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: '440px' }}>
        <div className="modal-header" style={{ borderBottom: 'none', paddingBottom: '0' }}>
          <h2>{title}</h2>
        </div>
        
        <div style={{ padding: '24px 32px 32px' }}>
          <p style={{ color: 'var(--text-secondary)', lineHeight: '1.6', fontSize: '14px' }}>
            {message}
          </p>
        </div>

        <div className="modal-actions" style={{ background: '#f9fafb', borderTop: '1px solid var(--border)' }}>
          <button className="btn-secondary" onClick={onCancel}>
            取消
          </button>
          <button
            className={isDanger ? 'btn-danger' : 'btn-primary'}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
};
