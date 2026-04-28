import React from 'react';
import { SettingsModal } from './SettingsModal';
import { SpaceModal } from './SpaceModal';
import { EditPaperModal } from './EditPaperModal';
import { ConfirmModal } from './ConfirmModal';
import { MCPGuideModal } from './MCPGuideModal';
import type { AgentConfig, MinerUTestResult, Paper } from '../../types';

interface ModalsContainerProps {
  modals: any; // useModals 的返回值
  llmConfig: AgentConfig;
  setLlmConfig: (config: AgentConfig) => void;
  saveLlmConfig: () => Promise<boolean>;
  mineruTestResult: MinerUTestResult | null;
  testMineruConnection: () => Promise<MinerUTestResult>;
  createOrUpdateSpace: (name: string, description: string, editingSpace: any) => Promise<boolean>;
  deleteSpace: (id: string) => Promise<boolean>;
  handleUpdatePaper: (id: string, data: any) => Promise<void>;
  handleDeletePaper: (id: string) => Promise<boolean>;
  selectedPaper: Paper | null;
  projectRoot: string;
  setNotice: (n: any) => void;
}

export const ModalsContainer: React.FC<ModalsContainerProps> = ({
  modals,
  llmConfig,
  setLlmConfig,
  saveLlmConfig,
  mineruTestResult,
  testMineruConnection,
  createOrUpdateSpace,
  deleteSpace,
  handleUpdatePaper,
  handleDeletePaper,
  selectedPaper,
  projectRoot,
  setNotice,
}) => {
  return (
    <>
      <SettingsModal
        isOpen={modals.settings}
        onClose={() => modals.closeModal('settings')}
        onSave={saveLlmConfig}
        onTestMineru={testMineruConnection}
        mineruTestResult={mineruTestResult}
        config={llmConfig}
        setConfig={setLlmConfig}
      />

      <SpaceModal
        isOpen={modals.space}
        onClose={() => modals.closeModal('space')}
        onSave={(name, desc) => 
          createOrUpdateSpace(name, desc, modals.editingSpace).then(success => success && modals.closeModal('space'))
        }
        isEditing={!!modals.editingSpace}
        initialName={modals.editingSpace?.name || ''}
        initialDescription={modals.editingSpace?.description || ''}
      />

      <EditPaperModal
        isOpen={modals.editPaper}
        onClose={() => modals.closeModal('editPaper')}
        onSave={handleUpdatePaper}
        paper={selectedPaper}
      />

      <ConfirmModal
        isOpen={modals.deleteSpace}
        title="确认删除空间？"
        message="此空间内所有的论文、解析结果和知识卡片将不再显示。"
        onConfirm={() => modals.spaceToDelete && deleteSpace(modals.spaceToDelete).then(() => modals.closeModal('deleteSpace'))}
        onCancel={() => modals.closeModal('deleteSpace')}
      />

      <ConfirmModal
        isOpen={modals.deletePaper}
        title="确认从库中移除这篇论文？"
        message="该操作将删除该论文的所有物理分片、搜索索引和已提取的卡片。"
        onConfirm={() => modals.paperToDelete && handleDeletePaper(modals.paperToDelete).then(() => modals.closeModal('deletePaper'))}
        onCancel={() => modals.closeModal('deletePaper')}
      />

      <MCPGuideModal
        isOpen={modals.mcpGuide}
        onClose={() => modals.closeModal('mcpGuide')}
        onCopy={(text) => navigator.clipboard.writeText(text).then(() => setNotice({ message: '配置已复制', type: 'success' }))}
        projectPath={projectRoot}
      />
    </>
  );
};
