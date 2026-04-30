import React, { useState } from 'react';
import { X, Copy, Terminal, Monitor, Code, Check } from 'lucide-react';
import { DialogShell } from './DialogShell';

interface MCPGuideModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCopy: (text: string) => void;
  projectPath: string;
}

type ClientType = 'claude-desktop' | 'claude-code' | 'cursor';

export const MCPGuideModal: React.FC<MCPGuideModalProps> = ({ isOpen, onClose, onCopy, projectPath }) => {
  const [activeTab, setActiveTab] = useState<ClientType>('claude-desktop');
  const [copiedType, setCopiedType] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleCopy = (text: string, type: string) => {
    onCopy(text);
    setCopiedType(type);
    setTimeout(() => setCopiedType(null), 2000);
  };

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
    <DialogShell
      isOpen={isOpen}
      onClose={onClose}
      labelledBy="mcp-guide-modal-title"
      className="mcp-guide-modal"
      style={{ maxWidth: '640px' }}
    >
      <div className="modal-header">
        <div className="modal-title-group">
          <div className="brand-mark" style={{ background: 'var(--accent)' }}>
            <Terminal size={18} />
          </div>
          <h2 id="mcp-guide-modal-title">连接到外部 AI 助手</h2>
        </div>
        <button className="btn-icon-close" onClick={onClose} aria-label="关闭 MCP 连接指南">
          <X size={20} />
        </button>
      </div>

      <div className="modal-subtitle" style={{ paddingBottom: '20px' }}>
        接入 MCP (Model Context Protocol) 协议，让您的 AI 助手直接读取本地论文知识库。
      </div>

      <div className="form-scroll-area" style={{ padding: '0' }}>
        <div style={{ padding: '0 32px' }}>
          <div className="tabs">
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

        <div style={{ padding: '24px 32px 32px' }}>
          {activeTab === 'claude-desktop' && (
            <div className="guide-section animation-fadeIn">
              <h3>1. 配置文件路径</h3>
              <p>在 macOS 上，使用编辑器打开以下配置文件：</p>
              <code>~/Library/Application Support/Claude/claude_desktop_config.json</code>

              <h3 style={{ marginTop: '24px' }}>2. 粘贴配置内容</h3>
              <p>将以下 JSON 片段添加到 <code>mcpServers</code> 字段中：</p>
              <div className="code-block-wrapper">
                <div className="code-block-header">
                  <span>claude_desktop_config.json</span>
                  <button
                    className="btn-copy-code-inline"
                    onClick={() => handleCopy(claudeConfig, 'config')}
                  >
                    {copiedType === 'config' ? <Check size={12} /> : <Copy size={12} />}
                    {copiedType === 'config' ? '已复制' : '复制配置'}
                  </button>
                </div>
                <pre>{claudeConfig}</pre>
              </div>

              <h3 style={{ marginTop: '24px' }}>3. 重启 Claude 应用</h3>
              <p>彻底退出并重启 Claude Desktop。在对话框中看到 🔨 图标即表示连接成功。</p>
            </div>
          )}

          {activeTab === 'cursor' && (
            <div className="guide-section animation-fadeIn">
              <h3>在 Cursor / Codex 中手动添加</h3>
              <p>Cursor 支持通过 stdio 方式连接 MCP 服务，请按照以下步骤配置：</p>

              <div className="cursor-guide-card">
                <ol>
                  <li>打开 Cursor <b>Settings &gt; Models &gt; MCP</b></li>
                  <li>点击 <b>+ Add New MCP Server</b></li>
                  <li>Name: <code>Paper-Engine</code></li>
                  <li>Type: <code>stdio</code></li>
                  <li>Command: <code>python</code></li>
                  <li>Arguments: <code>{effectivePath}/mcp_server.py</code></li>
                </ol>
              </div>

              <div style={{ marginTop: '20px', fontSize: '12px', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <Check size={14} style={{ color: 'var(--success)' }} />
                配置完成后，AI 将能够搜索您的论文库并引用具体内容。
              </div>
            </div>
          )}

          {activeTab === 'claude-code' && (
            <div className="guide-section animation-fadeIn">
              <h3>执行添加命令</h3>
              <p>在您的终端中运行以下命令，Claude Code 会自动完成注册：</p>

              <div className="code-block-wrapper">
                <div className="code-block-header">
                  <span>Terminal</span>
                  <button
                    className="btn-copy-code-inline"
                    onClick={() => handleCopy(claudeCodeCmd, 'cmd')}
                  >
                    {copiedType === 'cmd' ? <Check size={12} /> : <Copy size={12} />}
                    {copiedType === 'cmd' ? '已复制' : '复制命令'}
                  </button>
                </div>
                <pre>{claudeCodeCmd}</pre>
              </div>

              <p style={{ marginTop: '16px' }}>
                注册成功后，运行 <code>claude</code> 即可在对话中使用知识库。
              </p>
            </div>
          )}
        </div>
      </div>
    </DialogShell>
  );
};
