"""Idempotent SQLite schema migration helpers."""

import json
import sqlite3
from collections.abc import Callable

__all__ = [
    "LATEST_SCHEMA_VERSION",
    "SCHEMA_VERSION_KEY",
    "apply_migrations",
    "get_schema_version",
    "set_schema_version",
]

SCHEMA_VERSION_KEY = "schema_version"
LATEST_SCHEMA_VERSION = 7

Migration = Callable[[sqlite3.Connection], None]


def _create_parse_run_document_tables(conn: sqlite3.Connection) -> None:
    """Create parse run and structured document storage tables."""
    statements = (
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_id_space_id_unique
            ON papers(id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS parse_runs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            backend TEXT NOT NULL DEFAULT '',
            extraction_method TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'completed',
            quality_score REAL,
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            config_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (space_id) REFERENCES spaces(id)
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_parse_runs_id_paper_space_unique
            ON parse_runs(id, paper_id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS document_elements (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_index INTEGER NOT NULL,
            element_type TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            page_number INTEGER NOT NULL DEFAULT 0,
            bbox_json TEXT,
            heading_path_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_document_elements_id_parse_scope_unique
            ON document_elements(id, parse_run_id, paper_id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS document_tables (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_id TEXT,
            table_index INTEGER NOT NULL DEFAULT 0,
            page_number INTEGER NOT NULL DEFAULT 0,
            caption TEXT NOT NULL DEFAULT '',
            cells_json TEXT NOT NULL DEFAULT '[]',
            bbox_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (element_id, parse_run_id, paper_id, space_id)
                REFERENCES document_elements(id, parse_run_id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_assets (
            id TEXT PRIMARY KEY,
            parse_run_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            element_id TEXT,
            asset_type TEXT NOT NULL DEFAULT '',
            page_number INTEGER NOT NULL DEFAULT 0,
            uri TEXT NOT NULL DEFAULT '',
            bbox_json TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (element_id, parse_run_id, paper_id, space_id)
                REFERENCES document_elements(id, parse_run_id, paper_id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_paper_id
            ON parse_runs(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_space_id
            ON parse_runs(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_paper_id
            ON document_elements(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_space_id
            ON document_elements(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_parse_run_id
            ON document_elements(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_elements_paper_element_index
            ON document_elements(paper_id, element_index)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_paper_id
            ON document_tables(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_space_id
            ON document_tables(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_parse_run_id
            ON document_tables(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_tables_element_id
            ON document_tables(element_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_paper_id
            ON document_assets(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_space_id
            ON document_assets(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_parse_run_id
            ON document_assets(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_document_assets_element_id
            ON document_assets(element_id)
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _extend_passages_with_provenance_columns(conn: sqlite3.Connection) -> None:
    """Add passage provenance fields for structured parse persistence."""
    statements = (
        """
        ALTER TABLE passages
        ADD COLUMN parse_run_id TEXT REFERENCES parse_runs(id) ON DELETE SET NULL
        """,
        """
        ALTER TABLE passages
        ADD COLUMN element_ids_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE passages
        ADD COLUMN heading_path_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        ALTER TABLE passages
        ADD COLUMN bbox_json TEXT
        """,
        """
        ALTER TABLE passages
        ADD COLUMN token_count INTEGER
        """,
        """
        ALTER TABLE passages
        ADD COLUMN char_count INTEGER
        """,
        """
        ALTER TABLE passages
        ADD COLUMN content_hash TEXT
        """,
        """
        ALTER TABLE passages
        ADD COLUMN parser_backend TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE passages
        ADD COLUMN extraction_method TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE passages
        ADD COLUMN quality_flags_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_passages_paper_content_hash_unique
            ON passages(paper_id, content_hash)
            WHERE content_hash IS NOT NULL
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _create_analysis_run_and_card_provenance_schema(conn: sqlite3.Connection) -> None:
    """Create analysis run tracking and card provenance storage."""
    statements = (
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_papers_id_space_id_unique
            ON papers(id, space_id)
        """,
        """
        CREATE TABLE IF NOT EXISTS analysis_runs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed',
            model TEXT NOT NULL DEFAULT '',
            provider TEXT NOT NULL DEFAULT '',
            extractor_version TEXT NOT NULL DEFAULT '',
            accepted_card_count INTEGER NOT NULL DEFAULT 0,
            rejected_card_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            diagnostics_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_analysis_runs_id_paper_space_unique
            ON analysis_runs(id, paper_id, space_id)
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_analysis_runs_scope_immutable
        BEFORE UPDATE OF paper_id, space_id ON analysis_runs
        WHEN NEW.paper_id != OLD.paper_id OR NEW.space_id != OLD.space_id
        BEGIN
            SELECT RAISE(
                ABORT,
                'analysis run paper and space cannot be changed'
            );
        END
        """,
        """
        ALTER TABLE knowledge_cards
        ADD COLUMN created_by TEXT NOT NULL DEFAULT 'heuristic'
            CHECK(created_by IN ('user', 'heuristic', 'ai'))
        """,
        """
        ALTER TABLE knowledge_cards
        ADD COLUMN extractor_version TEXT NOT NULL DEFAULT ''
        """,
        """
        ALTER TABLE knowledge_cards
        ADD COLUMN analysis_run_id TEXT
            REFERENCES analysis_runs(id)
            ON DELETE SET NULL
        """,
        """
        ALTER TABLE knowledge_cards
        ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '{}'
        """,
        """
        ALTER TABLE knowledge_cards
        ADD COLUMN quality_flags_json TEXT NOT NULL DEFAULT '[]'
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_cards_id_paper_space_unique
            ON knowledge_cards(id, paper_id, space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_cards_analysis_run_id
            ON knowledge_cards(analysis_run_id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_passages_id_paper_space_unique
            ON passages(id, paper_id, space_id)
        """,
        """
        UPDATE knowledge_cards
        SET created_by = 'user'
        WHERE user_edited = 1
        """,
        """
        UPDATE knowledge_cards
        SET created_by = 'heuristic'
        WHERE user_edited != 1 OR user_edited IS NULL
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_knowledge_cards_analysis_scope_insert
        BEFORE INSERT ON knowledge_cards
        WHEN NEW.analysis_run_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM analysis_runs
                 WHERE id = NEW.analysis_run_id
                   AND paper_id = NEW.paper_id
                   AND space_id = NEW.space_id
             )
        BEGIN
            SELECT RAISE(
                ABORT,
                'analysis_run_id must match card paper and space'
            );
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_knowledge_cards_analysis_scope_update
        BEFORE UPDATE OF analysis_run_id, paper_id, space_id ON knowledge_cards
        WHEN NEW.analysis_run_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM analysis_runs
                 WHERE id = NEW.analysis_run_id
                   AND paper_id = NEW.paper_id
                   AND space_id = NEW.space_id
             )
        BEGIN
            SELECT RAISE(
                ABORT,
                'analysis_run_id must match card paper and space'
            );
        END
        """,
        """
        CREATE TABLE IF NOT EXISTS knowledge_card_sources (
            id TEXT PRIMARY KEY,
            card_id TEXT NOT NULL,
            passage_id TEXT NOT NULL,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            analysis_run_id TEXT,
            evidence_quote TEXT NOT NULL DEFAULT '',
            confidence REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (card_id, paper_id, space_id)
                REFERENCES knowledge_cards(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (passage_id, paper_id, space_id)
                REFERENCES passages(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (analysis_run_id)
                REFERENCES analysis_runs(id)
                ON DELETE SET NULL
        )
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_knowledge_card_sources_analysis_scope_insert
        BEFORE INSERT ON knowledge_card_sources
        WHEN NEW.analysis_run_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM analysis_runs
                 WHERE id = NEW.analysis_run_id
                   AND paper_id = NEW.paper_id
                   AND space_id = NEW.space_id
             )
        BEGIN
            SELECT RAISE(
                ABORT,
                'analysis_run_id must match source paper and space'
            );
        END
        """,
        """
        CREATE TRIGGER IF NOT EXISTS trg_knowledge_card_sources_analysis_scope_update
        BEFORE UPDATE OF analysis_run_id, paper_id, space_id ON knowledge_card_sources
        WHEN NEW.analysis_run_id IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM analysis_runs
                 WHERE id = NEW.analysis_run_id
                   AND paper_id = NEW.paper_id
                   AND space_id = NEW.space_id
             )
        BEGIN
            SELECT RAISE(
                ABORT,
                'analysis_run_id must match source paper and space'
            );
        END
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_runs_paper_id
            ON analysis_runs(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_runs_space_id
            ON analysis_runs(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_runs_paper_started_at
            ON analysis_runs(paper_id, started_at)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_card_sources_card_passage_unique
            ON knowledge_card_sources(card_id, passage_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_card_sources_card_id
            ON knowledge_card_sources(card_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_card_sources_passage_id
            ON knowledge_card_sources(passage_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_card_sources_paper_id
            ON knowledge_card_sources(paper_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_card_sources_space_id
            ON knowledge_card_sources(space_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_knowledge_card_sources_analysis_run_id
            ON knowledge_card_sources(analysis_run_id)
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _create_passage_embedding_schema(conn: sqlite3.Connection) -> None:
    """Create optional embedding storage for passage semantic search."""
    statements = (
        """
        CREATE TABLE IF NOT EXISTS passage_embeddings (
            passage_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            dimension INTEGER NOT NULL CHECK(dimension > 0),
            embedding_json TEXT NOT NULL,
            content_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (passage_id, provider, model),
            FOREIGN KEY (passage_id)
                REFERENCES passages(id)
                ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_passage_embeddings_passage_id
            ON passage_embeddings(passage_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_passage_embeddings_provider_model
            ON passage_embeddings(provider, model)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_passage_embeddings_content_hash
            ON passage_embeddings(content_hash)
        """,
    )
    for statement in statements:
        conn.execute(statement)


def _add_parse_run_worker_state(conn: sqlite3.Connection) -> None:
    """Add durable worker state to parse runs."""
    statements = (
        "ALTER TABLE parse_runs ADD COLUMN claimed_at TEXT",
        "ALTER TABLE parse_runs ADD COLUMN heartbeat_at TEXT",
        "ALTER TABLE parse_runs ADD COLUMN worker_id TEXT",
        "ALTER TABLE parse_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE parse_runs ADD COLUMN last_error TEXT",
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_status_started
            ON parse_runs(status, started_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_parse_runs_paper_status
            ON parse_runs(paper_id, status)
        """,
        """
        UPDATE parse_runs
        SET status = 'completed'
        WHERE status = ''
        """,
    )
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


def _add_metadata_and_analysis_worker_state(conn: sqlite3.Connection) -> None:
    """Track core paper metadata provenance and durable analysis worker state."""
    statements = (
        """
        ALTER TABLE papers
        ADD COLUMN metadata_status TEXT NOT NULL DEFAULT 'empty'
            CHECK(metadata_status IN ('empty', 'extracted', 'enriched', 'user_edited'))
        """,
        """
        ALTER TABLE papers
        ADD COLUMN metadata_sources_json TEXT NOT NULL DEFAULT '{}'
        """,
        """
        ALTER TABLE papers
        ADD COLUMN metadata_confidence_json TEXT NOT NULL DEFAULT '{}'
        """,
        """
        ALTER TABLE papers
        ADD COLUMN user_edited_fields_json TEXT NOT NULL DEFAULT '[]'
        """,
        "ALTER TABLE analysis_runs ADD COLUMN claimed_at TEXT",
        "ALTER TABLE analysis_runs ADD COLUMN heartbeat_at TEXT",
        "ALTER TABLE analysis_runs ADD COLUMN worker_id TEXT",
        "ALTER TABLE analysis_runs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE analysis_runs ADD COLUMN last_error TEXT",
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_runs_status_started
            ON analysis_runs(status, started_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_analysis_runs_paper_status
            ON analysis_runs(paper_id, status)
        """,
    )
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    core_fields = ("title", "authors", "year", "doi", "arxiv_id", "venue", "abstract")
    rows = conn.execute(
        """
        SELECT id, space_id, title, authors, year, doi, arxiv_id, venue, abstract,
               metadata_sources_json, metadata_confidence_json, user_edited_fields_json
        FROM papers
        """
    ).fetchall()
    for row in rows:
        existing_user_fields = _json_list(row["user_edited_fields_json"])
        existing_sources = _json_object(row["metadata_sources_json"])
        existing_confidence = _json_object(row["metadata_confidence_json"])

        user_fields = set(existing_user_fields)
        for field in core_fields:
            value = row[field]
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            user_fields.add(field)
            existing_sources.setdefault(field, "user.edit")
            existing_confidence.setdefault(field, 1.0)

        if not user_fields:
            continue

        conn.execute(
            """
            UPDATE papers
            SET metadata_status = 'user_edited',
                metadata_sources_json = ?,
                metadata_confidence_json = ?,
                user_edited_fields_json = ?
            WHERE id = ? AND space_id = ?
            """,
            (
                json.dumps(existing_sources, ensure_ascii=False, sort_keys=True),
                json.dumps(existing_confidence, ensure_ascii=False, sort_keys=True),
                json.dumps(sorted(user_fields), ensure_ascii=False),
                row["id"],
                row["space_id"],
            ),
        )


def _add_embedding_run_worker_state(conn: sqlite3.Connection) -> None:
    """Track embedding jobs separately from PDF parse runs."""
    statements = (
        """
        ALTER TABLE papers
        ADD COLUMN embedding_status TEXT NOT NULL DEFAULT 'pending'
            CHECK(embedding_status IN (
                'pending', 'running', 'completed', 'failed', 'skipped'
            ))
        """,
        """
        CREATE TABLE IF NOT EXISTS embedding_runs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL,
            space_id TEXT NOT NULL,
            parse_run_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued'
                CHECK(status IN ('queued', 'running', 'completed', 'failed')),
            provider TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            passage_count INTEGER NOT NULL DEFAULT 0,
            embedded_count INTEGER NOT NULL DEFAULT 0,
            reused_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            batch_count INTEGER NOT NULL DEFAULT 0,
            warnings_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL DEFAULT (datetime('now')),
            completed_at TEXT,
            claimed_at TEXT,
            heartbeat_at TEXT,
            worker_id TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            FOREIGN KEY (paper_id, space_id)
                REFERENCES papers(id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (parse_run_id, paper_id, space_id)
                REFERENCES parse_runs(id, paper_id, space_id)
                ON DELETE CASCADE,
            FOREIGN KEY (space_id) REFERENCES spaces(id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_embedding_runs_status_started
            ON embedding_runs(status, started_at)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_embedding_runs_paper_status
            ON embedding_runs(paper_id, status)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_embedding_runs_parse_run_id
            ON embedding_runs(parse_run_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_embedding_runs_space_id
            ON embedding_runs(space_id)
        """,
    )
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    parsed_rows = conn.execute(
        """
        SELECT p.id, p.space_id
        FROM papers p
        WHERE p.parse_status = 'parsed'
        """
    ).fetchall()
    for row in parsed_rows:
        embedding_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM passage_embeddings pe
            JOIN passages ps ON ps.id = pe.passage_id
            WHERE ps.paper_id = ?
              AND ps.space_id = ?
            """,
            (row["id"], row["space_id"]),
        ).fetchone()[0]
        next_status = "completed" if int(embedding_count) > 0 else "pending"
        conn.execute(
            """
            UPDATE papers
            SET embedding_status = ?
            WHERE id = ? AND space_id = ?
            """,
            (next_status, row["id"], row["space_id"]),
        )


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): item for key, item in parsed.items()}


def _json_list(value: object) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


MIGRATIONS: dict[int, Migration] = {
    1: _create_parse_run_document_tables,
    2: _extend_passages_with_provenance_columns,
    3: _create_analysis_run_and_card_provenance_schema,
    4: _create_passage_embedding_schema,
    5: _add_parse_run_worker_state,
    6: _add_metadata_and_analysis_worker_state,
    7: _add_embedding_run_worker_state,
}


def _schema_version_row_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    return row is not None


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Return the current schema version, or 0 when no version row exists."""
    row = conn.execute(
        "SELECT value FROM app_state WHERE key = ?", (SCHEMA_VERSION_KEY,)
    ).fetchone()
    if row is None:
        return 0
    return int(row[0])


def set_schema_version(conn: sqlite3.Connection, version: int) -> None:
    """Store the schema version in app_state as a single upserted row."""
    conn.execute(
        """
        INSERT INTO app_state (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (SCHEMA_VERSION_KEY, str(version)),
    )


def _pending_versions(current_version: int) -> range:
    return range(current_version + 1, LATEST_SCHEMA_VERSION + 1)


def _get_migration(version: int) -> Migration:
    try:
        return MIGRATIONS[version]
    except KeyError as exc:
        raise RuntimeError(f"No migration registered for version {version}") from exc


def _apply_migration(
    conn: sqlite3.Connection, version: int, migration: Migration
) -> None:
    savepoint = f"schema_migration_{version}"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        migration(conn)
        set_schema_version(conn, version)
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending schema migrations and persist the current version."""
    current_version = get_schema_version(conn)
    if current_version > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            "Database schema version "
            f"{current_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
        )

    migrations: dict[int, Migration] = {}
    for version in _pending_versions(current_version):
        migrations[version] = _get_migration(version)

    if not _schema_version_row_exists(conn):
        set_schema_version(conn, current_version)
        conn.commit()

    for version, migration in migrations.items():
        _apply_migration(conn, version, migration)
        conn.commit()
