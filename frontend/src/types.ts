export type SpaceStatus = 'active' | 'archived' | 'deleted';
export type ParseStatus = 'pending' | 'parsing' | 'parsed' | 'error';
export type ParsePaperStatus = 'queued' | Extract<ParseStatus, 'parsed' | 'error'>;
export type EmbeddingStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
export type EmbeddingRunStatus = 'queued' | 'running' | 'completed' | 'failed';
export type PdfParserBackend = 'mineru' | 'docling';
export type MetadataStatus = 'empty' | 'extracted' | 'enriched' | 'user_edited';
export type AnalysisRunStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type KnowledgeCardOrigin = 'user' | 'heuristic' | 'ai';
export type SearchStatus = 'idle' | 'loading' | 'success' | 'empty' | 'error';
export type SearchMode = 'fts' | 'hybrid';
export type SearchWarmupStatus = 'idle' | 'warming' | 'ready' | 'failed' | 'skipped';
export type UploadQueueStatus = 'uploading' | 'success' | 'failed';
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
  llm_timeout_seconds: number;
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

export interface PaperUnderstandingZh {
  one_sentence: string;
  problem: string;
  method: string;
  results: string;
  conclusion: string;
  limitations?: string;
  reusable_insights?: string[];
  source_passage_ids?: string[];
  confidence?: number;
  warnings?: string[];
  metadata?: Record<string, unknown>;
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
  embedding_status: EmbeddingStatus;
  metadata_status: MetadataStatus;
  metadata_sources_json: string;
  metadata_confidence_json: string;
  user_edited_fields_json: string;
  queued_parse_run_id?: string;
  parse_diagnostics?: PaperParseDiagnostics;
  ai_understanding_zh?: PaperUnderstandingZh | null;
}

export interface BatchUploadResult {
  filename: string;
  status: 'success' | 'failed';
  paper?: Paper;
  error?: string;
}

export interface BatchUploadResponse {
  total: number;
  succeeded: number;
  failed: number;
  results: BatchUploadResult[];
}

export interface UploadQueueItem {
  id: string;
  filename: string;
  status: UploadQueueStatus;
  title?: string;
  paper_id?: string;
  error?: string;
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

export interface ParseRunProgressStep {
  stage: string;
  label: string;
  progress: number;
}

export interface ParseRunProgress {
  stage: string;
  label: string;
  progress: number;
  details: Record<string, unknown>;
  steps: ParseRunProgressStep[];
}

export interface EmbeddingRun {
  id: string;
  paper_id: string;
  space_id: string;
  parse_run_id: string;
  status: EmbeddingRunStatus;
  provider: string;
  model: string;
  passage_count: number;
  embedded_count: number;
  reused_count: number;
  skipped_count: number;
  batch_count: number;
  warnings_json: string;
  metadata_json: string;
  started_at: string;
  completed_at: string | null;
  claimed_at: string | null;
  heartbeat_at: string | null;
  worker_id: string | null;
  attempt_count: number;
  last_error: string | null;
}

export type PaperTaskPhase = 'parsing' | 'analyzing' | 'completed' | 'failed' | 'cancelled';

export interface PaperBackgroundTask {
  paper_id: string;
  phase: PaperTaskPhase;
  progress: number;
  message: string;
  parse_run_id: string | null;
  analysis_run_id?: string | null;
  error_detail: string | null;
  parse_progress?: ParseRunProgress | null;
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

export interface AnalysisRun {
  id: string;
  paper_id: string;
  space_id: string;
  status: AnalysisRunStatus;
  model: string;
  provider: string;
  extractor_version: string;
  accepted_card_count: number;
  rejected_card_count: number;
  metadata_json: string;
  warnings_json: string;
  diagnostics_json: string;
  started_at: string;
  completed_at: string | null;
  last_error: string | null;
}

export interface PaperMetadata {
  paper_id: string;
  space_id: string;
  title: string;
  authors: string;
  year: number | null;
  doi: string;
  arxiv_id: string;
  pubmed_id: string;
  venue: string;
  abstract: string;
  parse_status: ParseStatus;
  embedding_status?: EmbeddingStatus;
  metadata_status: MetadataStatus;
  metadata_sources: Record<string, unknown>;
  metadata_confidence: Record<string, unknown>;
  user_edited_fields: string[];
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

export interface SearchWarmupState {
  space_id: string;
  status: SearchWarmupStatus;
  message: string;
  started_at: string | null;
  completed_at: string | null;
  elapsed_ms: number | null;
}

export interface PdfReaderTarget {
  paper: Paper;
  pageNumber: number;
  sourceLabel?: string;
  passageId?: string;
}
