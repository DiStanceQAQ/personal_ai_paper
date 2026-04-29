import { useState, useCallback } from 'react';
import { api } from '../api';
import type { AgentConfig, MinerUTestResult } from '../types';

export function useLlmConfig(setNotice: (n: { message: string, type: 'success' | 'error' } | null) => void) {
  const [llmConfig, setLlmConfig] = useState<AgentConfig>({
    llm_provider: 'openai',
    llm_base_url: 'https://api.openai.com/v1',
    llm_model: 'gpt-4o',
    llm_timeout_seconds: 180,
    llm_api_key: '',
    has_api_key: false,
    pdf_parser_backend: 'docling',
    mineru_base_url: '',
    mineru_api_key: '',
    has_mineru_api_key: false,
    parsers: {
      docling: { available: true, install_hint: '' },
      mineru: { configured: false, last_check_status: 'unknown' },
    },
  });
  const [mineruTestResult, setMineruTestResult] = useState<MinerUTestResult | null>(null);

  const loadLlmConfig = useCallback(async () => {
    try {
      const config = await api.getAgentConfig();
      setLlmConfig({ ...config, llm_api_key: '', mineru_api_key: '' });
    } catch {
      console.warn('无法加载 LLM 配置。');
    }
  }, []);

  const saveLlmConfig = async () => {
    try {
      await api.updateAgentConfig(llmConfig);
      setNotice({ message: '配置保存成功。', type: 'success' });
      await loadLlmConfig();
      return true;
    } catch {
      setNotice({ message: '保存配置失败。', type: 'error' });
      return false;
    }
  };

  const testMineruConnection = async (): Promise<MinerUTestResult> => {
    try {
      await api.updateAgentConfig(llmConfig);
      const result = await api.testMineruConnection();
      setMineruTestResult(result);
      setNotice({
        message: result.status === 'ok' ? 'MinerU 连接测试成功。' : result.detail,
        type: result.status === 'ok' ? 'success' : 'error',
      });
      return result;
    } catch {
      const result: MinerUTestResult = {
        status: 'network_error',
        detail: 'MinerU 连接测试失败。',
      };
      setMineruTestResult(result);
      setNotice({ message: result.detail, type: 'error' });
      return result;
    }
  };

  return {
    llmConfig,
    setLlmConfig,
    loadLlmConfig,
    saveLlmConfig,
    mineruTestResult,
    testMineruConnection,
  };
}
