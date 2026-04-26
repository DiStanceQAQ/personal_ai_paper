import { useState, useCallback } from 'react';
import { api } from '../api';

export function useLlmConfig(setNotice: (n: { message: string, type: 'success' | 'error' } | null) => void) {
  const [llmConfig, setLlmConfig] = useState({
    llm_provider: 'openai',
    llm_base_url: 'https://api.openai.com/v1',
    llm_model: 'gpt-4o',
    llm_api_key: '',
    has_api_key: false
  });

  const loadLlmConfig = useCallback(async () => {
    try {
      const config = await api.getAgentConfig();
      setLlmConfig({ ...config, llm_api_key: '' });
    } catch {
      console.warn('无法加载 LLM 配置。');
    }
  }, []);

  const saveLlmConfig = async () => {
    try {
      await api.updateAgentConfig(llmConfig);
      setNotice({ message: 'LLM 配置保存成功。', type: 'success' });
      await loadLlmConfig();
      return true;
    } catch {
      setNotice({ message: '保存配置失败。', type: 'error' });
      return false;
    }
  };

  return {
    llmConfig,
    setLlmConfig,
    loadLlmConfig,
    saveLlmConfig
  };
}
