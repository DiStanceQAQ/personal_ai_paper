import React, { useEffect } from 'react';

interface ToastProps {
  message: string;
  onClose: () => void;
  isVisible: boolean;
  type?: 'success' | 'error';
}

export const Toast: React.FC<ToastProps> = ({ message, onClose, isVisible, type = 'success' }) => {
  useEffect(() => {
    if (!isVisible) return undefined;

    const timeoutId = window.setTimeout(onClose, 2000);
    return () => window.clearTimeout(timeoutId);
  }, [isVisible, message, type, onClose]);

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
