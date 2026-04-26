import React from 'react';
import { X, Cpu } from 'lucide-react';
import { Select } from '../ui/Select';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
  config: {
    llm_provider: string;
    llm_base_url: string;
    llm_model: string;
    llm_api_key: string;
    has_api_key: boolean;
  };
  setConfig: (config: any) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  onSave,
  config,
  setConfig,
}) => {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="brand-mark" style={{ width: '28px', height: '28px', fontSize: '14px' }}>
              <Cpu size={16} />
            </div>
            <h2>LLM 深度解析配置</h2>
          </div>
          <button className="btn-icon-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <p className="modal-subtitle">
          配置您的模型参数，为论文提供语义化的深度知识抽取能力。
        </p>

        <div className="form-scroll-area">
          <Select
            label="API 提供商"
            value={config.llm_provider}
            onChange={(e) => setConfig({ ...config, llm_provider: e.target.value })}
            options={[
              { value: 'openai', label: 'OpenAI / 兼容接口' },
              { value: 'ollama', label: '本地 Ollama (推荐)' },
              { value: 'openrouter', label: 'OpenRouter' },
            ]}
          />

          <div className="form-group">
            <label>API Base URL</label>
            <input
              value={config.llm_base_url}
              onChange={(e) => setConfig({ ...config, llm_base_url: e.target.value })}
              placeholder="例如：https://api.openai.com/v1"
            />
          </div>

          <div className="form-group">
            <label>模型名称 (Model Name)</label>
            <input
              value={config.llm_model}
              onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
              placeholder="例如：gpt-4o 或 llama3"
            />
          </div>

          <div className="form-group">
            <label>
              API Key {config.has_api_key && <span className="secure-tag">已安全加密保存</span>}
            </label>
            <input
              type="password"
              value={config.llm_api_key}
              onChange={(e) => setConfig({ ...config, llm_api_key: e.target.value })}
              placeholder="输入您的 API 密钥..."
            />
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={onSave}>
            保存并应用配置
          </button>
        </div>
      </div>
    </div>
  );
};
