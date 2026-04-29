import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const app = readFileSync('frontend/src/App.tsx', 'utf8');
const workspace = readFileSync('frontend/src/components/layout/Workspace.tsx', 'utf8');
const styles = readFileSync('frontend/src/styles.css', 'utf8');
const types = readFileSync('frontend/src/types.ts', 'utf8');

test('search has explicit UI states and disabled empty-query feedback', () => {
  assert.match(types, /export type SearchStatus = 'idle' \| 'loading' \| 'success' \| 'empty' \| 'error';/);
  assert.match(app, /const \[searchStatus, setSearchStatus\]/);
  assert.match(app, /setSearchStatus\('loading'\)/);
  assert.match(app, /setSearchStatus\(searchResults\.length > 0 \? 'success' : 'empty'\)/);
  assert.match(app, /setSearchStatus\('error'\)/);
  assert.match(workspace, /searchStatus: SearchStatus;/);
  assert.match(workspace, /const canSearch = query\.trim\(\)\.length > 0 && searchStatus !== 'loading';/);
  assert.match(workspace, /disabled=\{!canSearch\}/);

  for (const state of ['idle', 'loading', 'empty', 'error']) {
    assert.match(workspace, new RegExp(`searchStatus === '${state}'`));
  }

  assert.match(styles, /\.search-state/);
  assert.match(styles, /\.btn-search-main:disabled/);
});

test('AI extraction action uses quiet tool styling instead of dominant animated CTA', () => {
  const aiButtonBlock = styles.match(/\.btn-ai-extract\s*\{[^}]+\}/s);
  assert.ok(aiButtonBlock, 'missing .btn-ai-extract rule');
  assert.doesNotMatch(aiButtonBlock[0], /linear-gradient/);
  assert.doesNotMatch(aiButtonBlock[0], /color:\s*white/);
  assert.doesNotMatch(aiButtonBlock[0], /height:\s*52px/);
  assert.doesNotMatch(styles, /\.btn-ai-extract svg\s*\{[^}]*animation:/s);
  assert.doesNotMatch(styles, /@keyframes pulse-icon/);
});

test('library empty state is a dedicated centered panel instead of a grid item', () => {
  assert.match(workspace, /papers\.length > 0 \? \(/);
  assert.match(workspace, /className="library-empty-state"/);
  assert.match(styles, /\.library-empty-state\s*\{[^}]*min-height:\s*220px;/s);
  assert.match(styles, /\.library-empty-state\s*\{[^}]*display:\s*flex;/s);
  assert.match(styles, /\.library-empty-state\s*\{[^}]*justify-content:\s*center;/s);
  assert.match(styles, /\.library-empty-state\s*\{[^}]*border:\s*1px dashed var\(--border\);/s);
});
