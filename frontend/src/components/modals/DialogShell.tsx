import React, { useEffect, useRef } from 'react';

interface DialogShellProps {
  isOpen: boolean;
  onClose: () => void;
  labelledBy: string;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}

const focusableSelector = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function getFocusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(focusableSelector))
    .filter((element) => !element.hasAttribute('disabled') && element.offsetParent !== null);
}

export const DialogShell: React.FC<DialogShellProps> = ({
  isOpen,
  onClose,
  labelledBy,
  children,
  className = '',
  style,
}) => {
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousActiveElement = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;

    previousActiveElement.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;

    const frame = window.requestAnimationFrame(() => {
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = getFocusableElements(dialog);
      (focusable[0] || dialog).focus();
    });

    return () => {
      window.cancelAnimationFrame(frame);
      previousActiveElement.current?.focus();
      previousActiveElement.current = null;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }

    if (event.key !== 'Tab') return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = getFocusableElements(dialog);
    if (focusable.length === 0) {
      event.preventDefault();
      dialog.focus();
      return;
    }

    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className={['modal', className].filter(Boolean).join(' ')}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        style={style}
        onKeyDown={handleKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
};
