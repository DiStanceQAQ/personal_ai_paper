"""HTTP routes for importing and managing papers."""

from typing import Any

from fastapi import APIRouter, Body, Query, UploadFile, status

from paper_engine.papers import service

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.post("/upload")
async def upload_paper(file: UploadFile) -> dict[str, Any]:
    return await service.upload_paper(file)


@router.post("/upload/batch")
async def upload_papers_batch(files: list[UploadFile]) -> dict[str, Any]:
    return await service.upload_papers_batch(files)


@router.get("")
async def list_papers(space_id: str | None = None) -> list[dict[str, Any]]:
    return await service.list_papers(space_id=space_id)


@router.get("/{paper_id}")
async def get_paper(paper_id: str) -> dict[str, Any]:
    return await service.get_paper(paper_id)


@router.get("/{paper_id}/metadata")
async def get_paper_metadata(paper_id: str) -> dict[str, Any]:
    return await service.get_paper_metadata(paper_id)


@router.get("/{paper_id}/parse-runs")
async def list_parse_runs(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_parse_runs(paper_id)


@router.get("/{paper_id}/embedding-runs")
async def list_embedding_runs(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_embedding_runs(paper_id)


@router.post("/{paper_id}/analysis-runs", status_code=status.HTTP_202_ACCEPTED)
async def create_analysis_run(paper_id: str) -> dict[str, Any]:
    return await service.create_analysis_run(paper_id)


@router.get("/{paper_id}/analysis-runs")
async def list_analysis_runs(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_analysis_runs(paper_id)


@router.get("/{paper_id}/analysis-runs/{run_id}")
async def get_analysis_run(paper_id: str, run_id: str) -> dict[str, Any]:
    return await service.get_analysis_run(paper_id, run_id)


@router.post("/{paper_id}/analysis-runs/{run_id}/cancel")
async def cancel_analysis_run(paper_id: str, run_id: str) -> dict[str, Any]:
    return await service.cancel_analysis_run(paper_id, run_id)


@router.get("/{paper_id}/elements")
async def list_document_elements(
    paper_id: str,
    element_type: str | None = Query(None, alias="type"),
    page: int | None = Query(None, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return await service.list_document_elements(
        paper_id=paper_id,
        element_type=element_type,
        page=page,
        limit=limit,
    )


@router.get("/{paper_id}/tables")
async def list_document_tables(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_document_tables(paper_id)


@router.patch("/{paper_id}")
async def update_paper(
    paper_id: str,
    title: str | None = Body(None),
    authors: str | None = Body(None),
    year: int | None = Body(None),
    doi: str | None = Body(None),
    arxiv_id: str | None = Body(None),
    pubmed_id: str | None = Body(None),
    venue: str | None = Body(None),
    abstract: str | None = Body(None),
    citation: str | None = Body(None),
    user_tags: str | None = Body(None),
    relation_to_idea: str | None = Body(None),
) -> dict[str, Any]:
    return await service.update_paper(
        paper_id=paper_id,
        title=title,
        authors=authors,
        year=year,
        doi=doi,
        arxiv_id=arxiv_id,
        pubmed_id=pubmed_id,
        venue=venue,
        abstract=abstract,
        citation=citation,
        user_tags=user_tags,
        relation_to_idea=relation_to_idea,
    )


@router.post("/{paper_id}/parse")
async def parse_paper(paper_id: str) -> dict[str, Any]:
    return await service.parse_paper(paper_id)


@router.get("/{paper_id}/passages")
async def list_passages(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_passages(paper_id)


@router.get("/{paper_id}/cards")
async def list_paper_cards(
    paper_id: str,
    card_type: str | None = None,
) -> list[dict[str, Any]]:
    return await service.list_paper_cards(paper_id, card_type=card_type)


@router.post("/{paper_id}/cards")
async def create_paper_card(
    paper_id: str,
    card_type: str = Body(...),
    summary: str = Body(""),
    source_passage_id: str | None = Body(None),
    confidence: float = Body(1.0),
) -> dict[str, Any]:
    return await service.create_paper_card(
        paper_id=paper_id,
        card_type=card_type,
        summary=summary,
        source_passage_id=source_passage_id,
        confidence=confidence,
    )


@router.get("/{paper_id}/cards/{card_id}")
async def get_paper_card(paper_id: str, card_id: str) -> dict[str, Any]:
    return await service.get_paper_card(paper_id, card_id)


@router.patch("/{paper_id}/cards/{card_id}")
async def update_paper_card(
    paper_id: str,
    card_id: str,
    summary: str | None = Body(None),
    card_type: str | None = Body(None),
    confidence: float | None = Body(None),
) -> dict[str, Any]:
    return await service.update_paper_card(
        paper_id=paper_id,
        card_id=card_id,
        summary=summary,
        card_type=card_type,
        confidence=confidence,
    )


@router.delete("/{paper_id}/cards/{card_id}")
async def delete_paper_card(paper_id: str, card_id: str) -> dict[str, str]:
    return await service.delete_paper_card(paper_id, card_id)


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str) -> dict[str, str]:
    return await service.delete_paper(paper_id)
