import { api } from './api';
import type {
  DocumentElement,
  DocumentElementType,
  DocumentTable,
  ParsePaperResponse,
  ParseRun,
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
type _ListParseRunsReturnsRuns = Assert<
  IsEqual<AsyncReturn<typeof api.listParseRuns>, ParseRun[]>
>;
type _ListElementsReturnsElements = Assert<
  IsEqual<AsyncReturn<typeof api.listDocumentElements>, DocumentElement[]>
>;
type _ListTablesReturnsTables = Assert<
  IsEqual<AsyncReturn<typeof api.listDocumentTables>, DocumentTable[]>
>;

const elementType: DocumentElementType = 'paragraph';
void elementType;
