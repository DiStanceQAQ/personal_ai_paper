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
      <div className="modal" style={{ maxWidth: '560px' }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-group">
            <div className="brand-mark" style={{ width: '32px', height: '32px' }}>
              <Cpu size={18} />
            </div>
            <h2>解析与模型配置</h2>
          </div>
          <button className="btn-icon-close" onClick={onClose}>
            <X size={20} />
          </button>
        </div>

        <p className="modal-subtitle">
          配置 PDF 高级解析后端以及用于深度提取的 LLM 参数。建议使用 MinerU 以获得最佳表格识别效果。
        </p>

        <div className="form-scroll-area">
          <div className="settings-section-title">PDF 解析引擎</div>
          <Select
            label="解析后端"
            value={config.pdf_parser_backend}
            onChange={(e) =>
              setConfig({
                ...config,
                pdf_parser_backend: e.target.value as PdfParserBackend,
              })
            }
            options={[
              { value: 'mineru', label: 'MinerU API (推荐，支持复杂公式与表格)' },
              { value: 'docling', label: 'Docling (本地解析，速度快)' },
            ]}
          />

          {config.pdf_parser_backend === 'docling' && !config.parsers.docling.available && (
            <p className="field-warning">
              检测到本地环境未安装 docling。请运行: {config.parsers.docling.install_hint || 'pip install docling'}
            </p>
          )}

          {config.pdf_parser_backend === 'mineru' && (
            <div className="settings-subsection animation-fadeIn">
              <div className="form-group">
                <label>MinerU 接口地址</label>
                <input
                  value={config.mineru_base_url}
                  onChange={(e) => setConfig({ ...config, mineru_base_url: e.target.value })}
                  placeholder="例如：http://127.0.0.1:8000"
                />
              </div>

              <div className="form-group">
                <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span>MinerU API Key</span>
                  {config.has_mineru_api_key && <span className="secure-tag">已通过加密验证</span>}
                </label>
                <input
                  type="password"
                  value={config.mineru_api_key}
                  onChange={(e) => setConfig({ ...config, mineru_api_key: e.target.value })}
                  placeholder="输入 MinerU 访问密钥..."
                />
              </div>

              <button className="btn-secondary parser-test-btn" type="button" onClick={onTestMineru}>
                测试服务可用性
              </button>
              {mineruTestResult && (
                <div className={mineruTestResult.status === 'ok' ? 'field-success' : 'field-warning'}>
                  {mineruTestResult.detail}
                </div>
              )}
            </div>
          )}

          <div className="settings-section-title">推理模型配置 (LLM)</div>
          <Select
            label="模型服务商"
            value={config.llm_provider}
            onChange={(e) => setConfig({ ...config, llm_provider: e.target.value })}
            options={[
              { value: 'openai', label: 'OpenAI / 兼容接口' },
              { value: 'ollama', label: 'Local Ollama' },
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
            <label>模型标识符</label>
            <input
              value={config.llm_model}
              onChange={(e) => setConfig({ ...config, llm_model: e.target.value })}
              placeholder="例如：gpt-4o 或 llama3.1"
            />
          </div>

          <div className="form-group">
            <label style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span>API Key</span>
              {config.has_api_key && <span className="secure-tag">密钥已就绪</span>}
            </label>
            <input
              type="password"
              value={config.llm_api_key}
              onChange={(e) => setConfig({ ...config, llm_api_key: e.target.value })}
              placeholder="在此输入您的 API 密钥..."
            />
          </div>
        </div>

        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>
            放弃修改
          </button>
          <button className="btn-primary" onClick={onSave}>
            应用并保存配置
          </button>
        </div>
      </div>
    </div>
  );
};
