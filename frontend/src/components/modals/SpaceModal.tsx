import React from 'react';

interface SpaceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: () => void;
  isEditing: boolean;
  name: string;
  setName: (name: string) => void;
  description: string;
  setDescription: (desc: string) => void;
}

export const SpaceModal: React.FC<SpaceModalProps> = ({
  isOpen,
  onClose,
  onSave,
  isEditing,
  name,
  setName,
  description,
  setDescription,
}) => {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{isEditing ? '编辑研究空间' : '新建研究空间'}</h2>
        <div className="form-group">
          <label>空间名称</label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoFocus
            placeholder="例如：大模型推理优化"
          />
        </div>
        <div className="form-group">
          <label>空间描述</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            placeholder="描述此空间的研究目标、关注点..."
          />
        </div>
        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={onSave}>
            {isEditing ? '保存修改' : '创建并进入'}
          </button>
        </div>
      </div>
    </div>
  );
};
