import React from 'react';
import { Plus, FolderOpen, Edit2, Trash2, Settings } from 'lucide-react';
import type { Space } from '../../types';

interface SidebarProps {
  spaces: Space[];
  activeSpace: Space | null;
  onSelectSpace: (space: Space) => void;
  onOpenCreateModal: () => void;
  onOpenEditModal: (e: React.MouseEvent, space: Space) => void;
  onOpenDeleteConfirm: (e: React.MouseEvent, spaceId: string) => void;
  onOpenSettings: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  spaces,
  activeSpace,
  onSelectSpace,
  onOpenCreateModal,
  onOpenEditModal,
  onOpenDeleteConfirm,
  onOpenSettings,
}) => {
  return (
    <aside className="sidebar">
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
            onClick={() => onSelectSpace(space)}
          >
            <div className="space-item-main">
              <FolderOpen size={16} />
              <span>{space.name}</span>
            </div>
            <div className="space-item-actions">
              <button onClick={(e) => onOpenEditModal(e, space)}>
                <Edit2 size={12} />
              </button>
              <button onClick={(e) => onOpenDeleteConfirm(e, space.id)}>
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
    </aside>
  );
};
