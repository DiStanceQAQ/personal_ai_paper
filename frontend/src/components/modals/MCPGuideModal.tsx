import React, { useState } from 'react';
import { X, Copy, Terminal, Monitor, Code } from 'lucide-react';

interface MCPGuideModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCopy: (text: string) => void;
  projectPath: string;
}

type ClientType = 'claude-desktop' | 'claude-code' | 'cursor';

export const MCPGuideModal: React.FC<MCPGuideModalProps> = ({ isOpen, onClose, onCopy, projectPath }) => {
  const [activeTab, setActiveTab] = useState<ClientType>('claude-desktop');

  if (!isOpen) return null;

  // 使用动态传入的路径，如果没获取到则显示占位符
  const effectivePath = projectPath || "/YOUR/PROJECT/PATH";

  const claudeConfig = `{
  "mcpServers": {
    "personal-ai-paper": {
      "command": "python",
      "args": ["${effectivePath}/mcp_server.py"],
      "env": {
        "PYTHONPATH": "${effectivePath}"
      }
    }
  }
}`;

  const claudeCodeCmd = `mcp add personal-ai-paper python ${effectivePath}/mcp_server.py`;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal settings-modal" style={{ maxWidth: '640px', padding: '0' }} onClick={(e) => e.stopPropagation()}>
        <div style={{ padding: '32px 32px 16px' }}>
          <div className="modal-header">
            <div className="modal-title-group">
              <div className="brand-mark" style={{ background: '#7c3aed', width: '32px', height: '32px' }}>
                <Terminal size={18} />
              </div>
              <h2>连接到外部 AI 助手</h2>
            </div>
            <button className="btn-icon-close" onClick={onClose}><X size={20} /></button>
          </div>
          <p className="modal-subtitle">接入 MCP 协议，让 AI 直接读取您本地的论文知识库。</p>
        </div>

        <div style={{ padding: '0 32px' }}>
          <div className="tabs" style={{ marginBottom: '24px' }}>
            <div className="tabs-list">
              <button 
                className={activeTab === 'claude-desktop' ? 'tab-btn active' : 'tab-btn'}
                onClick={() => setActiveTab('claude-desktop')}
              >
                <Monitor size={14} style={{ marginRight: '6px' }} />
                Claude Desktop
              </button>
              <button 
                className={activeTab === 'cursor' ? 'tab-btn active' : 'tab-btn'}
                onClick={() => setActiveTab('cursor')}
              >
                <Code size={14} style={{ marginRight: '6px' }} />
                Cursor / Codex
              </button>
              <button 
                className={activeTab === 'claude-code' ? 'tab-btn active' : 'tab-btn'}
                onClick={() => setActiveTab('claude-code')}
              >
                <Terminal size={14} style={{ marginRight: '6px' }} />
                Claude Code
              </button>
            </div>
          </div>
        </div>

        <div className="form-scroll-area" style={{ padding: '0 32px 32px', maxHeight: '450px' }}>
          {activeTab === 'claude-desktop' && (
            <div className="guide-section">
              <h3>1. 配置文件路径</h3>
              <p>在 macOS 上，手动打开以下文件：</p>
              <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>
              
              <h3 style={{ marginTop: '20px' }}>2. 粘贴配置内容</h3>
              <div className="code-block-wrapper">
                <div className="code-block-header">
                  <span>claude_desktop_config.json</span>
                  <button className="btn-copy-code-inline" onClick={() => onCopy(claudeConfig)}>
                    <Copy size={12} /> 复制配置
                  </button>
                </div>
                <pre>{claudeConfig}</pre>
              </div>
              
              <h3 style={{ marginTop: '20px' }}>3. 重启 Claude 应用</h3>
              <p>重启后，对话框中会出现 🔨 图标，代表已连接成功。</p>
            </div>
          )}

          {activeTab === 'cursor' && (
            <div className="guide-section">
              <h3>在 Cursor / Codex 中手动添加</h3>
              <div style={{ marginTop: '16px', background: 'var(--bg-main)', padding: '16px', borderRadius: '12px', border: '1px solid var(--border)' }}>
                <ol style={{ fontSize: '13px', paddingLeft: '20px', lineHeight: '2' }}>
                  <li>打开 Cursor <b>Settings &gt; Models &gt; MCP</b></li>
                  <li>点击 <b>+ Add New MCP Server</b></li>
                  <li>Name: <code>Paper-Engine</code></li>
                  <li>Type: <code>stdio</code></li>
                  <li>Command: <code>python</code></li>
                  <li>Arguments: <code>{effectivePath}/mcp_server.py</code></li>
                </ol>
              </div>
            </div>
          )}

          {activeTab === 'claude-code' && (
            <div className="guide-section">
              <h3>执行添加命令</h3>
              <div className="code-block-wrapper">
                <div className="code-block-header">
                  <span>Terminal</span>
                  <button className="btn-copy-code-inline" onClick={() => onCopy(claudeCodeCmd)}>
                    <Copy size={12} /> 复制命令
                  </button>
                </div>
                <pre>{claudeCodeCmd}</pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
