import { invoke, isTauri } from '@tauri-apps/api/core';
import type {
  AgentConfig,
  AgentStatus,
  BatchUploadResponse,
  AnalysisRun,
  DocumentElement,
  DocumentElementType,
  DocumentTable,
  EmbeddingRun,
  KnowledgeCard,
  MinerUTestResult,
  Paper,
  PaperMetadata,
  ParsePaperResponse,
  ParseRun,
  Passage,
  SearchResult,
  SearchMode,
  SearchWarmupState,
  Space,
} from './types';

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';
const REQUEST_TIMEOUT_MS = 8000;

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

export async function backendUrl(path = ''): Promise<string> {
  const baseUrl = await initializeBackendBaseUrl();
  return `${baseUrl}${path}`;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  timeoutMs?: number,
): Promise<T> {
  const baseUrl = await initializeBackendBaseUrl();
  const controller = timeoutMs === undefined ? null : new AbortController();
  const timeout =
    controller === null
      ? undefined
      : window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${baseUrl}${path}`, {
      headers: init?.body instanceof FormData ? undefined : { 'Content-Type': 'application/json' },
      ...init,
      signal: init?.signal ?? controller?.signal,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(error.detail || `请求失败：${res.status}`);
    }
    return res.json() as Promise<T>;
  } finally {
    if (timeout !== undefined) {
      window.clearTimeout(timeout);
    }
  }
}

export const api = {
  health: () =>
    request<{ status: string; service: string; version: string }>(
      '/health',
      undefined,
      REQUEST_TIMEOUT_MS,
    ),
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
  getPaperPdfUrl: async (paperId: string, pageNumber?: number) => {
    const safePage = pageNumber && pageNumber > 0 ? Math.floor(pageNumber) : 1;
    return backendUrl(`/api/papers/${paperId}/pdf#page=${safePage}`);
  },
  getPaperMetadata: (paperId: string) => request<PaperMetadata>(`/api/papers/${paperId}/metadata`),
  deletePaper: (paperId: string) => request<{ status: string; paper_id: string }>(`/api/papers/${paperId}`, { method: 'DELETE' }),
  updatePaper: (paperId: string, body: Partial<Paper>) =>
    request<Paper>(`/api/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadPaper: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Paper>('/api/papers/upload', { method: 'POST', body });
  },
  uploadPapersBatch: (files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append('files', file));
    return request<BatchUploadResponse>('/api/papers/upload/batch', { method: 'POST', body });
  },
  parsePaper: (paperId: string) =>
    request<ParsePaperResponse>(`/api/papers/${paperId}/parse`, { method: 'POST' }),
  listParseRuns: (paperId: string) => request<ParseRun[]>(`/api/papers/${paperId}/parse-runs`),
  listEmbeddingRuns: (paperId: string) => request<EmbeddingRun[]>(`/api/papers/${paperId}/embedding-runs`),
  createAnalysisRun: (paperId: string) =>
    request<AnalysisRun>(`/api/papers/${paperId}/analysis-runs`, { method: 'POST' }),
  listAnalysisRuns: (paperId: string) =>
    request<AnalysisRun[]>(`/api/papers/${paperId}/analysis-runs`),
  getAnalysisRun: (paperId: string, runId: string) =>
    request<AnalysisRun>(`/api/papers/${paperId}/analysis-runs/${runId}`),
  cancelAnalysisRun: (paperId: string, runId: string) =>
    request<AnalysisRun>(`/api/papers/${paperId}/analysis-runs/${runId}/cancel`, { method: 'POST' }),
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
  listCards: (paperId: string, cardType?: string) => {
    const params = new URLSearchParams();
    if (cardType) params.set('card_type', cardType);
    const query = params.toString();
    return request<KnowledgeCard[]>(`/api/papers/${paperId}/cards${query ? `?${query}` : ''}`);
  },
  createCard: (paperId: string, card: Partial<KnowledgeCard>) =>
    request<KnowledgeCard>(`/api/papers/${paperId}/cards`, { method: 'POST', body: JSON.stringify(card) }),
  updateCard: (paperId: string, cardId: string, card: Partial<KnowledgeCard>) =>
    request<KnowledgeCard>(`/api/papers/${paperId}/cards/${cardId}`, { method: 'PATCH', body: JSON.stringify(card) }),
  deleteCard: (paperId: string, cardId: string) =>
    request<{ status: string; card_id: string }>(`/api/papers/${paperId}/cards/${cardId}`, { method: 'DELETE' }),
  search: (q: string, mode: SearchMode = 'fts') => {
    const params = new URLSearchParams({ q, limit: '30', mode });
    return request<SearchResult[]>(`/api/search?${params.toString()}`);
  },
  getSearchWarmup: () => request<SearchWarmupState>('/api/search/warmup'),
  startSearchWarmup: () =>
    request<SearchWarmupState>('/api/search/warmup', { method: 'POST' }),
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
};
