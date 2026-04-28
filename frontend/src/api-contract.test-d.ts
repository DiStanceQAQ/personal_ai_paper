import { api } from './api';
import type {
  AgentConfig,
  DocumentElement,
  DocumentElementType,
  DocumentTable,
  KnowledgeCard,
  KnowledgeCardEvidence,
  KnowledgeCardOrigin,
  MinerUTestResult,
  Paper,
  PaperParseDiagnostics,
  ParsePaperResponse,
  ParseRun,
  RunDeepAnalysisResponse,
} from './types';

type Assert<T extends true> = T;
type IsEqual<A, B> =
  (<T>() => T extends A ? 1 : 2) extends
  (<T>() => T extends B ? 1 : 2)
    ? true
    : false;
type AsyncReturn<T extends (...args: any[]) => Promise<unknown>> = Awaited<ReturnType<T>>;

type _ParsePaperReturnsExtendedResponse = Assert<
  IsEqual<AsyncReturn<typeof api.parsePaper>, ParsePaperResponse>
>;
type _AgentConfigReturnsParserSettings = Assert<
  IsEqual<
    AsyncReturn<typeof api.getAgentConfig>,
    Omit<AgentConfig, 'llm_api_key' | 'mineru_api_key'>
  >
>;
type _MinerUTestReturnsStatus = Assert<
  IsEqual<AsyncReturn<typeof api.testMineruConnection>, MinerUTestResult>
>;
type _ListParseRunsReturnsRuns = Assert<
  IsEqual<AsyncReturn<typeof api.listParseRuns>, ParseRun[]>
>;
type _ListElementsReturnsElements = Assert<
  IsEqual<AsyncReturn<typeof api.listDocumentElements>, DocumentElement[]>
>;
type _ListTablesReturnsTables = Assert<
  IsEqual<AsyncReturn<typeof api.listDocumentTables>, DocumentTable[]>
>;
type _ListCardsReturnsCardsWithProvenance = Assert<
  IsEqual<AsyncReturn<typeof api.listCards>, KnowledgeCard[]>
>;
type _RunDeepAnalysisReturnsAnalysisSummary = Assert<
  IsEqual<AsyncReturn<typeof api.runDeepAnalysis>, RunDeepAnalysisResponse>
>;
type _PaperCarriesOptionalParseDiagnostics = Assert<
  IsEqual<Paper['parse_diagnostics'], PaperParseDiagnostics | undefined>
>;
type _KnowledgeCardCarriesOrigin = Assert<
  IsEqual<KnowledgeCard['created_by'], KnowledgeCardOrigin>
>;
type _KnowledgeCardCarriesEvidenceJson = Assert<
  IsEqual<KnowledgeCard['evidence_json'], string>
>;

const elementType: DocumentElementType = 'paragraph';
const origin: KnowledgeCardOrigin = 'ai';
const evidence: KnowledgeCardEvidence = {
  source_passage_ids: ['passage-1'],
  evidence_quote: 'quoted evidence',
  reasoning_summary: 'why this card is grounded',
};
void elementType;
void origin;
void evidence;
