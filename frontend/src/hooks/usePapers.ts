import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import type {
  AgentStatus,
  AnalysisRun,
  EvidenceBackedUnderstandingField,
  EmbeddingRun,
  KnowledgeCard,
  PaperBackgroundTask,
  Paper,
  PaperParseDiagnostics,
  PaperUnderstandingZh,
  ParsePaperResponse,
  ParseRun,
  ParseRunProgress,
  ParseRunProgressStep,
  Passage,
  UploadQueueItem,
} from '../types';

const PARSE_POLL_INTERVAL_MS = 1000;
const BACKGROUND_TASK_POLL_MS = 2000;
const UPLOAD_QUEUE_RESET_MS = 8000;
const PARSE_PROGRESS_STEPS: ParseRunProgressStep[] = [
  { stage: 'queued', label: '等待 worker', progress: 8 },
  { stage: 'claimed', label: 'worker 已接收', progress: 10 },
  { stage: 'checking_file', label: '检查文件', progress: 12 },
  { stage: 'loading_backend', label: '加载解析器', progress: 20 },
  { stage: 'inspecting_pdf', label: '检查结构', progress: 28 },
  { stage: 'parsing_layout', label: '解析版面', progress: 38 },
  { stage: 'chunking', label: '切分片段', progress: 64 },
  { stage: 'persisting', label: '保存结果', progress: 78 },
  { stage: 'queueing_embedding', label: '提交索引', progress: 90 },
  { stage: 'completed', label: '完成', progress: 100 },
  { stage: 'failed', label: '失败', progress: 100 },
];

interface PaperParseSummary {
  diagnostics?: PaperParseDiagnostics;
  trackedRun?: ParseRun;
}

function parseWarnings(warningsJson: string | undefined): string[] {
  if (!warningsJson) return [];

  try {
    const parsed = JSON.parse(warningsJson);
    return Array.isArray(parsed)
      ? parsed.filter((warning): warning is string => typeof warning === 'string')
      : [];
  } catch {
    return [];
  }
}

function diagnosticsFromRun(
  run: ParseRun | undefined,
  counts: { passageCount?: number | null; tableCount?: number | null } = {},
): PaperParseDiagnostics | undefined {
  if (!run && counts.passageCount == null && counts.tableCount == null) {
    return undefined;
  }

  return {
    parser_backend: run?.backend || null,
    quality_score: run?.quality_score ?? null,
    warning_count: parseWarnings(run?.warnings_json).length,
    passage_count: counts.passageCount ?? null,
    table_count: counts.tableCount ?? null,
    last_parse_time: run?.completed_at || run?.started_at || run?.created_at || null,
  };
}

function diagnosticsFromParseResponse(response: ParsePaperResponse): PaperParseDiagnostics {
  return {
    parser_backend: response.backend,
    quality_score: response.quality_score,
    warning_count: response.warnings.length,
    passage_count: response.passage_count,
    table_count: null,
    last_parse_time: null,
  };
}

function mergeDiagnostics(
  primary: PaperParseDiagnostics | undefined,
  fallback: PaperParseDiagnostics | undefined,
): PaperParseDiagnostics | undefined {
  if (!primary) return fallback;
  if (!fallback) return primary;

  const sameRun =
    primary.last_parse_time === fallback.last_parse_time &&
    primary.parser_backend === fallback.parser_backend;

  return {
    ...primary,
    passage_count: primary.passage_count ?? (sameRun ? fallback.passage_count : null),
    table_count: primary.table_count ?? (sameRun ? fallback.table_count : null),
  };
}

function isEmbeddingActive(paper: Paper): boolean {
  return paper.embedding_status === 'pending' || paper.embedding_status === 'running';
}

function uploadQueueId(file: File, index: number): string {
  return `${Date.now()}-${index}-${file.name}-${file.size}`;
}

function attachDiagnostics(
  paper: Paper,
  diagnostics: PaperParseDiagnostics | undefined,
): Paper {
  return {
    ...paper,
    parse_diagnostics: diagnostics,
  };
}

function nonEmptyString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    : [];
}

function parseEvidenceBackedUnderstandingField(value: unknown): EvidenceBackedUnderstandingField | null {
  if (typeof value === 'string') {
    const text = value.trim();
    return text ? { text } : null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;

  const payload = value as Record<string, unknown>;
  const text = nonEmptyString(payload.text);
  if (!text) return null;
  return {
    text,
    source_passage_ids: stringList(payload.source_passage_ids),
    evidence_quote: nonEmptyString(payload.evidence_quote),
    reasoning_summary: nonEmptyString(payload.reasoning_summary),
  };
}

function parsePaperUnderstandingZh(run: AnalysisRun | undefined): PaperUnderstandingZh | null {
  if (!run?.metadata_json) return null;

  try {
    const metadata = JSON.parse(run.metadata_json) as Record<string, unknown>;
    const rawUnderstanding = metadata.paper_understanding_zh;
    if (!rawUnderstanding || typeof rawUnderstanding !== 'object' || Array.isArray(rawUnderstanding)) {
      return null;
    }

    const payload = rawUnderstanding as Record<string, unknown>;
    const understanding: PaperUnderstandingZh = {
      one_sentence: nonEmptyString(payload.one_sentence),
      problem: parseEvidenceBackedUnderstandingField(payload.problem),
      method: parseEvidenceBackedUnderstandingField(payload.method),
      results: parseEvidenceBackedUnderstandingField(payload.results),
      conclusion: parseEvidenceBackedUnderstandingField(payload.conclusion),
      limitations: parseEvidenceBackedUnderstandingField(payload.limitations),
      reusable_insights: stringList(payload.reusable_insights),
      source_passage_ids: stringList(payload.source_passage_ids),
      warnings: stringList(payload.warnings),
    };
    if (typeof payload.confidence === 'number' && Number.isFinite(payload.confidence)) {
      understanding.confidence = payload.confidence;
    }
    if (payload.metadata && typeof payload.metadata === 'object' && !Array.isArray(payload.metadata)) {
      understanding.metadata = payload.metadata as Record<string, unknown>;
    }

    return Object.values(understanding).some((value) =>
      typeof value === 'string' ? value.length > 0 : Array.isArray(value) ? value.length > 0 : Boolean(value),
    )
      ? understanding
      : null;
  } catch {
    return null;
  }
}

function attachPaperUnderstanding(
  paper: Paper,
  analysisRun: AnalysisRun | undefined,
): Paper {
  return {
    ...paper,
    ai_understanding_zh: parsePaperUnderstandingZh(analysisRun),
  };
}

async function loadPaperParseSummary(paper: Paper): Promise<PaperParseSummary> {
  try {
    const runs = await api.listParseRuns(paper.id);
    const latestRun = runs[0];
    const activeRun = findLatestActiveParseRun(runs);
    const failedRun = paper.parse_status === 'error' && latestRun?.status === 'failed'
      ? latestRun
      : undefined;

    return {
      diagnostics: diagnosticsFromRun(latestRun),
      trackedRun: activeRun || failedRun,
    };
  } catch (err) {
    console.error(`Failed to load parse diagnostics for ${paper.id}:`, err);
    return {};
  }
}

function findLatestActiveParseRun(runs: ParseRun[]): ParseRun | undefined {
  return runs.find((run) => run.status === 'queued' || run.status === 'running');
}

function parseRunFailureMessage(run: ParseRun): string {
  return run.last_error || 'PDF 解析失败，请检查解析配置或稍后重试。';
}

function findLatestActiveAnalysisRun(runs: AnalysisRun[]): AnalysisRun | undefined {
  return runs.find((run) => run.status === 'queued' || run.status === 'running');
}

function analysisRunFailureMessage(run: AnalysisRun): string {
  return run.last_error || 'AI 深度分析失败，请检查模型配置后重试。';
}

interface AnalysisRunProgress {
  stage: string;
  total_batches: number;
  completed_batches: number;
  current_batch_index: number | null;
  accepted_card_count: number;
  rejected_card_count: number;
}

function numericProgressValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function fallbackParseStage(status: string): string {
  if (status === 'queued') return 'queued';
  if (status === 'running') return 'parsing_layout';
  if (status === 'completed') return 'completed';
  if (status === 'failed') return 'failed';
  return status;
}

function fallbackParseRunProgress(run: ParseRun): ParseRunProgress {
  const stage = fallbackParseStage(run.status);
  return {
    stage,
    label: parseRunStageLabel(stage, run.status),
    progress: fallbackParseProgress(run.status),
    details: {},
    steps: PARSE_PROGRESS_STEPS,
  };
}

function parseRunProgress(run: ParseRun): ParseRunProgress {
  if (!run.metadata_json) return fallbackParseRunProgress(run);

  try {
    const metadata = JSON.parse(run.metadata_json) as Record<string, unknown>;
    const rawProgress = metadata.progress;
    if (!rawProgress || typeof rawProgress !== 'object') return fallbackParseRunProgress(run);

    const progress = rawProgress as Record<string, unknown>;
    const rawProgressValue = numericProgressValue(progress.progress);
    const stage = typeof progress.stage === 'string' ? progress.stage : run.status;
    const label = typeof progress.label === 'string'
      ? progress.label
      : parseRunStageLabel(stage, run.status);
    const details = progress.details && typeof progress.details === 'object' && !Array.isArray(progress.details)
      ? progress.details as Record<string, unknown>
      : {};

    return {
      stage,
      label,
      progress: Math.min(100, Math.max(0, Math.round(rawProgressValue ?? fallbackParseProgress(run.status)))),
      details,
      steps: PARSE_PROGRESS_STEPS,
    };
  } catch {
    return fallbackParseRunProgress(run);
  }
}

function fallbackParseProgress(status: string): number {
  if (status === 'queued') return 8;
  if (status === 'running') return 38;
  if (status === 'completed') return 100;
  if (status === 'failed') return 100;
  return 18;
}

function parseRunStageLabel(stage: string, status: string): string {
  const matched = PARSE_PROGRESS_STEPS.find((step) => step.stage === stage);
  if (matched) return matched.label;
  if (status === 'queued') return '等待后台解析';
  if (status === 'running') return 'PDF 解析正在后台运行';
  if (status === 'completed') return 'PDF 解析完成';
  if (status === 'failed') return 'PDF 解析失败';
  return 'PDF 解析任务进行中';
}

function parseRunProgressMessage(run: ParseRun): string {
  const progress = parseRunProgress(run);
  if (run.status === 'queued') {
    return `${progress.label}，等待后台 worker 处理...`;
  }
  if (run.status === 'running') {
    const details = progress.details || {};
    const suffixes = [
      typeof details.backend === 'string' ? `解析器：${details.backend}` : '',
      typeof details.page_count === 'number' ? `页数：${details.page_count}` : '',
      typeof details.passage_count === 'number' ? `片段：${details.passage_count}` : '',
      typeof details.element_count === 'number' ? `元素：${details.element_count}` : '',
    ].filter(Boolean);
    const suffix = suffixes.length > 0 ? ` (${suffixes.join('，')})` : '';
    if (progress.stage === 'claimed') {
      return `${progress.label}，准备开始解析${suffix}...`;
    }
    if (progress.stage === 'queueing_embedding') {
      return `${progress.label}${suffix}...`;
    }
    return `${progress.label}中${suffix}...`;
  }
  if (run.status === 'completed') return 'PDF 解析完成，准备执行 AI 深度分析...';
  if (run.status === 'failed') {
    const failedAfterLabel = progress.details.failed_after_label;
    return typeof failedAfterLabel === 'string' && failedAfterLabel
      ? `PDF 解析失败，最后停在：${failedAfterLabel}。`
      : parseRunFailureMessage(run);
  }
  return 'PDF 解析任务进行中...';
}

function taskFromParseRun(
  run: ParseRun,
  analysisRunId: string | null = null,
): Omit<PaperBackgroundTask, 'paper_id'> {
  const progress = parseRunProgress(run);
  if (run.status === 'failed') {
    return {
      phase: 'failed',
      progress: 100,
      message: parseRunProgressMessage(run),
      parse_run_id: run.id,
      analysis_run_id: analysisRunId,
      error_detail: parseRunFailureMessage(run),
      parse_progress: progress,
    };
  }

  return {
    phase: 'parsing',
    progress: progress.progress,
    message: parseRunProgressMessage(run),
    parse_run_id: run.id,
    analysis_run_id: analysisRunId,
    error_detail: null,
    parse_progress: progress,
  };
}

function parseAnalysisRunProgress(run: AnalysisRun): AnalysisRunProgress | null {
  if (!run.diagnostics_json) return null;

  try {
    const diagnostics = JSON.parse(run.diagnostics_json) as Record<string, unknown>;
    const rawProgress = diagnostics.progress;
    if (!rawProgress || typeof rawProgress !== 'object') return null;

    const progress = rawProgress as Record<string, unknown>;
    return {
      stage: typeof progress.stage === 'string' ? progress.stage : '',
      total_batches: numericProgressValue(progress.total_batches) ?? 0,
      completed_batches: numericProgressValue(progress.completed_batches) ?? 0,
      current_batch_index: numericProgressValue(progress.current_batch_index),
      accepted_card_count: numericProgressValue(progress.accepted_card_count) ?? 0,
      rejected_card_count: numericProgressValue(progress.rejected_card_count) ?? 0,
    };
  } catch {
    return null;
  }
}

function analysisRunProgressPercent(run: AnalysisRun, progress: AnalysisRunProgress | null): number {
  if (run.status === 'queued') return 82;
  if (!progress) return 92;
  if (progress.stage === 'metadata') return 84;
  if (progress.stage === 'understanding') return 88;
  if (progress.stage === 'derive_cards') return 94;
  if (progress.stage === 'persisting') return 98;
  if (progress.stage === 'ranking') return 98;
  if (progress.total_batches <= 0) return 92;

  const completedRatio = Math.min(
    1,
    Math.max(0, progress.completed_batches / progress.total_batches),
  );
  return Math.min(98, Math.max(84, Math.round(84 + completedRatio * 14)));
}

function analysisRunProgressMessage(
  run: AnalysisRun,
  progress: AnalysisRunProgress | null,
): string {
  if (run.status === 'queued') {
    return 'AI 分析任务已进入队列，等待后台处理...';
  }
  if (!progress) return 'AI 深度分析正在后台运行...';
  if (progress.stage === 'metadata') return 'AI 深度分析正在识别论文元数据...';
  if (progress.stage === 'understanding') return 'AI 正在建立整篇论文的中文理解...';
  if (progress.stage === 'derive_cards') return 'AI 正在生成论文级知识卡片...';
  if (progress.stage === 'persisting') return 'AI 正在保存论文理解和知识卡片...';

  const total = progress.total_batches;
  if (progress.stage === 'ranking' && total > 0) {
    return `AI 深度分析正在去重排序，已完成 ${total}/${total} 批。`;
  }
  if (total <= 0) return 'AI 深度分析正在后台运行...';

  const completed = Math.min(progress.completed_batches, total);
  if (completed >= total) {
    return `AI 深度分析已完成 ${total}/${total} 批，正在整理结果...`;
  }

  const currentBatch = progress.current_batch_index == null
    ? completed + 1
    : progress.current_batch_index + 1;
  const safeCurrentBatch = Math.min(Math.max(currentBatch, 1), total);
  const candidateCount = run.accepted_card_count || progress.accepted_card_count;
  const rejectedCount = run.rejected_card_count || progress.rejected_card_count;
  const candidateText = candidateCount > 0 ? `，已得到 ${candidateCount} 张候选卡` : '';
  const rejectedText = rejectedCount > 0 ? `，${rejectedCount} 条候选被拒绝` : '';
  return `AI 深度分析正在处理第 ${safeCurrentBatch}/${total} 批，已完成 ${completed} 批${candidateText}${rejectedText}。`;
}

function taskFromAnalysisRun(
  run: AnalysisRun,
  parseRunId: string | null = null,
): Omit<PaperBackgroundTask, 'paper_id'> {
  if (run.status === 'completed') {
    return {
      phase: 'completed',
      progress: 100,
      message: `AI 深度分析完成，生成 ${run.accepted_card_count} 张卡片。`,
      parse_run_id: parseRunId,
      analysis_run_id: run.id,
      error_detail: null,
    };
  }

  if (run.status === 'failed' || run.status === 'cancelled') {
    return {
      phase: run.status === 'cancelled' ? 'cancelled' : 'failed',
      progress: 100,
      message: run.status === 'cancelled' ? 'AI 深度分析已取消。' : 'AI 深度分析失败。',
      parse_run_id: parseRunId,
      analysis_run_id: run.id,
      error_detail: run.status === 'cancelled' ? null : analysisRunFailureMessage(run),
    };
  }

  const progress = parseAnalysisRunProgress(run);
  return {
    phase: 'analyzing',
    progress: analysisRunProgressPercent(run, progress),
    message: analysisRunProgressMessage(run, progress),
    parse_run_id: parseRunId,
    analysis_run_id: run.id,
    error_detail: null,
  };
}

export function usePapers(
  activeSpaceId: string | undefined,
  setNotice: (n: { message: string, type: 'success' | 'error' } | null) => void,
  setIsProcessing: (b: boolean) => void
) {
  const [papers, setPapers] = useState<Paper[]>([]);
  const [selectedPaper, setSelectedPaper] = useState<Paper | null>(null);
  const [passages, setPassages] = useState<Passage[]>([]);
  const [cards, setCards] = useState<KnowledgeCard[]>([]);
  const [agentStatus, setAgentStatus] = useState<AgentStatus | null>(null);
  const [backgroundTasks, setBackgroundTasks] = useState<Record<string, PaperBackgroundTask>>({});
  const [uploadQueue, setUploadQueue] = useState<UploadQueueItem[]>([]);
  const [embeddingRunsByPaperId, setEmbeddingRunsByPaperId] = useState<Record<string, EmbeddingRun | null>>({});
  
  const pollingRef = useRef<number | null>(null);
  const backgroundPollingRef = useRef<number | null>(null);
  const uploadQueueResetRef = useRef<number | null>(null);
  const backgroundTasksRef = useRef<Record<string, PaperBackgroundTask>>({});
  const monitorInFlightRef = useRef(false);
  const analysisInFlightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    backgroundTasksRef.current = backgroundTasks;
  }, [backgroundTasks]);

  useEffect(() => {
    setSelectedPaper(null);
    setPassages([]);
    setCards([]);
    setUploadQueue([]);
    setEmbeddingRunsByPaperId({});
  }, [activeSpaceId]);

  useEffect(() => {
    return () => {
      if (uploadQueueResetRef.current) {
        window.clearTimeout(uploadQueueResetRef.current);
        uploadQueueResetRef.current = null;
      }
    };
  }, []);

  const loadPapers = useCallback(async () => {
    if (!activeSpaceId) {
      setPapers([]);
      setAgentStatus(null);
      return;
    }
    try {
      const [loadedPapers, status] = await Promise.all([
        api.listPapers(),
        api.agentStatus(),
      ]);

      const parseSummaryEntries = await Promise.all(
        loadedPapers.map(async (paper) => [paper.id, await loadPaperParseSummary(paper)] as const),
      );
      const parseSummaryByPaperId = new Map(parseSummaryEntries);
      const papersWithDiagnostics = loadedPapers.map((paper) =>
        attachDiagnostics(paper, parseSummaryByPaperId.get(paper.id)?.diagnostics),
      );

      setPapers(papersWithDiagnostics);
      setBackgroundTasks((current) => {
        const paperIds = new Set(papersWithDiagnostics.map((paper) => paper.id));
        const next: Record<string, PaperBackgroundTask> = {};
        for (const [paperId, task] of Object.entries(current)) {
          if (paperIds.has(paperId)) next[paperId] = task;
        }
        for (const paper of papersWithDiagnostics) {
          const run = parseSummaryByPaperId.get(paper.id)?.trackedRun;
          const existing = next[paper.id];
          const canRecoverParseTask =
            !existing ||
            existing.phase === 'parsing' ||
            (existing.phase === 'failed' && existing.parse_run_id === run?.id);
          if (run && canRecoverParseTask) {
            next[paper.id] = {
              paper_id: paper.id,
              ...taskFromParseRun(run, existing?.analysis_run_id || null),
            };
          }
        }
        return next;
      });
      setEmbeddingRunsByPaperId((current) => {
        const paperIds = new Set(papersWithDiagnostics.map((paper) => paper.id));
        const next: Record<string, EmbeddingRun | null> = {};
        for (const [paperId, run] of Object.entries(current)) {
          if (paperIds.has(paperId)) next[paperId] = run;
        }
        return next;
      });
      setSelectedPaper((current) => {
        if (!current) return null;
        const refreshedPaper = papersWithDiagnostics.find((paper) => paper.id === current.id);
        const paperWithDiagnostics = refreshedPaper
          ? attachDiagnostics(
              refreshedPaper,
              mergeDiagnostics(refreshedPaper.parse_diagnostics, current.parse_diagnostics),
            )
          : current;
        return {
          ...paperWithDiagnostics,
          ai_understanding_zh: current.ai_understanding_zh,
        };
      });
      setAgentStatus(status);
    } catch (err) {
      console.error('Failed to load papers:', err);
    }
  }, [activeSpaceId]);

  const setBackgroundTask = useCallback(
    (paperId: string, update: Omit<PaperBackgroundTask, 'paper_id'>) => {
      setBackgroundTasks((current) => ({
        ...current,
        [paperId]: {
          paper_id: paperId,
          ...update,
        },
      }));
    },
    [],
  );

  const clearUploadQueueLater = useCallback(() => {
    if (uploadQueueResetRef.current) {
      window.clearTimeout(uploadQueueResetRef.current);
    }
    uploadQueueResetRef.current = window.setTimeout(() => {
      setUploadQueue((current) =>
        current.some((item) => item.status === 'uploading') ? current : [],
      );
      uploadQueueResetRef.current = null;
    }, UPLOAD_QUEUE_RESET_MS);
  }, []);

  const loadEmbeddingRunSummary = useCallback(async (paperId: string) => {
    try {
      const runs = await api.listEmbeddingRuns(paperId);
      setEmbeddingRunsByPaperId((current) => ({
        ...current,
        [paperId]: runs[0] || null,
      }));
    } catch (err) {
      console.error(`Failed to load embedding runs for ${paperId}:`, err);
    }
  }, []);

  const refreshPaperDetails = useCallback(
    async (
      paperId: string,
      parseResult?: ParsePaperResponse,
    ) => {
      const [updatedPaper, paperCards, paperPassages, parseRuns, paperTables, analysisRuns] = await Promise.all([
        api.getPaper(paperId),
        api.listCards(paperId),
        api.listPassages(paperId),
        api.listParseRuns(paperId),
        api.listDocumentTables(paperId),
        api.listAnalysisRuns(paperId),
      ]);
      const diagnostics = mergeDiagnostics(
        diagnosticsFromRun(parseRuns[0], {
          passageCount: paperPassages.length,
          tableCount: paperTables.length,
        }),
        parseResult ? diagnosticsFromParseResponse(parseResult) : undefined,
      );
      const updatedPaperWithDiagnostics = attachPaperUnderstanding(
        attachDiagnostics(updatedPaper, diagnostics),
        analysisRuns[0],
      );

      if (selectedPaper?.id === paperId) {
        setSelectedPaper(updatedPaperWithDiagnostics);
        setPassages(paperPassages);
        setCards(paperCards);
      }
      setPapers((current) =>
        current.map((paper) =>
          paper.id === paperId
            ? attachDiagnostics(updatedPaperWithDiagnostics, mergeDiagnostics(diagnostics, paper.parse_diagnostics))
            : paper,
        ),
      );
      if (analysisRuns[0]) {
        setBackgroundTask(
          paperId,
          taskFromAnalysisRun(analysisRuns[0], backgroundTasksRef.current[paperId]?.parse_run_id || null),
        );
      }
      await loadEmbeddingRunSummary(paperId);
      await loadPapers();
    },
    [loadEmbeddingRunSummary, loadPapers, selectedPaper, setBackgroundTask],
  );

  const startBackgroundAnalysis = useCallback(
    async (paperId: string) => {
      if (analysisInFlightRef.current.has(paperId)) return;

      analysisInFlightRef.current.add(paperId);
      setBackgroundTask(paperId, {
        phase: 'analyzing',
        progress: 82,
        message: '正在提交 AI 深度分析任务...',
        parse_run_id: backgroundTasksRef.current[paperId]?.parse_run_id || null,
        analysis_run_id: backgroundTasksRef.current[paperId]?.analysis_run_id || null,
        error_detail: null,
      });
      try {
        const run = await api.createAnalysisRun(paperId);
        setBackgroundTask(
          paperId,
          taskFromAnalysisRun(run, backgroundTasksRef.current[paperId]?.parse_run_id || null),
        );
        setNotice({ message: 'AI 深度分析任务已提交，将在后台运行。', type: 'success' });
      } catch (err: any) {
        const detail = err.message || 'AI 深度分析失败，请检查模型配置。';
        setBackgroundTask(paperId, {
          phase: 'failed',
          progress: 100,
          message: 'AI 深度分析失败。',
          parse_run_id: backgroundTasksRef.current[paperId]?.parse_run_id || null,
          analysis_run_id: backgroundTasksRef.current[paperId]?.analysis_run_id || null,
          error_detail: detail,
        });
        setNotice({ message: `AI 解析失败: ${detail}`, type: 'error' });
      } finally {
        analysisInFlightRef.current.delete(paperId);
      }
    },
    [setBackgroundTask, setNotice],
  );

  // 状态轮询逻辑：如果有论文正在解析，每 3 秒刷新一次列表
  useEffect(() => {
    const selectedPaperId = selectedPaper?.id;
    if (selectedPaperId) {
      void loadEmbeddingRunSummary(selectedPaperId);
    }

    const hasParsingPaper = papers.some(p => p.parse_status === 'pending' || p.parse_status === 'parsing');
    const hasEmbeddingPaper = papers.some(isEmbeddingActive);
    const hasPollingPaper = hasParsingPaper || hasEmbeddingPaper;
    
    if (hasPollingPaper && !pollingRef.current) {
      pollingRef.current = window.setInterval(() => {
        void loadPapers();
        if (selectedPaperId) {
          void loadEmbeddingRunSummary(selectedPaperId);
        }
      }, 3000);
    } else if (!hasPollingPaper && pollingRef.current) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    return () => {
      if (pollingRef.current) {
        window.clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [papers, loadEmbeddingRunSummary, loadPapers, selectedPaper?.id]);

  const monitorBackgroundTasks = useCallback(async () => {
    if (monitorInFlightRef.current) return;
    monitorInFlightRef.current = true;
    try {
      const parseTasks = Object.values(backgroundTasksRef.current).filter(
        (task) => task.phase === 'parsing',
      );
      const analysisTasks = Object.values(backgroundTasksRef.current).filter(
        (task) => task.phase === 'analyzing',
      );
      if (parseTasks.length > 0 || analysisTasks.length > 0) {
        await loadPapers();
      }
      for (const task of parseTasks) {
        const runs = await api.listParseRuns(task.paper_id);
        const matchedRun = task.parse_run_id
          ? runs.find((run) => run.id === task.parse_run_id) || runs[0]
          : runs[0];

        if (!matchedRun) {
          setBackgroundTask(task.paper_id, {
            phase: 'failed',
            progress: 100,
            message: '未找到解析任务记录。',
            parse_run_id: task.parse_run_id,
            analysis_run_id: task.analysis_run_id || null,
            error_detail: '未找到解析任务记录。',
          });
          continue;
        }

        if (matchedRun.status === 'completed') {
          setBackgroundTask(task.paper_id, {
            phase: 'analyzing',
            progress: 76,
            message: 'PDF 解析完成，准备执行 AI 深度分析...',
            parse_run_id: matchedRun.id,
            analysis_run_id: task.analysis_run_id || null,
            error_detail: null,
            parse_progress: parseRunProgress(matchedRun),
          });
          void startBackgroundAnalysis(task.paper_id);
          continue;
        }

        if (matchedRun.status === 'failed') {
          const detail = parseRunFailureMessage(matchedRun);
          setBackgroundTask(
            task.paper_id,
            taskFromParseRun(matchedRun, task.analysis_run_id || null),
          );
          setNotice({ message: `PDF 解析失败: ${detail}`, type: 'error' });
          continue;
        }

        setBackgroundTask(
          task.paper_id,
          taskFromParseRun(matchedRun, task.analysis_run_id || null),
        );
      }

      for (const task of analysisTasks) {
        const runs = await api.listAnalysisRuns(task.paper_id);
        const matchedRun = task.analysis_run_id
          ? runs.find((run) => run.id === task.analysis_run_id) || runs[0]
          : runs[0];

        if (!matchedRun) {
          setBackgroundTask(task.paper_id, {
            phase: 'failed',
            progress: 100,
            message: '未找到 AI 分析任务记录。',
            parse_run_id: task.parse_run_id,
            analysis_run_id: task.analysis_run_id || null,
            error_detail: '未找到 AI 分析任务记录。',
          });
          continue;
        }

        setBackgroundTask(
          task.paper_id,
          taskFromAnalysisRun(matchedRun, task.parse_run_id),
        );

        if (matchedRun.status === 'completed') {
          setNotice({
            message: `AI 解析成功！提取了 ${matchedRun.accepted_card_count} 张卡片。`,
            type: 'success',
          });
          await refreshPaperDetails(task.paper_id);
        }

        if (matchedRun.status === 'failed') {
          setNotice({
            message: `AI 解析失败: ${analysisRunFailureMessage(matchedRun)}`,
            type: 'error',
          });
        }
        if (matchedRun.status === 'cancelled') {
          setNotice({ message: 'AI 深度分析已取消。', type: 'success' });
          await refreshPaperDetails(task.paper_id);
        }
      }
    } finally {
      monitorInFlightRef.current = false;
    }
  }, [loadPapers, refreshPaperDetails, setBackgroundTask, setNotice, startBackgroundAnalysis]);

  useEffect(() => {
    const hasActiveBackgroundTask = Object.values(backgroundTasks).some(
      (task) => task.phase === 'parsing' || task.phase === 'analyzing',
    );

    if (hasActiveBackgroundTask && !backgroundPollingRef.current) {
      backgroundPollingRef.current = window.setInterval(() => {
        void monitorBackgroundTasks();
      }, BACKGROUND_TASK_POLL_MS);
    } else if (!hasActiveBackgroundTask && backgroundPollingRef.current) {
      window.clearInterval(backgroundPollingRef.current);
      backgroundPollingRef.current = null;
    }

    return () => {
      if (backgroundPollingRef.current) {
        window.clearInterval(backgroundPollingRef.current);
        backgroundPollingRef.current = null;
      }
    };
  }, [backgroundTasks, monitorBackgroundTasks]);

  const openPaper = async (paper: Paper) => {
    setSelectedPaper(paper);
    try {
      const [paperPassages, paperCards, parseRuns, paperTables, analysisRuns] = await Promise.all([
        api.listPassages(paper.id),
        api.listCards(paper.id),
        api.listParseRuns(paper.id),
        api.listDocumentTables(paper.id),
        api.listAnalysisRuns(paper.id),
      ]);
      const diagnostics = diagnosticsFromRun(parseRuns[0], {
        passageCount: paperPassages.length,
        tableCount: paperTables.length,
      });
      const selectedWithDiagnostics = attachPaperUnderstanding(
        attachDiagnostics(
          paper,
          mergeDiagnostics(diagnostics, paper.parse_diagnostics),
        ),
        analysisRuns[0],
      );

      setSelectedPaper(selectedWithDiagnostics);
      setPassages(paperPassages);
      setCards(paperCards);
      setPapers((current) =>
        current.map((item) =>
          item.id === paper.id
            ? attachDiagnostics(item, mergeDiagnostics(diagnostics, item.parse_diagnostics))
            : item,
        ),
      );
      if (analysisRuns[0]) {
        setBackgroundTask(
          paper.id,
          taskFromAnalysisRun(
            analysisRuns[0],
            backgroundTasksRef.current[paper.id]?.parse_run_id || null,
          ),
        );
      }
      await loadEmbeddingRunSummary(paper.id);
    } catch {
      setNotice({ message: '获取论文详情失败。', type: 'error' });
    }
  };

  const deletePaper = async (paperId: string) => {
    try {
      await api.deletePaper(paperId);
      if (selectedPaper?.id === paperId) {
        setSelectedPaper(null);
        setPassages([]);
        setCards([]);
      }
      setNotice({ message: '论文已从库中移除。', type: 'success' });
      await loadPapers();
      return true;
    } catch {
      setNotice({ message: '移除论文失败。', type: 'error' });
      return false;
    }
  };

  const uploadPaper = async (files: File | File[]) => {
    const selectedFiles = Array.isArray(files) ? files : [files];
    if (selectedFiles.length === 0) return;

    if (uploadQueueResetRef.current) {
      window.clearTimeout(uploadQueueResetRef.current);
      uploadQueueResetRef.current = null;
    }
    const queuedItems = selectedFiles.map<UploadQueueItem>((file, index) => ({
      id: uploadQueueId(file, index),
      filename: file.name,
      status: 'uploading',
    }));

    setUploadQueue(queuedItems);
    setIsProcessing(true);
    setNotice({
      message:
        selectedFiles.length === 1
          ? '正在导入 PDF 文件...'
          : `正在批量导入 ${selectedFiles.length} 个 PDF 文件...`,
      type: 'success',
    });
    try {
      if (selectedFiles.length === 1) {
        const paper = await api.uploadPaper(selectedFiles[0]);
        setUploadQueue((current) =>
          current.map((item) =>
            item.id === queuedItems[0].id
              ? {
                  ...item,
                  status: 'success',
                  paper_id: paper.id,
                  title: paper.title || item.filename,
                }
              : item,
          ),
        );
        setNotice({ message: '导入成功。', type: 'success' });
      } else {
        const result = await api.uploadPapersBatch(selectedFiles);
        setUploadQueue(
          queuedItems.map((item, index) => {
            const uploadResult = result.results[index];
            if (!uploadResult) {
              return {
                ...item,
                status: 'failed',
                error: '后端未返回该文件的导入结果。',
              };
            }
            if (uploadResult.status === 'success' && uploadResult.paper) {
              return {
                ...item,
                status: 'success',
                paper_id: uploadResult.paper.id,
                title: uploadResult.paper.title || uploadResult.filename,
              };
            }
            return {
              ...item,
              status: 'failed',
              error: uploadResult.error || '导入失败。',
            };
          }),
        );
        setNotice({
          message:
            result.failed === 0
              ? `批量导入成功：${result.succeeded}/${result.total}。`
              : `批量导入完成：成功 ${result.succeeded}，失败 ${result.failed}。`,
          type: result.succeeded > 0 ? 'success' : 'error',
        });
      }
      await loadPapers();
    } catch (err: any) {
      const detail = err.message || '文件导入失败。';
      setUploadQueue((current) =>
        current.map((item) =>
          item.status === 'uploading'
            ? { ...item, status: 'failed', error: detail }
            : item,
        ),
      );
      setNotice({ message: detail, type: 'error' });
    } finally {
      setIsProcessing(false);
      clearUploadQueueLater();
    }
  };

  const runDeepAnalysis = async (paperId: string) => {
    try {
      const currentPaper =
        papers.find((paper) => paper.id === paperId) ||
        (selectedPaper?.id === paperId ? selectedPaper : null);
      const existingRuns = await api.listParseRuns(paperId);
      const existingActiveRun = findLatestActiveParseRun(existingRuns);

      if (existingActiveRun) {
        setBackgroundTask(paperId, taskFromParseRun(existingActiveRun));
        setNotice({ message: '已在后台继续等待 PDF 解析完成。', type: 'success' });
        return;
      }

      if (currentPaper?.parse_status === 'parsed') {
        const analysisRuns = await api.listAnalysisRuns(paperId);
        const activeAnalysisRun = findLatestActiveAnalysisRun(analysisRuns);
        if (activeAnalysisRun) {
          setBackgroundTask(
            paperId,
            taskFromAnalysisRun(activeAnalysisRun, backgroundTasksRef.current[paperId]?.parse_run_id || null),
          );
          setNotice({ message: '已在后台继续等待 AI 深度分析完成。', type: 'success' });
          return;
        }

        setBackgroundTask(paperId, {
          phase: 'analyzing',
          progress: 82,
          message: '已在后台启动 AI 深度分析。',
          parse_run_id: null,
          analysis_run_id: null,
          error_detail: null,
        });
        setNotice({ message: '已在后台启动 AI 深度分析。', type: 'success' });
        void startBackgroundAnalysis(paperId);
        return;
      }

      const parseResult = await api.parsePaper(paperId);
      setBackgroundTask(paperId, {
        phase: 'parsing',
        progress: 8,
        message: '已提交 PDF 解析任务，等待后台 worker 接收...',
        parse_run_id: parseResult.parse_run_id,
        analysis_run_id: null,
        error_detail: null,
        parse_progress: {
          stage: 'queued',
          label: '等待 worker',
          progress: 8,
          details: {},
          steps: PARSE_PROGRESS_STEPS,
        },
      });
      setNotice({ message: '已在后台提交 PDF 解析任务。', type: 'success' });
      await loadPapers();
    } catch (err: any) {
      setNotice({ message: `AI 解析失败: ${err.message || '请检查模型配置'}`, type: 'error' });
    }
  };

  const cancelAnalysisRun = async (paperId: string, runId: string) => {
    try {
      const run = await api.cancelAnalysisRun(paperId, runId);
      setBackgroundTask(
        paperId,
        taskFromAnalysisRun(run, backgroundTasksRef.current[paperId]?.parse_run_id || null),
      );
      setNotice({ message: 'AI 深度分析已取消。', type: 'success' });
      await refreshPaperDetails(paperId);
    } catch (err: any) {
      setNotice({ message: `取消 AI 分析失败: ${err.message || '请稍后重试'}`, type: 'error' });
    }
  };

  return {
    papers,
    selectedPaper,
    setSelectedPaper,
    passages,
    cards,
    setCards,
    agentStatus,
    setAgentStatus,
    loadPapers,
    openPaper,
    deletePaper,
    uploadPaper,
    runDeepAnalysis,
    cancelAnalysisRun,
    backgroundTasks,
    uploadQueue,
    embeddingRunsByPaperId,
  };
}
