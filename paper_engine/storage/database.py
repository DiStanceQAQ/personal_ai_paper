"""SQLite database initialization and connection management."""

import sqlite3
from pathlib import Path

from paper_engine.core.config import APP_DATA_DIR, DATABASE_PATH
from paper_engine.storage.migrations import apply_migrations

__all__ = [
    "SCHEMA_SQL",
    "DATABASE_PATH",
    "ensure_app_data_dir",
    "get_connection",
    "init_db",
    "get_table_names",
]

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'archived', 'deleted')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    year INTEGER,
    doi TEXT NOT NULL DEFAULT '',
    arxiv_id TEXT NOT NULL DEFAULT '',
    pubmed_id TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    abstract TEXT NOT NULL DEFAULT '',
    citation TEXT NOT NULL DEFAULT '',
    user_tags TEXT NOT NULL DEFAULT '',
    relation_to_idea TEXT NOT NULL DEFAULT 'unclassified'
        CHECK(relation_to_idea IN (
            'supports', 'refutes', 'inspires', 'baseline',
            'method_source', 'background', 'result_comparison', 'unclassified'
        )),
    file_path TEXT NOT NULL DEFAULT '',
    file_hash TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT (datetime('now')),
    parse_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(parse_status IN ('pending', 'parsing', 'parsed', 'error')),
    FOREIGN KEY (space_id) REFERENCES spaces(id)
);

CREATE TABLE IF NOT EXISTS passages (
    id TEXT NOT NULL UNIQUE,
    paper_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    section TEXT NOT NULL DEFAULT '',
    page_number INTEGER NOT NULL DEFAULT 0,
    paragraph_index INTEGER NOT NULL DEFAULT 0,
    original_text TEXT NOT NULL DEFAULT '',
    parse_confidence REAL NOT NULL DEFAULT 1.0,
    passage_type TEXT NOT NULL DEFAULT 'body'
        CHECK(passage_type IN (
            'abstract', 'introduction', 'method', 'result',
            'discussion', 'limitation', 'appendix', 'body'
        )),
    FOREIGN KEY (paper_id) REFERENCES papers(id),
    FOREIGN KEY (space_id) REFERENCES spaces(id)
);

CREATE TABLE IF NOT EXISTS knowledge_cards (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    paper_id TEXT NOT NULL,
    source_passage_id TEXT,
    card_type TEXT NOT NULL
        CHECK(card_type IN (
            'Problem', 'Claim', 'Evidence', 'Method',
            'Object', 'Variable', 'Metric', 'Result',
            'Failure Mode', 'Interpretation', 'Limitation', 'Practical Tip'
        )),
    summary TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 1.0,
    user_edited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (paper_id) REFERENCES papers(id),
    FOREIGN KEY (space_id) REFERENCES spaces(id),
    FOREIGN KEY (source_passage_id) REFERENCES passages(id)
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    paper_id TEXT,
    content TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (space_id) REFERENCES spaces(id),
    FOREIGN KEY (paper_id) REFERENCES papers(id)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
"""


def ensure_app_data_dir() -> None:
    """Create the application data directory if it does not exist."""
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection(database_path: Path | None = None) -> sqlite3.Connection:
    """Get a SQLite connection with foreign keys enabled."""
    path = database_path or DATABASE_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(database_path: Path | None = None) -> sqlite3.Connection:
    """Initialize the database schema. Safe to call repeatedly (uses IF NOT EXISTS)."""
    ensure_app_data_dir()
    conn = get_connection(database_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()

    # Initialize FTS5 index on the same connection (standalone, no content=)
    from paper_engine.retrieval.lexical import FTS_TABLE

    conn.execute(f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE}
        USING fts5(
            passage_id,
            paper_id,
            space_id,
            section,
            original_text
        )
    """)
    conn.commit()
    apply_migrations(conn)

    return conn


def get_table_names(conn: sqlite3.Connection) -> list[str]:
    """Return a list of all table names in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]
