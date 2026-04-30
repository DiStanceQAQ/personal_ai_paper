import React, { useState, useEffect } from 'react';
import { DialogShell } from './DialogShell';

interface SpaceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (name: string, description: string) => void;
  isEditing: boolean;
  initialName?: string;
  initialDescription?: string;
}

export const SpaceModal: React.FC<SpaceModalProps> = ({
  isOpen,
  onClose,
  onSave,
  isEditing,
  initialName = '',
  initialDescription = '',
}) => {
  const [name, setName] = useState(initialName);
  const [description, setDescription] = useState(initialDescription);

  useEffect(() => {
    if (isOpen) {
      setName(initialName);
      setDescription(initialDescription);
    }
  }, [isOpen, initialName, initialDescription]);

  if (!isOpen) return null;
  const titleId = isEditing ? 'space-modal-edit-title' : 'space-modal-create-title';

  return (
    <DialogShell isOpen={isOpen} onClose={onClose} labelledBy={titleId}>
        <div className="modal-header">
          <div className="modal-title-group">
            <h2 id={titleId}>{isEditing ? '编辑研究空间' : '新建研究空间'}</h2>
          </div>
        </div>
        
        <div style={{ padding: '32px' }}>
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
        </div>

        <div className="modal-actions">
          <button className="btn-secondary" onClick={onClose}>
            取消
          </button>
          <button className="btn-primary" onClick={() => onSave(name, description)}>
            {isEditing ? '保存修改' : '创建并进入'}
          </button>
        </div>
    </DialogShell>
  );
};
