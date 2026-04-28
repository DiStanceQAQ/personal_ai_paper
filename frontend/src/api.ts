import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  AgentConfig,
  AgentStatus,
  DocumentElement,
  DocumentElementType,
  DocumentTable,
  KnowledgeCard,
  MinerUTestResult,
  Paper,
  ParsePaperResponse,
  ParseRun,
  Passage,
  RunDeepAnalysisResponse,
  SearchResult,
  Space,
} from './types';

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';

let cachedBackendUrl: string | null = null;

export async function initializeBackendBaseUrl(): Promise<string> {
  if (cachedBackendUrl) return cachedBackendUrl;
  try {
    const url = await invoke<string>('backend_url');
    cachedBackendUrl = url.replace(/\/$/, '');
  } catch (error) {
    if (isTauri()) {
      throw error;
    }
    cachedBackendUrl = window.localStorage.getItem('paper-engine-backend-url') || DEFAULT_BACKEND;
  }
  return cachedBackendUrl;
}

export function setBackendBaseUrl(url: string): void {
  cachedBackendUrl = url.replace(/\/$/, '');
  window.localStorage.setItem('paper-engine-backend-url', cachedBackendUrl);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const baseUrl = await initializeBackendBaseUrl();
  const res = await fetch(`${baseUrl}${path}`, {
    headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    throw new Error(error.detail || `请求失败：${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; service: string; version: string }>('/health'),
  listSpaces: () => request<Space[]>('/api/spaces'),
  createSpace: (name: string, description: string) =>
    request<Space>('/api/spaces', { method: 'POST', body: JSON.stringify({ name, description }) }),
  updateSpace: (spaceId: string, name: string, description: string) =>
    request<Space>(`/api/spaces/${spaceId}`, { method: 'PATCH', body: JSON.stringify({ name, description }) }),
  deleteSpace: (spaceId: string) =>
    request<{ status: string; space_id: string }>(`/api/spaces/${spaceId}`, { method: 'DELETE' }),
  setActiveSpace: (spaceId: string) =>
    request<{ active_space_id: string; space: Space }>(`/api/spaces/active/${spaceId}`, { method: 'PUT' }),
  getActiveSpace: () => request<Space>('/api/spaces/active'),
  listPapers: () => request<Paper[]>('/api/papers'),
  getPaper: (paperId: string) => request<Paper>(`/api/papers/${paperId}`),
  deletePaper: (paperId: string) => request<{ status: string; paper_id: string }>(`/api/papers/${paperId}`, { method: 'DELETE' }),
  updatePaper: (paperId: string, body: Partial<Paper>) =>
    request<Paper>(`/api/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadPaper: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Paper>('/api/papers/upload', { method: 'POST', body });
  },
  parsePaper: (paperId: string) =>
    request<ParsePaperResponse>(`/api/papers/${paperId}/parse`, { method: 'POST' }),
  listParseRuns: (paperId: string) => request<ParseRun[]>(`/api/papers/${paperId}/parse-runs`),
  listDocumentElements: (
    paperId: string,
    filters: { type?: DocumentElementType; page?: number; limit?: number } = {},
  ) => {
    const params = new URLSearchParams();
    if (filters.type) params.set('type', filters.type);
    if (filters.page !== undefined) params.set('page', String(filters.page));
    if (filters.limit !== undefined) params.set('limit', String(filters.limit));
    const query = params.toString();
    return request<DocumentElement[]>(`/api/papers/${paperId}/elements${query ? `?${query}` : ''}`);
  },
  listDocumentTables: (paperId: string) => request<DocumentTable[]>(`/api/papers/${paperId}/tables`),
  listPassages: (paperId: string) => request<Passage[]>(`/api/papers/${paperId}/passages`),
  listCards: (paperId?: string, cardType?: string) => {
    const params = new URLSearchParams();
    if (paperId) params.set('paper_id', paperId);
    if (cardType) params.set('card_type', cardType);
    const query = params.toString();
    return request<KnowledgeCard[]>(`/api/cards${query ? `?${query}` : ''}`);
  },
  createCard: (card: Partial<KnowledgeCard>) =>
    request<KnowledgeCard>('/api/cards', { method: 'POST', body: JSON.stringify(card) }),
  updateCard: (cardId: string, card: Partial<KnowledgeCard>) =>
    request<KnowledgeCard>(`/api/cards/${cardId}`, { method: 'PATCH', body: JSON.stringify(card) }),
  deleteCard: (cardId: string) =>
    request<{ status: string; card_id: string }>(`/api/cards/${cardId}`, { method: 'DELETE' }),
  search: (q: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}&limit=30`),
  agentStatus: () => request<AgentStatus>('/api/agent/status'),
  setAgentStatus: (enabled: boolean) =>
    request<{ enabled: boolean }>('/api/agent/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
  getAgentConfig: () =>
    request<Omit<AgentConfig, 'llm_api_key' | 'mineru_api_key'>>('/api/agent/config'),
  getAppInfo: () => request<{ project_root: string; os: string }>('/api/info'),
  updateAgentConfig: (config: AgentConfig) =>
    request<{ status: string }>('/api/agent/config', { method: 'PUT', body: JSON.stringify(config) }),
  testMineruConnection: () =>
    request<MinerUTestResult>('/api/agent/config/mineru/test', { method: 'POST' }),
  runDeepAnalysis: (paperId: string) =>
    request<RunDeepAnalysisResponse>(`/api/agent/analyze/${paperId}`, { method: 'POST' }),
};
