import { create } from 'zustand';

type NoticeType = 'success' | 'error';

export interface Notice {
  message: string;
  type: NoticeType;
}

export interface UIState {
  // Global Notice
  notice: Notice | null;
  setNotice: (notice: Notice | null) => void;
  showNotice: (message: string, type?: NoticeType) => void;

  // View State
  activeView: 'library' | 'search' | 'reader';
  setActiveView: (view: 'library' | 'search' | 'reader') => void;

  // Layout State
  isSidebarOpen: boolean;
  setIsSidebarOpen: (isOpen: boolean | ((prev: boolean) => boolean)) => void;
  isInspectorOpen: boolean;
  setIsInspectorOpen: (isOpen: boolean | ((prev: boolean) => boolean)) => void;
}

export const useUIStore = create<UIState>((set) => ({
  notice: null,
  setNotice: (notice) => set({ notice }),
  showNotice: (message, type = 'success') => set({ notice: { message, type } }),

  activeView: 'library',
  setActiveView: (activeView) => set({ activeView }),

  isSidebarOpen: true,
  setIsSidebarOpen: (updater) =>
    set((state) => ({
      isSidebarOpen: typeof updater === 'function' ? updater(state.isSidebarOpen) : updater,
    })),

  isInspectorOpen: true,
  setIsInspectorOpen: (updater) =>
    set((state) => ({
      isInspectorOpen: typeof updater === 'function' ? updater(state.isInspectorOpen) : updater,
    })),
}));
