export type SpaceStatus = 'active' | 'archived' | 'deleted';
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'error';
export type ParsePaperStatus = Extract<ParseStatus, 'parsed' | 'error'>;
export type DocumentElementType =
  | 'title'
  | 'heading'
  | 'paragraph'
  | 'list'
  | 'table'
  | 'figure'
  | 'caption'
  | 'equation'
  | 'code'
  | 'reference'
  | 'page_header'
  | 'page_footer'
  | 'unknown';

export type ExtractionMethod =
  | 'native_text'
  | 'ocr'
  | 'layout_model'
  | 'llm_parser'
  | 'legacy'
  | '';

export interface Space {
  id: string;
  name: string;
  description: string;
  status: SpaceStatus;
  created_at: string;
  updated_at: string;
}

export interface PaperParseDiagnostics {
  parser_backend: string | null;
  quality_score: number | null;
  warning_count: number;
  passage_count: number | null;
  table_count: number | null;
  last_parse_time: string | null;
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
  parse_diagnostics?: PaperParseDiagnostics;
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
  parse_run_id: string | null;
  element_ids_json: string | null;
  heading_path_json: string | null;
  bbox_json: string | null;
  token_count: number | null;
  char_count: number | null;
  content_hash: string | null;
  parser_backend: string;
  extraction_method: ExtractionMethod;
  quality_flags_json: string | null;
}

export interface ParsePaperResponse {
  status: ParsePaperStatus;
  paper_id: string;
  passage_count: number;
  parse_run_id: string | null;
  backend: string | null;
  quality_score: number | null;
  warnings: string[];
}

export interface ParseRun {
  id: string;
  paper_id: string;
  space_id: string;
  backend: string;
  extraction_method: ExtractionMethod;
  status: string;
  quality_score: number | null;
  started_at: string;
  created_at: string;
  completed_at: string | null;
  warnings_json: string;
  config_json: string;
  metadata_json: string;
}

export interface DocumentElement {
  id: string;
  parse_run_id: string;
  paper_id: string;
  space_id: string;
  element_index: number;
  element_type: DocumentElementType;
  text: string;
  page_number: number;
  bbox_json: string | null;
  heading_path_json: string;
  metadata_json: string;
}

export interface DocumentTable {
  id: string;
  parse_run_id: string;
  paper_id: string;
  space_id: string;
  element_id: string | null;
  table_index: number;
  page_number: number;
  caption: string;
  cells_json: string;
  bbox_json: string | null;
  metadata_json: string;
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
