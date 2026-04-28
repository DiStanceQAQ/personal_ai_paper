import React from 'react';
import { X, Cpu } from 'lucide-react';
import { Select } from '../ui/Select';
import type { AgentConfig, MinerUTestResult, PdfParserBackend } from '../../types';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void | Promise<boolean>;
  onTestMineru: () => Promise<MinerUTestResult>;
  mineruTestResult: MinerUTestResult | null;
  config: AgentConfig;
  setConfig: (config: AgentConfig) => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
  onSave,
  onTestMineru,
  mineruTestResult,
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
            <h2>解析与模型配置</h2>
          </div>
          <button className="btn-icon-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <p className="modal-subtitle">
          配置 PDF 解析后端与模型参数。
        </p>

        <div className="form-scroll-area">
          <div className="settings-section-title">PDF 解析</div>
          <Select
            label="PDF 解析方式"
            value={config.pdf_parser_backend}
            onChange={(e) =>
              setConfig({
                ...config,
                pdf_parser_backend: e.target.value as PdfParserBackend,
              })
            }
            options={[
              { value: 'mineru', label: 'MinerU API（推荐）' },
              { value: 'docling', label: 'Docling 本地解析' },
            ]}
          />

          {config.pdf_parser_backend === 'docling' && !config.parsers.docling.available && (
            <p className="field-warning">
              请安装 docling: {config.parsers.docling.install_hint || 'pip install docling'}
            </p>
          )}

          {config.pdf_parser_backend === 'mineru' && (
            <div className="settings-subsection">
              <div className="form-group">
                <label>MinerU Base URL</label>
                <input
                  value={config.mineru_base_url}
                  onChange={(e) => setConfig({ ...config, mineru_base_url: e.target.value })}
                  placeholder="例如：http://127.0.0.1:8000"
                />
              </div>

              <div className="form-group">
                <label>
                  MinerU API Key {config.has_mineru_api_key && <span className="secure-tag">已安全保存</span>}
                </label>
                <input
                  type="password"
                  value={config.mineru_api_key}
                  onChange={(e) => setConfig({ ...config, mineru_api_key: e.target.value })}
                  placeholder="输入 MinerU API Key..."
                />
              </div>

              <button className="btn-secondary parser-test-btn" type="button" onClick={onTestMineru}>
                测试 MinerU 连接
              </button>
              {mineruTestResult && (
                <p className={mineruTestResult.status === 'ok' ? 'field-success' : 'field-warning'}>
                  {mineruTestResult.detail}
                </p>
              )}
            </div>
          )}

          <div className="settings-section-title">LLM 深度解析</div>
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
