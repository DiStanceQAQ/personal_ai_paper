export type SpaceStatus = 'active' | 'archived' | 'deleted';
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'error';
export type ParsePaperStatus = 'queued' | Extract<ParseStatus, 'parsed' | 'error'>;
export type PdfParserBackend = 'mineru' | 'docling';
export type KnowledgeCardOrigin = 'user' | 'heuristic' | 'ai';
export type SearchStatus = 'idle' | 'loading' | 'success' | 'empty' | 'error';
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

export interface ParserAvailability {
  docling: {
    available: boolean;
    install_hint: string;
    detail?: string;
  };
  mineru: {
    configured: boolean;
    last_check_status: string;
  };
}

export interface AgentConfig {
  llm_provider: string;
  llm_base_url: string;
  llm_model: string;
  llm_api_key: string;
  has_api_key: boolean;
  pdf_parser_backend: PdfParserBackend;
  mineru_base_url: string;
  mineru_api_key: string;
  has_mineru_api_key: boolean;
  parsers: ParserAvailability;
}

export interface MinerUTestResult {
  status: 'ok' | 'missing_credentials' | 'http_error' | 'network_error';
  detail: string;
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
  queued_parse_run_id?: string;
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
  last_error: string | null;
  warnings_json: string;
  config_json: string;
  metadata_json: string;
}

export type PaperTaskPhase = 'parsing' | 'analyzing' | 'completed' | 'failed';

export interface PaperBackgroundTask {
  paper_id: string;
  phase: PaperTaskPhase;
  progress: number;
  message: string;
  parse_run_id: string | null;
  error_detail: string | null;
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

export interface KnowledgeCardEvidence {
  source_passage_ids: string[];
  evidence_quote?: string;
  reasoning_summary?: string;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
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
  created_by: KnowledgeCardOrigin;
  extractor_version: string;
  analysis_run_id: string | null;
  evidence_json: string;
  quality_flags_json: string;
  created_at: string;
  updated_at: string;
}

export interface RunDeepAnalysisResponse {
  status: 'success';
  card_count: number;
  analysis_run_id: string;
  accepted_card_count: number;
  rejected_card_count: number;
  metadata_confidence: number;
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
