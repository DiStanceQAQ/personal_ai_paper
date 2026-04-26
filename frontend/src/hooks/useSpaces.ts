import { useState, useCallback } from 'react';
import { api } from '../api';
import { Space } from '../types';

export function useSpaces(setNotice: (n: { message: string, type: 'success' | 'error' } | null) => void) {
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [activeSpace, setActiveSpace] = useState<Space | null>(null);

  const loadSpaces = useCallback(async () => {
    try {
      const loadedSpaces = await api.listSpaces();
      setSpaces(loadedSpaces);
      try {
        const active = await api.getActiveSpace();
        setActiveSpace(active);
      } catch {
        setActiveSpace(null);
      }
    } catch (err) {
      console.error('Failed to load spaces:', err);
      setNotice({ message: '获取研究空间失败。', type: 'error' });
    }
  }, [setNotice]);

  const switchSpace = async (space: Space) => {
    if (activeSpace?.id === space.id) return;
    try {
      await api.setActiveSpace(space.id);
      setActiveSpace(space);
      return true;
    } catch {
      setNotice({ message: '切换空间失败。', type: 'error' });
      return false;
    }
  };

  const createOrUpdateSpace = async (name: string, description: string, editingSpace: Space | null) => {
    if (!name.trim()) {
      setNotice({ message: '请输入空间名称。', type: 'error' });
      return false;
    }
    try {
      if (editingSpace) {
        await api.updateSpace(editingSpace.id, name.trim(), description.trim());
        setNotice({ message: '空间更新成功。', type: 'success' });
      } else {
        const space = await api.createSpace(name.trim(), description.trim());
        await api.setActiveSpace(space.id);
        setNotice({ message: '新空间已创建并激活。', type: 'success' });
      }
      await loadSpaces();
      return true;
    } catch {
      setNotice({ message: '操作空间失败。', type: 'error' });
      return false;
    }
  };

  const deleteSpace = async (spaceId: string) => {
    try {
      await api.deleteSpace(spaceId);
      if (activeSpace?.id === spaceId) {
        setActiveSpace(null);
      }
      setNotice({ message: '研究空间已删除。', type: 'success' });
      await loadSpaces();
      return true;
    } catch {
      setNotice({ message: '删除空间失败。', type: 'error' });
      return false;
    }
  };

  return {
    spaces,
    activeSpace,
    loadSpaces,
    switchSpace,
    createOrUpdateSpace,
    deleteSpace,
  };
}
