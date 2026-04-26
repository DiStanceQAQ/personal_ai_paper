import React from 'react';

interface LoadingOverlayProps {
  isVisible: boolean;
  message: string;
}

export const LoadingOverlay: React.FC<LoadingOverlayProps> = ({ isVisible, message }) => {
  if (!isVisible) return null;

  return (
    <div className="modal-overlay" style={{ zIndex: 11000, background: 'rgba(0,0,0,0.2)' }}>
      <div className="processing-card">
        <div className="spinner"></div>
        <p>{message || '正在处理中...'}</p>
      </div>
    </div>
  );
};
