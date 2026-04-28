import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const app = readFileSync('frontend/src/App.tsx', 'utf8');
const sidebar = readFileSync('frontend/src/components/layout/Sidebar.tsx', 'utf8');
const inspector = readFileSync('frontend/src/components/layout/Inspector.tsx', 'utf8');
const styles = readFileSync('frontend/src/styles.css', 'utf8');

test('app shell collapse state is class-driven instead of inline grid styles', () => {
  assert.doesNotMatch(app, /style=\{\{\s*gridTemplateColumns/s);
  assert.match(app, /className=\{appShellClassName\}/);
  assert.match(app, /sidebar-collapsed/);
  assert.match(app, /inspector-collapsed/);
  assert.match(styles, /grid-template-columns:\s*var\(--sidebar-track\)\s+minmax\(0,\s*1fr\)\s+var\(--inspector-track\)/);
  assert.match(styles, /\.workspace\s*\{[^}]*min-width:\s*0;/s);
  assert.doesNotMatch(styles, /minmax\(500px,\s*1fr\)/);
});

test('rail toggle buttons expose semantic state and icon affordances', () => {
  for (const source of [sidebar, inspector]) {
    assert.match(source, /aria-label=/);
    assert.match(source, /aria-expanded=\{isOpen\}/);
    assert.doesNotMatch(source, />\s*[←→]\s*</);
  }

  assert.match(sidebar, /PanelLeft(?:Close|Open)/);
  assert.match(inspector, /PanelRight(?:Close|Open)/);
});

test('rail control CSS avoids hidden full-height traps and layout-unstable transitions', () => {
  assert.doesNotMatch(styles, /\.sidebar::after/);
  assert.doesNotMatch(styles, /\.inspector::after/);

  for (const selector of ['sidebar-toggle', 'inspector-toggle']) {
    const block = styles.match(new RegExp(`\\.${selector}\\s*\\{[^}]+\\}`, 's'));
    assert.ok(block, `missing .${selector} rule`);
    assert.doesNotMatch(block[0], /transition:\s*all\b/);
  }

  assert.match(styles, /\.sidebar-toggle:focus-visible/);
  assert.match(styles, /\.inspector-toggle:focus-visible/);
  assert.match(styles, /@media\s+\(prefers-reduced-motion:\s*reduce\)/);
});

test('inspector content layout is declared once', () => {
  assert.equal(styles.match(/^\.inspector-content\s*\{/gm)?.length, 1);
});
