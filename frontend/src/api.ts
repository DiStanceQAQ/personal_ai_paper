import type { AgentStatus, KnowledgeCard, Paper, Passage, SearchResult, Space } from './types';

const DEFAULT_BACKEND = 'http://127.0.0.1:8000';

export function backendBaseUrl(): string {
  return window.localStorage.getItem('paper-engine-backend-url') || DEFAULT_BACKEND;
}

export function setBackendBaseUrl(url: string): void {
  window.localStorage.setItem('paper-engine-backend-url', url.replace(/\/$/, ''));
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${backendBaseUrl()}${path}`, {
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
  setActiveSpace: (spaceId: string) =>
    request<{ active_space_id: string; space: Space }>(`/api/spaces/active/${spaceId}`, { method: 'PUT' }),
  getActiveSpace: () => request<Space>('/api/spaces/active'),
  listPapers: () => request<Paper[]>('/api/papers'),
  getPaper: (paperId: string) => request<Paper>(`/api/papers/${paperId}`),
  updatePaper: (paperId: string, body: Partial<Paper>) =>
    request<Paper>(`/api/papers/${paperId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  uploadPaper: (file: File) => {
    const body = new FormData();
    body.append('file', file);
    return request<Paper>('/api/papers/upload', { method: 'POST', body });
  },
  parsePaper: (paperId: string) =>
    request<{ status: string; paper_id: string; passage_count: number }>(`/api/papers/${paperId}/parse`, { method: 'POST' }),
  listPassages: (paperId: string) => request<Passage[]>(`/api/papers/${paperId}/passages`),
  listCards: (paperId?: string, cardType?: string) => {
    const params = new URLSearchParams();
    if (paperId) params.set('paper_id', paperId);
    if (cardType) params.set('card_type', cardType);
    const query = params.toString();
    return request<KnowledgeCard[]>(`/api/cards${query ? `?${query}` : ''}`);
  },
  extractCards: (paperId: string) =>
    request<{ status: string; paper_id: string; card_count: number; mode?: string; message?: string }>(
      `/api/cards/extract/${paperId}`,
      { method: 'POST' },
    ),
  search: (q: string) => request<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}&limit=30`),
  agentStatus: () => request<AgentStatus>('/api/agent/status'),
  setAgentStatus: (enabled: boolean) =>
    request<{ enabled: boolean }>('/api/agent/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
};
