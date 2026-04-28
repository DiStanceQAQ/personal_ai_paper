import React from 'react';
import { Edit2, FolderOpen, PanelLeftClose, PanelLeftOpen, Plus, Settings, Trash2 } from 'lucide-react';
import type { Space } from '../../types';

export interface SidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  spaces: Space[];
  activeSpace: Space | null;
  onSelectSpace: (space: Space) => void;
  onOpenCreateModal: () => void;
  onOpenEditModal: (e: React.MouseEvent, space: Space) => void;
  onOpenDeleteConfirm: (e: React.MouseEvent, spaceId: string) => void;
  onOpenSettings: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  isOpen,
  onToggle,
  spaces,
  activeSpace,
  onSelectSpace,
  onOpenCreateModal,
  onOpenEditModal,
  onOpenDeleteConfirm,
  onOpenSettings,
}) => {
  const ToggleIcon = isOpen ? PanelLeftClose : PanelLeftOpen;
  const toggleLabel = isOpen ? '收起侧边栏' : '展开侧边栏';

  return (
    <aside className={isOpen ? 'sidebar' : 'sidebar collapsed'}>
      <button
        type="button"
        className="sidebar-toggle"
        onClick={onToggle}
        title={toggleLabel}
        aria-label={toggleLabel}
        aria-expanded={isOpen}
      >
        <ToggleIcon size={15} aria-hidden="true" />
      </button>

      {isOpen && (
        <div className="sidebar-content">
          <div className="brand">
            <div className="brand-mark">P</div>
            <div>
              <h1>论文知识引擎</h1>
              <p>Idea Space 工作台</p>
            </div>
          </div>

          <div className="sidebar-actions">
            <button className="btn-new-space" onClick={onOpenCreateModal}>
              <Plus size={18} />
              <span>新建空间</span>
            </button>
          </div>

          <nav className="space-list">
            {spaces.map((space) => (
              <div
                key={space.id}
                className={space.id === activeSpace?.id ? 'space-item-wrapper active' : 'space-item-wrapper'}
              >
                <button
                  className="space-item-main-btn"
                  onClick={() => onSelectSpace(space)}
                  aria-current={space.id === activeSpace?.id ? 'page' : undefined}
                >
                  <div className="space-item-main">
                    <FolderOpen size={16} />
                    <span>{space.name}</span>
                  </div>
                </button>
                <div className="space-item-actions">
                  <button onClick={(e) => { e.stopPropagation(); onOpenEditModal(e, space); }} aria-label={`编辑 ${space.name}`}>
                    <Edit2 size={12} />
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); onOpenDeleteConfirm(e, space.id); }} aria-label={`删除 ${space.name}`}>
                    <Trash2 size={12} />
                  </button>
                </div>
              </div>
            ))}
          </nav>

          <div className="sidebar-footer">
            <button className="btn-settings-sidebar" onClick={onOpenSettings}>
              <Settings size={16} />
              <span>LLM 配置</span>
            </button>
          </div>
        </div>
      )}
    </aside>
  );
};
