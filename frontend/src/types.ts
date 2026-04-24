export type SpaceStatus = 'active' | 'archived' | 'deleted';
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'error';

export interface Space {
  id: string;
  name: string;
  description: string;
  status: SpaceStatus;
  created_at: string;
  updated_at: string;
}

export interface Paper {
  id: string;
  space_id: string;
  title: string;
  authors: string;
  year: number | null;
  doi: string;
  arxiv_id: string;
  pubmed_id: string;
  venue: string;
  abstract: string;
  citation: string;
  user_tags: string;
  relation_to_idea: string;
  file_path: string;
  file_hash: string;
  imported_at: string;
  parse_status: ParseStatus;
}

export interface Passage {
  id: string;
  paper_id: string;
  space_id: string;
  section: string;
  page_number: number;
  paragraph_index: number;
  original_text: string;
  parse_confidence: number;
  passage_type: string;
}

export interface KnowledgeCard {
  id: string;
  space_id: string;
  paper_id: string;
  source_passage_id: string | null;
  card_type: string;
  summary: string;
  confidence: number;
  user_edited: number;
  created_at: string;
  updated_at: string;
}

export interface AgentStatus {
  enabled: boolean;
  server_name: string;
  transport: string;
  active_space: Space | null;
}

export interface SearchResult {
  score: number;
  passage_id: string;
  paper_id: string;
  section: string;
  page_number: number;
  paragraph_index: number;
  snippet: string;
  original_text: string;
  paper_title: string;
}
