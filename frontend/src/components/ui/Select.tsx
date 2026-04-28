import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';

interface Option {
  value: string;
  label: string;
}

interface SelectProps {
  id?: string;
  name?: string;
  label?: string;
  options: Option[];
  error?: string;
  value?: string;
  defaultValue?: string;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
  onBlur?: React.FocusEventHandler<HTMLButtonElement>;
  onChange?: (event: React.ChangeEvent<HTMLSelectElement>) => void;
}

const MENU_GAP = 4;
const MENU_MAX_HEIGHT = 260;
const MENU_VIEWPORT_PADDING = 12;

export const Select: React.FC<SelectProps> = ({
  id,
  name,
  label,
  options,
  error,
  value,
  defaultValue,
  disabled = false,
  placeholder,
  className = '',
  onBlur,
  onChange,
}) => {
  const generatedId = useId();
  const selectId = id ?? generatedId;
  const listboxId = `${selectId}-listbox`;
  const errorId = `${selectId}-error`;
  const wrapperRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [isOpen, setIsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({});

  const selectedValue =
    typeof value === 'string'
      ? value
      : typeof defaultValue === 'string'
        ? defaultValue
        : '';
  const selectedIndex = options.findIndex((option) => option.value === selectedValue);
  const resolvedIndex = selectedIndex >= 0 ? selectedIndex : 0;
  const selectedOption = options[selectedIndex] ?? null;
  const displayLabel = selectedOption?.label ?? placeholder ?? options[0]?.label ?? '';
  const triggerClassName = [
    'custom-select-trigger',
    className,
    error ? 'error' : '',
    isOpen ? 'open' : '',
  ].filter(Boolean).join(' ');

  const closeMenu = () => setIsOpen(false);

  const updateMenuPosition = () => {
    if (!triggerRef.current) return;

    const rect = triggerRef.current.getBoundingClientRect();
    const measuredHeight = menuRef.current?.offsetHeight ?? 0;
    const fallbackHeight = Math.min(options.length * 40 + 8, MENU_MAX_HEIGHT);
    const expectedHeight = Math.max(measuredHeight, fallbackHeight);
    const spaceBelow = window.innerHeight - rect.bottom - MENU_GAP - MENU_VIEWPORT_PADDING;
    const spaceAbove = rect.top - MENU_GAP - MENU_VIEWPORT_PADDING;
    const shouldOpenAbove = spaceBelow < Math.min(expectedHeight, 180) && spaceAbove > spaceBelow;

    const maxHeight = Math.max(
      120,
      Math.min(MENU_MAX_HEIGHT, shouldOpenAbove ? spaceAbove : spaceBelow),
    );
    const top = shouldOpenAbove
      ? Math.max(MENU_VIEWPORT_PADDING, rect.top - MENU_GAP - Math.min(expectedHeight, maxHeight))
      : rect.bottom + MENU_GAP;

    setMenuStyle({
      position: 'fixed',
      top: `${Math.round(top)}px`,
      left: `${Math.round(rect.left)}px`,
      width: `${Math.round(rect.width)}px`,
      maxHeight: `${Math.round(maxHeight)}px`,
    });
  };

  useEffect(() => {
    if (!isOpen) return;
    setHighlightedIndex(resolvedIndex);
  }, [isOpen, resolvedIndex]);

  useEffect(() => {
    if (!isOpen) return;

    updateMenuPosition();
    const animationFrame = window.requestAnimationFrame(updateMenuPosition);

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (wrapperRef.current?.contains(target) || menuRef.current?.contains(target)) {
        return;
      }
      closeMenu();
    };

    const handleGlobalKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      closeMenu();
      triggerRef.current?.focus();
    };

    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('resize', updateMenuPosition);
    window.addEventListener('scroll', updateMenuPosition, true);
    document.addEventListener('keydown', handleGlobalKeyDown);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('resize', updateMenuPosition);
      window.removeEventListener('scroll', updateMenuPosition, true);
      document.removeEventListener('keydown', handleGlobalKeyDown);
    };
  }, [isOpen, options.length]);

  useEffect(() => {
    if (!isOpen) return;
    const activeOption = menuRef.current?.querySelector<HTMLElement>(`[data-index="${highlightedIndex}"]`);
    activeOption?.scrollIntoView({ block: 'nearest' });
  }, [highlightedIndex, isOpen]);

  const emitChange = (nextValue: string) => {
    onChange?.({
      target: { value: nextValue, name },
      currentTarget: { value: nextValue, name },
    } as React.ChangeEvent<HTMLSelectElement>);
  };

  const selectOption = (nextValue: string) => {
    if (nextValue !== selectedValue) {
      emitChange(nextValue);
    }
    setHighlightedIndex(options.findIndex((option) => option.value === nextValue));
    closeMenu();
    window.requestAnimationFrame(() => {
      triggerRef.current?.focus();
    });
  };

  const openMenu = () => {
    if (disabled || options.length === 0) return;
    setHighlightedIndex(resolvedIndex);
    setIsOpen(true);
  };

  const toggleMenu = () => {
    if (isOpen) {
      closeMenu();
      return;
    }
    openMenu();
  };

  const moveHighlight = (nextIndex: number) => {
    if (options.length === 0) return;
    setHighlightedIndex(Math.max(0, Math.min(nextIndex, options.length - 1)));
  };

  const handleTriggerKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (disabled || options.length === 0) return;

    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
          moveHighlight(selectedIndex >= 0 ? selectedIndex + 1 : 0);
          return;
        }
        moveHighlight(highlightedIndex + 1);
        return;
      case 'ArrowUp':
        event.preventDefault();
        if (!isOpen) {
          setIsOpen(true);
          moveHighlight(selectedIndex >= 0 ? selectedIndex - 1 : options.length - 1);
          return;
        }
        moveHighlight(highlightedIndex - 1);
        return;
      case 'Home':
        event.preventDefault();
        if (!isOpen) setIsOpen(true);
        moveHighlight(0);
        return;
      case 'End':
        event.preventDefault();
        if (!isOpen) setIsOpen(true);
        moveHighlight(options.length - 1);
        return;
      case 'Enter':
      case ' ':
        event.preventDefault();
        if (!isOpen) {
          openMenu();
          return;
        }
        selectOption(options[highlightedIndex]?.value ?? selectedValue);
        return;
      case 'Tab':
        closeMenu();
        return;
      default:
        return;
    }
  };

  return (
    <div className="form-group">
      {label && <label htmlFor={selectId}>{label}</label>}
      <div className={`select-wrapper${isOpen ? ' open' : ''}`} ref={wrapperRef}>
        {name && <input type="hidden" name={name} value={selectedValue} />}
        <button
          id={selectId}
          ref={triggerRef}
          type="button"
          className={triggerClassName}
          onClick={toggleMenu}
          onKeyDown={handleTriggerKeyDown}
          onBlur={onBlur}
          aria-haspopup="listbox"
          aria-expanded={isOpen}
          aria-controls={isOpen ? listboxId : undefined}
          aria-activedescendant={isOpen ? `${selectId}-option-${highlightedIndex}` : undefined}
          aria-invalid={!!error || undefined}
          aria-describedby={error ? errorId : undefined}
          disabled={disabled}
        >
          <span className={`select-value${selectedOption ? '' : ' placeholder'}`}>{displayLabel}</span>
          <span className="select-arrow" aria-hidden="true">
            <ChevronDown size={16} />
          </span>
        </button>

        {isOpen && typeof document !== 'undefined' && createPortal(
          <div
            id={listboxId}
            ref={menuRef}
            className="custom-select-menu"
            style={menuStyle}
            role="listbox"
            aria-labelledby={selectId}
          >
            {options.map((option, index) => {
              const isHighlighted = highlightedIndex === index;
              const isSelected = option.value === selectedValue;

              return (
                <button
                  key={option.value}
                  id={`${selectId}-option-${index}`}
                  type="button"
                  role="option"
                  data-index={index}
                  aria-selected={isSelected}
                  className={[
                    'custom-select-option',
                    isSelected ? 'selected' : '',
                    isHighlighted ? 'highlighted' : '',
                  ].filter(Boolean).join(' ')}
                  onClick={() => selectOption(option.value)}
                  onMouseEnter={() => setHighlightedIndex(index)}
                >
                  <span className="custom-select-option-label">{option.label}</span>
                  <span className="custom-select-option-check" aria-hidden="true">
                    {isSelected ? <Check size={16} /> : null}
                  </span>
                </button>
              );
            })}
          </div>,
          document.body,
        )}
      </div>
      {error && <p className="error-message" id={errorId}>{error}</p>}
    </div>
  );
};
