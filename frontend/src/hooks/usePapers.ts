import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import { Paper, AgentStatus, Passage, KnowledgeCard } from '../types';

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
      setPapers(loadedPapers);
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
      const [paperPassages, paperCards] = await Promise.all([
        api.listPassages(paper.id),
        api.listCards(paper.id),
      ]);
      setPassages(paperPassages);
      setCards(paperCards);
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
      await api.parsePaper(paperId);
      setNotice({ message: '正在调用内置 Agent 进行深度语义分析...', type: 'success' });
      const result = await api.runDeepAnalysis(paperId);
      setNotice({ message: `AI 解析成功！识别了元数据并提取了 ${result.card_count} 张卡片。`, type: 'success' });
      
      const [updatedPaper, paperCards] = await Promise.all([
        api.getPaper(paperId),
        api.listCards(paperId),
      ]);
      
      if (selectedPaper?.id === paperId) {
        setSelectedPaper(updatedPaper);
        setCards(paperCards);
      }
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
