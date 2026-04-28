"""HTTP routes for importing and managing papers."""

from typing import Any

from fastapi import APIRouter, Body, Query, UploadFile

from paper_engine.papers import service

router = APIRouter(prefix="/api/papers", tags=["papers"])


@router.post("/upload")
async def upload_paper(file: UploadFile) -> dict[str, Any]:
    return await service.upload_paper(file)


@router.get("")
async def list_papers(space_id: str | None = None) -> list[dict[str, Any]]:
    return await service.list_papers(space_id=space_id)


@router.get("/{paper_id}")
async def get_paper(paper_id: str) -> dict[str, Any]:
    return await service.get_paper(paper_id)


@router.get("/{paper_id}/parse-runs")
async def list_parse_runs(paper_id: str) -> list[dict[str, Any]]:
    return await service.list_parse_runs(paper_id)


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


@router.delete("/{paper_id}")
async def delete_paper(paper_id: str) -> dict[str, str]:
    return await service.delete_paper(paper_id)
