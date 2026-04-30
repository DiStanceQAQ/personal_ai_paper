import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const app = readFileSync('frontend/src/App.tsx', 'utf8');
const inspector = readFileSync('frontend/src/components/layout/Inspector.tsx', 'utf8');
const usePapers = readFileSync('frontend/src/hooks/usePapers.ts', 'utf8');
const styles = readFileSync('frontend/src/styles.css', 'utf8');
const api = readFileSync('frontend/src/api.ts', 'utf8');

test('app no longer renders a blocking loading overlay for background analysis', () => {
  assert.doesNotMatch(app, /<LoadingOverlay/);
  assert.match(app, /isVisible=\{!!notice/);
});

test('paper hook starts AI parse work in background instead of synchronously waiting for completion', () => {
  assert.match(usePapers, /phase: 'parsing'/);
  assert.match(usePapers, /phase: 'analyzing'/);
  assert.match(usePapers, /已在后台提交 PDF 解析任务/);
  assert.match(usePapers, /已在后台启动 AI 深度分析/);
  assert.match(usePapers, /api\.createAnalysisRun\(paperId\)/);
  assert.doesNotMatch(usePapers, /await waitForParseRunCompletion\(/);
});

test('paper hook renders analysis batch progress from run diagnostics', () => {
  assert.match(usePapers, /parseAnalysisRunProgress/);
  assert.match(usePapers, /diagnostics\.progress/);
  assert.match(usePapers, /completed_batches/);
  assert.match(usePapers, /AI 深度分析正在处理第/);
});

test('frontend uses paper-scoped cards and analysis cancellation APIs', () => {
  assert.match(api, /\/api\/papers\/\$\{paperId\}\/cards/);
  assert.match(api, /cancelAnalysisRun/);
  assert.doesNotMatch(api, /\/api\/agent\/analyze/);
  assert.doesNotMatch(api, /`\/api\/cards/);
  assert.match(inspector, /onCancelAnalysis/);
  assert.match(styles, /\.task-progress-card\.cancelled/);
});

test('inspector renders an inline task progress panel instead of relying on a modal blocker', () => {
  assert.match(inspector, /analysisTask/);
  assert.match(inspector, /visibleAnalysisTask/);
  assert.match(inspector, /analysisTask\?\.phase === 'completed' \? null : analysisTask/);
  assert.match(inspector, /task-progress-card/);
  assert.match(styles, /\.task-progress-card/);
  assert.match(styles, /\.task-progress-bar/);
  assert.match(styles, /\.task-progress-fill/);
});
