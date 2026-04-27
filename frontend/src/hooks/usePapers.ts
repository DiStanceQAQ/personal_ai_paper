import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import type {
  AgentStatus,
  KnowledgeCard,
  Paper,
  PaperParseDiagnostics,
  ParsePaperResponse,
  ParseRun,
  Passage,
} from '../types';

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
  
  const pollingRef = useRef<number | null>(null);

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

  // 状态轮询逻辑：如果有论文正在解析，每 3 秒刷新一次列表
  useEffect(() => {
    const hasParsingPaper = papers.some(p => p.parse_status === 'parsing');
    
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

  const openPaper = async (paper: Paper) => {
    setSelectedPaper(paper);
    try {
      const [paperPassages, paperCards, parseRuns, paperTables] = await Promise.all([
        api.listPassages(paper.id),
        api.listCards(paper.id),
        api.listParseRuns(paper.id),
        api.listDocumentTables(paper.id),
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
    setIsProcessing(true);
    try {
      setNotice({ message: '正在进行 PDF 物理切片和 RAG 预处理...', type: 'success' });
      const parseResult = await api.parsePaper(paperId);
      setNotice({ message: '正在调用内置 Agent 进行深度语义分析...', type: 'success' });
      const result = await api.runDeepAnalysis(paperId);
      setNotice({ message: `AI 解析成功！识别了元数据并提取了 ${result.card_count} 张卡片。`, type: 'success' });
      
      const [updatedPaper, paperCards, paperPassages, parseRuns, paperTables] = await Promise.all([
        api.getPaper(paperId),
        api.listCards(paperId),
        api.listPassages(paperId),
        api.listParseRuns(paperId),
        api.listDocumentTables(paperId),
      ]);
      const diagnostics = mergeDiagnostics(
        diagnosticsFromRun(parseRuns[0], {
          passageCount: paperPassages.length,
          tableCount: paperTables.length,
        }),
        diagnosticsFromParseResponse(parseResult),
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
      await loadPapers();
    } catch (err: any) {
      setNotice({ message: `AI 解析失败: ${err.message || '请检查模型配置'}`, type: 'error' });
    } finally {
      setIsProcessing(false);
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
  };
}
