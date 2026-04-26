import { useState } from 'react';
import { Space, Paper } from '../types';

export function useModals() {
  const [activeModals, setActiveModals] = useState({
    settings: false,
    space: false,
    mcpGuide: false,
    editPaper: false,
    deleteSpace: false,
    deletePaper: false,
  });

  const [modalData, setModalData] = useState<{
    editingSpace: Space | null;
    spaceToDelete: string | null;
    paperToDelete: string | null;
  }>({
    editingSpace: null,
    spaceToDelete: null,
    paperToDelete: null,
  });

  const openModal = (modal: keyof typeof activeModals, data?: any) => {
    setActiveModals((prev) => ({ ...prev, [modal]: true }));
    if (data) {
      setModalData((prev) => ({ ...prev, ...data }));
    }
  };

  const closeModal = (modal: keyof typeof activeModals) => {
    setActiveModals((prev) => ({ ...prev, [modal]: false }));
    // 清理数据（可选）
    if (modal === 'space') setModalData((prev) => ({ ...prev, editingSpace: null }));
    if (modal === 'deleteSpace') setModalData((prev) => ({ ...prev, spaceToDelete: null }));
    if (modal === 'deletePaper') setModalData((prev) => ({ ...prev, paperToDelete: null }));
  };

  return {
    ...activeModals,
    ...modalData,
    openModal,
    closeModal,
  };
}
