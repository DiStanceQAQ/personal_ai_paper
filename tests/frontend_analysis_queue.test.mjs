import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const usePapers = readFileSync('frontend/src/hooks/usePapers.ts', 'utf8');

test('paper polling keeps refreshing while queued parses are still pending', () => {
  assert.match(
    usePapers,
    /const hasParsingPaper = papers\.some\(p => p\.parse_status === 'pending' \|\| p\.parse_status === 'parsing'\);/,
  );
});

test('deep analysis waits for an active parse run before calling the analysis endpoint', () => {
  assert.match(usePapers, /function findLatestActiveParseRun/);
  assert.match(usePapers, /run\.status === 'queued' \|\| run\.status === 'running'/);
  assert.match(
    usePapers,
    /setBackgroundTask\(paperId,\s*\{\s*phase: 'parsing'/s,
  );
  assert.match(
    usePapers,
    /setNotice\(\{ message: '已在后台提交 PDF 解析任务。', type: 'success' \}\);/,
  );
  assert.match(
    usePapers,
    /void startBackgroundAnalysis\(paperId\);/,
  );
});
