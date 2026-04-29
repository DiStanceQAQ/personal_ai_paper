import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import type {
  AgentStatus,
  AnalysisRun,
  KnowledgeCard,
  PaperBackgroundTask,
  Paper,
  PaperParseDiagnostics,
  ParsePaperResponse,
  ParseRun,
  Passage,
} from '../types';

const PARSE_POLL_INTERVAL_MS = 1000;
const BACKGROUND_TASK_POLL_MS = 2000;

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

function attachDiagnostics(
  paper: Paper,
  diagnostics: PaperParseDiagnostics | undefined,
): Paper {
  return {
    ...paper,
    parse_diagnostics: diagnostics,
  };
}

async function loadPaperParseSummary(paper: Paper): Promise<PaperParseDiagnostics | undefined> {
  if (paper.parse_status === 'pending') return undefined;

  try {
    const runs = await api.listParseRuns(paper.id);
    return diagnosticsFromRun(runs[0]);
  } catch (err) {
    console.error(`Failed to load parse diagnostics for ${paper.id}:`, err);
    return undefined;
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
  
  const pollingRef = useRef<number | null>(null);
  const backgroundPollingRef = useRef<number | null>(null);
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
  }, [activeSpaceId]);

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

      const diagnosticsEntries = await Promise.all(
        loadedPapers.map(async (paper) => [paper.id, await loadPaperParseSummary(paper)] as const),
      );
      const diagnosticsByPaperId = new Map(diagnosticsEntries);
      const papersWithDiagnostics = loadedPapers.map((paper) =>
        attachDiagnostics(paper, diagnosticsByPaperId.get(paper.id)),
      );

      setPapers(papersWithDiagnostics);
      setSelectedPaper((current) => {
        if (!current) return null;
        const refreshedPaper = papersWithDiagnostics.find((paper) => paper.id === current.id);
        return refreshedPaper
          ? attachDiagnostics(
              refreshedPaper,
              mergeDiagnostics(refreshedPaper.parse_diagnostics, current.parse_diagnostics),
            )
          : current;
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
      const updatedPaperWithDiagnostics = attachDiagnostics(updatedPaper, diagnostics);

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
      await loadPapers();
    },
    [loadPapers, selectedPaper, setBackgroundTask],
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
    const hasParsingPaper = papers.some(p => p.parse_status === 'pending' || p.parse_status === 'parsing');
    
    if (hasParsingPaper && !pollingRef.current) {
      pollingRef.current = window.setInterval(() => {
        loadPapers();
      }, 3000);
    } else if (!hasParsingPaper && pollingRef.current) {
      window.clearInterval(pollingRef.current);
      pollingRef.current = null;
    }

    return () => {
      if (pollingRef.current) {
        window.clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [papers, loadPapers]);

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
          });
          void startBackgroundAnalysis(task.paper_id);
          continue;
        }

        if (matchedRun.status === 'failed') {
          const detail = parseRunFailureMessage(matchedRun);
          setBackgroundTask(task.paper_id, {
            phase: 'failed',
            progress: 100,
            message: 'PDF 解析失败。',
            parse_run_id: matchedRun.id,
            analysis_run_id: task.analysis_run_id || null,
            error_detail: detail,
          });
          setNotice({ message: `PDF 解析失败: ${detail}`, type: 'error' });
          continue;
        }

        setBackgroundTask(task.paper_id, {
          phase: 'parsing',
          progress: matchedRun.status === 'queued' ? 18 : 54,
          message: matchedRun.status === 'queued'
            ? '解析任务已进入队列，等待后台处理...'
            : 'PDF 解析正在后台运行...',
          parse_run_id: matchedRun.id,
          analysis_run_id: task.analysis_run_id || null,
          error_detail: null,
        });
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
      const selectedWithDiagnostics = attachDiagnostics(
        paper,
        mergeDiagnostics(diagnostics, paper.parse_diagnostics),
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

  const uploadPaper = async (file: File) => {
    setIsProcessing(true);
    setNotice({ message: '正在导入并预处理 PDF 文件...', type: 'success' });
    try {
      await api.uploadPaper(file);
      setNotice({ message: '导入成功。', type: 'success' });
      await loadPapers();
    } catch (err: any) {
      setNotice({ message: err.message || '文件导入失败。', type: 'error' });
    } finally {
      setIsProcessing(false);
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
        setBackgroundTask(paperId, {
          phase: 'parsing',
          progress: existingActiveRun.status === 'queued' ? 18 : 54,
          message: existingActiveRun.status === 'queued'
            ? '解析任务已进入队列，等待后台处理...'
            : 'PDF 解析正在后台运行...',
          parse_run_id: existingActiveRun.id,
          analysis_run_id: null,
          error_detail: null,
        });
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
        progress: 18,
        message: '已在后台提交 PDF 解析任务。',
        parse_run_id: parseResult.parse_run_id,
        analysis_run_id: null,
        error_detail: null,
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
  };
}
