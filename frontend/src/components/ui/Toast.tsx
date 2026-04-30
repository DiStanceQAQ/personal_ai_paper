import React from 'react';

interface ToastProps {
  message: string;
  onClose: () => void;
  isVisible: boolean;
  type?: 'success' | 'error';
}

export const Toast: React.FC<ToastProps> = ({ message, onClose, isVisible, type = 'success' }) => {
  if (!isVisible) return null;

  return (
    <div
      className={`notice ${type}`}
      onClick={onClose}
      role={type === 'error' ? 'alert' : 'status'}
      aria-live={type === 'error' ? 'assertive' : 'polite'}
    >
      {message}
    </div>
  );
};
