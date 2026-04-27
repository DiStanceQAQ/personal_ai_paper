"""Transactional persistence for structured PDF parse results."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from typing import Any

from pdf_models import ParseAsset, ParseDocument, ParseElement, ParseTable, PassageRecord
from search import FTS_TABLE

__all__ = ["persist_parse_result"]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _optional_json(value: Any | None) -> str | None:
    if value is None:
        return None
    return _json(value)


def _savepoint_name() -> str:
    return f"persist_parse_result_{uuid.uuid4().hex}"


def _storage_id(parse_run_id: str, source_id: str) -> str:
    return f"{parse_run_id}:{source_id}"


def _source_metadata(metadata: dict[str, Any], key: str, source_id: str) -> dict[str, Any]:
    stored_metadata = dict(metadata)
    stored_metadata[key] = source_id
    return stored_metadata


def _validate_inputs(
    paper_id: str,
    space_id: str,
    parse_document: ParseDocument,
    passages: Sequence[PassageRecord],
) -> None:
    if parse_document.paper_id != paper_id:
        raise ValueError("parse_document.paper_id does not match paper_id")
    if parse_document.space_id != space_id:
        raise ValueError("parse_document.space_id does not match space_id")

    passage_ids: set[str] = set()
    content_hashes: set[str] = set()
    element_ids = {element.id for element in parse_document.elements}

    for passage in passages:
        if passage.paper_id != paper_id:
            raise ValueError(f"passage {passage.id} paper_id does not match paper_id")
        if passage.space_id != space_id:
            raise ValueError(f"passage {passage.id} space_id does not match space_id")
        if passage.id in passage_ids:
            raise ValueError(f"duplicate passage id {passage.id}")
        passage_ids.add(passage.id)

        if passage.content_hash is not None:
            if passage.content_hash in content_hashes:
                raise ValueError(f"duplicate content_hash {passage.content_hash}")
            content_hashes.add(passage.content_hash)

        unknown_element_ids = set(passage.element_ids) - element_ids
        if unknown_element_ids:
            unknown = sorted(unknown_element_ids)[0]
            raise ValueError(f"passage {passage.id} references unknown element_id {unknown}")

    for table in parse_document.tables:
        if table.element_id is not None and table.element_id not in element_ids:
            raise ValueError(
                f"table {table.id} references unknown element_id {table.element_id}"
            )

    for asset in parse_document.assets:
        if asset.element_id is not None and asset.element_id not in element_ids:
            raise ValueError(
                f"asset {asset.id} references unknown element_id {asset.element_id}"
            )


def _load_old_generated_passages(
    conn: sqlite3.Connection, paper_id: str, space_id: str
) -> dict[str, str | None]:
    rows = conn.execute(
        """
        SELECT id, content_hash
        FROM passages
        WHERE paper_id = ?
          AND space_id = ?
          AND parse_run_id IS NOT NULL
        """,
        (paper_id, space_id),
    ).fetchall()
    return {str(row["id"]): row["content_hash"] for row in rows}


def _load_card_sources_for_old_passages(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
    old_passage_ids: set[str],
) -> dict[str, str]:
    if not old_passage_ids:
        return {}

    rows = conn.execute(
        """
        SELECT id, source_passage_id
        FROM knowledge_cards
        WHERE paper_id = ?
          AND space_id = ?
          AND source_passage_id IS NOT NULL
        """,
        (paper_id, space_id),
    ).fetchall()
    return {
        str(row["id"]): str(row["source_passage_id"])
        for row in rows
        if row["source_passage_id"] in old_passage_ids
    }


def _validate_existing_db_conflicts(
    conn: sqlite3.Connection,
    paper_id: str,
    passages: Sequence[PassageRecord],
    old_generated_passage_ids: set[str],
    new_stored_passage_ids: set[str],
) -> None:
    if new_stored_passage_ids:
        placeholders = ",".join("?" for _ in new_stored_passage_ids)
        rows = conn.execute(
            f"SELECT id FROM passages WHERE id IN ({placeholders})",
            tuple(new_stored_passage_ids),
        ).fetchall()
        for row in rows:
            existing_id = str(row["id"])
            if existing_id not in old_generated_passage_ids:
                raise ValueError(f"passage id {existing_id} already exists")

    new_hashes = {
        passage.content_hash
        for passage in passages
        if passage.content_hash is not None
    }
    if new_hashes:
        placeholders = ",".join("?" for _ in new_hashes)
        rows = conn.execute(
            f"""
            SELECT id, content_hash
            FROM passages
            WHERE paper_id = ?
              AND content_hash IN ({placeholders})
            """,
            (paper_id, *new_hashes),
        ).fetchall()
        for row in rows:
            existing_id = str(row["id"])
            if existing_id not in old_generated_passage_ids:
                raise ValueError(f"content_hash {row['content_hash']} already exists")


def _insert_parse_run(
    conn: sqlite3.Connection,
    parse_run_id: str,
    paper_id: str,
    space_id: str,
    parse_document: ParseDocument,
) -> None:
    conn.execute(
        """
        INSERT INTO parse_runs (
            id, paper_id, space_id, backend, extraction_method, status,
            quality_score, completed_at, warnings_json, config_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, 'completed', ?, datetime('now'), ?, ?, ?)
        """,
        (
            parse_run_id,
            paper_id,
            space_id,
            parse_document.backend,
            parse_document.extraction_method,
            parse_document.quality.quality_score,
            _json(parse_document.quality.warnings),
            _json({}),
            _json(parse_document.metadata),
        ),
    )


def _insert_element(
    conn: sqlite3.Connection,
    parse_run_id: str,
    paper_id: str,
    space_id: str,
    element: ParseElement,
    stored_element_id: str,
) -> None:
    conn.execute(
        """
        INSERT INTO document_elements (
            id, parse_run_id, paper_id, space_id, element_index, element_type,
            text, page_number, bbox_json, heading_path_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stored_element_id,
            parse_run_id,
            paper_id,
            space_id,
            element.element_index,
            element.element_type,
            element.text,
            element.page_number,
            _optional_json(element.bbox),
            _json(element.heading_path),
            _json(_source_metadata(element.metadata, "source_element_id", element.id)),
        ),
    )


def _insert_table(
    conn: sqlite3.Connection,
    parse_run_id: str,
    paper_id: str,
    space_id: str,
    table: ParseTable,
    stored_table_id: str,
    stored_element_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO document_tables (
            id, parse_run_id, paper_id, space_id, element_id, table_index,
            page_number, caption, cells_json, bbox_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stored_table_id,
            parse_run_id,
            paper_id,
            space_id,
            stored_element_id,
            table.table_index,
            table.page_number,
            table.caption,
            _json(table.cells),
            _optional_json(table.bbox),
            _json(_source_metadata(table.metadata, "source_table_id", table.id)),
        ),
    )


def _insert_asset(
    conn: sqlite3.Connection,
    parse_run_id: str,
    paper_id: str,
    space_id: str,
    asset: ParseAsset,
    stored_asset_id: str,
    stored_element_id: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO document_assets (
            id, parse_run_id, paper_id, space_id, element_id, asset_type,
            page_number, uri, bbox_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            stored_asset_id,
            parse_run_id,
            paper_id,
            space_id,
            stored_element_id,
            asset.asset_type,
            asset.page_number,
            asset.uri,
            _optional_json(asset.bbox),
            _json(_source_metadata(asset.metadata, "source_asset_id", asset.id)),
        ),
    )


def _insert_passage(
    conn: sqlite3.Connection,
    parse_run_id: str,
    passage: PassageRecord,
    stored_passage_id: str,
    stored_element_ids: list[str],
) -> PassageRecord:
    persisted = passage.model_copy(
        update={
            "id": stored_passage_id,
            "parse_run_id": parse_run_id,
            "element_ids": stored_element_ids,
        }
    )
    row = persisted.to_passage_row()
    conn.execute(
        """
        INSERT INTO passages (
            id, paper_id, space_id, section, page_number, paragraph_index,
            original_text, parse_confidence, passage_type, parse_run_id,
            element_ids_json, heading_path_json, bbox_json, token_count,
            char_count, content_hash, parser_backend, extraction_method,
            quality_flags_json
        )
        VALUES (
            :id, :paper_id, :space_id, :section, :page_number,
            :paragraph_index, :original_text, :parse_confidence,
            :passage_type, :parse_run_id, :element_ids_json,
            :heading_path_json, :bbox_json, :token_count, :char_count,
            :content_hash, :parser_backend, :extraction_method,
            :quality_flags_json
        )
        """,
        row,
    )
    conn.execute(
        f"""
        INSERT INTO {FTS_TABLE} (
            passage_id, paper_id, space_id, section, original_text
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            persisted.id,
            persisted.paper_id,
            persisted.space_id,
            persisted.section,
            persisted.original_text,
        ),
    )
    return persisted


def _delete_old_generated_rows(
    conn: sqlite3.Connection, paper_id: str, space_id: str, old_passage_ids: Sequence[str]
) -> None:
    if old_passage_ids:
        conn.executemany(
            f"DELETE FROM {FTS_TABLE} WHERE passage_id = ?",
            [(passage_id,) for passage_id in old_passage_ids],
        )
    conn.execute(
        """
        DELETE FROM passages
        WHERE paper_id = ?
          AND space_id = ?
          AND parse_run_id IS NOT NULL
        """,
        (paper_id, space_id),
    )
    conn.execute(
        "DELETE FROM parse_runs WHERE paper_id = ? AND space_id = ?",
        (paper_id, space_id),
    )


def _remap_card_sources(
    conn: sqlite3.Connection,
    old_passage_hashes: dict[str, str | None],
    new_hash_to_passage_id: dict[str, str],
    card_id_to_old_source_id: dict[str, str],
) -> None:
    for card_id, old_source_id in card_id_to_old_source_id.items():
        old_hash = old_passage_hashes[old_source_id]
        new_source_id = (
            new_hash_to_passage_id.get(old_hash) if old_hash is not None else None
        )
        conn.execute(
            """
            UPDATE knowledge_cards
            SET source_passage_id = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (new_source_id, card_id),
        )


def persist_parse_result(
    conn: sqlite3.Connection,
    paper_id: str,
    space_id: str,
    parse_document: ParseDocument,
    passages: Sequence[PassageRecord],
) -> str:
    """Persist a structured parse result and return the generated parse run id."""
    _validate_inputs(paper_id, space_id, parse_document, passages)

    parse_run_id = f"parse-run-{uuid.uuid4()}"
    savepoint = _savepoint_name()
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        old_passage_hashes = _load_old_generated_passages(conn, paper_id, space_id)
        old_generated_passage_ids = set(old_passage_hashes)
        element_id_map = {
            element.id: _storage_id(parse_run_id, element.id)
            for element in parse_document.elements
        }
        passage_id_map = {
            passage.id: _storage_id(parse_run_id, passage.id)
            for passage in passages
        }
        _validate_existing_db_conflicts(
            conn,
            paper_id,
            passages,
            old_generated_passage_ids,
            set(passage_id_map.values()),
        )
        card_id_to_old_source_id = _load_card_sources_for_old_passages(
            conn, paper_id, space_id, old_generated_passage_ids
        )

        # Temporarily drop card passage FKs so generated passages can be replaced.
        if old_passage_hashes:
            conn.execute(
                """
                UPDATE knowledge_cards
                SET source_passage_id = NULL,
                    updated_at = datetime('now')
                WHERE paper_id = ?
                  AND space_id = ?
                  AND source_passage_id IN (
                      SELECT id
                      FROM passages
                      WHERE paper_id = ?
                        AND space_id = ?
                        AND parse_run_id IS NOT NULL
                  )
                """,
                (paper_id, space_id, paper_id, space_id),
            )

        _delete_old_generated_rows(conn, paper_id, space_id, list(old_passage_hashes))
        _insert_parse_run(conn, parse_run_id, paper_id, space_id, parse_document)

        for element in parse_document.elements:
            _insert_element(
                conn,
                parse_run_id,
                paper_id,
                space_id,
                element,
                element_id_map[element.id],
            )
        for table in parse_document.tables:
            _insert_table(
                conn,
                parse_run_id,
                paper_id,
                space_id,
                table,
                _storage_id(parse_run_id, table.id),
                element_id_map.get(table.element_id) if table.element_id else None,
            )
        for asset in parse_document.assets:
            _insert_asset(
                conn,
                parse_run_id,
                paper_id,
                space_id,
                asset,
                _storage_id(parse_run_id, asset.id),
                element_id_map.get(asset.element_id) if asset.element_id else None,
            )

        new_hash_to_passage_id: dict[str, str] = {}
        for passage in passages:
            persisted = _insert_passage(
                conn,
                parse_run_id,
                passage,
                passage_id_map[passage.id],
                [element_id_map[element_id] for element_id in passage.element_ids],
            )
            if persisted.content_hash is not None:
                new_hash_to_passage_id[persisted.content_hash] = persisted.id

        _remap_card_sources(
            conn,
            old_passage_hashes,
            new_hash_to_passage_id,
            card_id_to_old_source_id,
        )
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise

    return parse_run_id
