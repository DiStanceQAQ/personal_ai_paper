import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const selectComponent = readFileSync('frontend/src/components/ui/Select.tsx', 'utf8');
const styles = readFileSync('frontend/src/styles.css', 'utf8');

test('select component exposes proper label and error accessibility hooks', () => {
  assert.match(selectComponent, /useId/);
  assert.match(selectComponent, /htmlFor=\{selectId\}/);
  assert.match(selectComponent, /id=\{selectId\}/);
  assert.match(selectComponent, /aria-invalid=\{!!error \|\| undefined\}/);
  assert.match(selectComponent, /aria-describedby=\{error \? errorId : undefined\}/);
  assert.match(selectComponent, /aria-haspopup="listbox"/);
  assert.match(selectComponent, /aria-controls=\{isOpen \? listboxId : undefined\}/);
  assert.match(selectComponent, /id=\{errorId\}/);
});

test('select uses a stable chevron icon instead of inline malformed svg markup', () => {
  assert.match(selectComponent, /ChevronDown/);
  assert.doesNotMatch(selectComponent, /<svg xmlns=/);
  assert.doesNotMatch(selectComponent, /m6 9 6 6 6-9/);
});

test('select styles define trigger focus, menu layout, and disabled state', () => {
  assert.match(styles, /\.select-wrapper\s*\{[^}]*width:\s*100%;/s);
  assert.match(styles, /\.custom-select-trigger\s*\{[^}]*width:\s*100%;/s);
  assert.match(styles, /\.custom-select-trigger:focus-visible/);
  assert.match(styles, /\.select-arrow\s*\{[^}]*top:\s*50%;/s);
  assert.match(styles, /\.select-arrow\s*\{[^}]*transform:\s*translateY\(-50%\);/s);
  assert.match(styles, /\.custom-select-trigger:disabled/);
  assert.match(styles, /\.custom-select-menu\s*\{/);
});

test('select is implemented as a custom listbox instead of a native select element', () => {
  assert.match(selectComponent, /createPortal/);
  assert.match(selectComponent, /useState/);
  assert.match(selectComponent, /useRef/);
  assert.match(selectComponent, /useEffect/);
  assert.match(selectComponent, /aria-haspopup="listbox"/);
  assert.match(selectComponent, /aria-expanded=\{isOpen\}/);
  assert.match(selectComponent, /role="listbox"/);
  assert.match(selectComponent, /role="option"/);
  assert.match(selectComponent, /onKeyDown=\{handleTriggerKeyDown\}/);
  assert.doesNotMatch(selectComponent, /<select/);
});

test('custom select styles define a trigger, popover menu, and option states', () => {
  assert.match(styles, /\.custom-select-trigger\s*\{/);
  assert.match(styles, /\.custom-select-menu\s*\{/);
  assert.match(styles, /\.custom-select-option\s*\{/);
  assert.match(styles, /\.custom-select-option\.selected\s*\{/);
  assert.match(styles, /\.custom-select-option:hover\s*\{/);
  assert.match(styles, /\.select-wrapper\.open \.select-arrow\s*\{/);
});
