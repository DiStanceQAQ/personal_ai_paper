"""Optional HTTP client and TEI parser for GROBID scholarly metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Iterable
import xml.etree.ElementTree as ET

import httpx

from db import get_connection


DEFAULT_TIMEOUT = 30.0
XML_ID_ATTR = "{http://www.w3.org/XML/1998/namespace}id"


class GrobidClientError(Exception):
    """Raised when a GROBID HTTP call or TEI parse fails."""


@dataclass(frozen=True)
class GrobidMetadata:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    abstract: str = ""


@dataclass(frozen=True)
class GrobidSection:
    heading: str
    text: str


@dataclass(frozen=True)
class GrobidReference:
    id: str = ""
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    doi: str = ""
    raw_text: str = ""


@dataclass(frozen=True)
class GrobidParseResult:
    metadata: GrobidMetadata
    sections: list[GrobidSection]
    references: list[GrobidReference]
    raw_tei: str


class GrobidClient:
    """Small synchronous client for an optional GROBID service."""

    def __init__(
        self,
        base_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def is_alive(self) -> bool:
        """Return whether the configured GROBID service reports healthy."""
        try:
            response = self._client.get(self._url("api/isalive"))
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def process_header(self, file_path: Path | str) -> GrobidMetadata:
        """POST a PDF to GROBID's header endpoint and parse metadata TEI."""
        tei = self._post_pdf("api/processHeaderDocument", Path(file_path))
        return parse_grobid_metadata(tei)

    def process_fulltext(self, file_path: Path | str) -> GrobidParseResult:
        """POST a PDF to GROBID's fulltext endpoint and parse returned TEI."""
        tei = self._post_pdf("api/processFulltextDocument", Path(file_path))
        return parse_grobid_fulltext(tei)

    def close(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            self._client.close()

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"

    def _post_pdf(self, endpoint: str, file_path: Path) -> str:
        try:
            with file_path.open("rb") as pdf_file:
                response = self._client.post(
                    self._url(endpoint),
                    files={"input": (file_path.name, pdf_file, "application/pdf")},
                )
            response.raise_for_status()
        except (OSError, httpx.HTTPError) as exc:
            raise GrobidClientError(f"GROBID {endpoint} request failed") from exc
        return response.text


def get_configured_grobid_client() -> GrobidClient | None:
    """Return a configured optional client, or None when no base URL is stored."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            ("grobid_base_url",),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    base_url = str(row["value"]).strip()
    if not base_url:
        return None
    return GrobidClient(base_url)


def parse_grobid_fulltext(tei: str) -> GrobidParseResult:
    """Parse GROBID fulltext TEI into metadata, sections, and references."""
    root = _parse_xml(tei)
    return GrobidParseResult(
        metadata=_extract_metadata(root),
        sections=_extract_sections(root),
        references=_extract_references(root),
        raw_tei=tei,
    )


def parse_grobid_metadata(tei: str) -> GrobidMetadata:
    """Parse GROBID header TEI into scholarly metadata."""
    return _extract_metadata(_parse_xml(tei))


def _parse_xml(tei: str) -> ET.Element:
    try:
        return ET.fromstring(tei)
    except ET.ParseError as exc:
        raise GrobidClientError("GROBID returned invalid TEI XML") from exc


def _extract_metadata(root: ET.Element) -> GrobidMetadata:
    header = _first_descendant(root, "teiHeader")
    scope = header if header is not None else root
    title_stmt = _first_descendant(scope, "titleStmt")
    source_desc = _first_descendant(scope, "sourceDesc")
    publication_stmt = _first_descendant(scope, "publicationStmt")
    profile_desc = _first_descendant(scope, "profileDesc")
    bibl = _first_descendant(source_desc, "biblStruct")
    analytic = _first_child(bibl, "analytic")

    return GrobidMetadata(
        title=_metadata_title(title_stmt, source_desc),
        authors=_metadata_authors(title_stmt, analytic),
        year=_first_year(scope),
        venue=_metadata_venue(source_desc, publication_stmt),
        doi=_first_doi(scope),
        abstract=_text_content(_first_descendant(profile_desc, "abstract")),
    )


def _metadata_title(
    title_stmt: ET.Element | None,
    source_desc: ET.Element | None,
) -> str:
    title = _first_title(title_stmt, preferred_level="a")
    if title:
        return title
    bibl = _first_descendant(source_desc, "biblStruct")
    analytic = _first_child(bibl, "analytic")
    return _first_title(analytic, preferred_level="a")


def _metadata_venue(
    source_desc: ET.Element | None,
    publication_stmt: ET.Element | None,
) -> str:
    bibl = _first_descendant(source_desc, "biblStruct")
    monogr = _first_child(bibl, "monogr")
    venue = _first_title(monogr, preferred_level="j")
    if venue:
        return venue
    return _text_content(_first_descendant(publication_stmt, "publisher"))


def _metadata_authors(
    title_stmt: ET.Element | None,
    analytic: ET.Element | None,
) -> list[str]:
    authors = _authors_from_parent(title_stmt)
    if authors:
        return authors
    return _authors_from_parent(analytic)


def _extract_sections(root: ET.Element) -> list[GrobidSection]:
    body = _first_descendant(root, "body")
    if body is None:
        return []

    sections: list[GrobidSection] = []
    for div in _descendants(body, "div"):
        heading = _text_content(_first_child(div, "head"))
        paragraphs = [_text_content(paragraph) for paragraph in _children(div, "p")]
        text = "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
        if heading or text:
            sections.append(GrobidSection(heading=heading, text=text))
    return sections


def _extract_references(root: ET.Element) -> list[GrobidReference]:
    references: list[GrobidReference] = []
    for bibl in _descendants(root, "biblStruct"):
        if _is_header_bibl(root, bibl):
            continue
        analytic = _first_child(bibl, "analytic")
        monogr = _first_child(bibl, "monogr")
        analytic_title = _first_title(analytic, preferred_level="a")
        title = analytic_title or _first_title(monogr, preferred_level="m")
        references.append(
            GrobidReference(
                id=_attribute(bibl, "id"),
                title=title,
                authors=_reference_authors(analytic, monogr),
                year=_first_year(bibl),
                venue=_reference_venue(
                    monogr,
                    has_analytic_title=bool(analytic_title),
                ),
                doi=_first_doi(bibl),
                raw_text=_raw_reference_text(bibl),
            )
        )
    return references


def _reference_authors(
    analytic: ET.Element | None,
    monogr: ET.Element | None,
) -> list[str]:
    authors = _authors_from_parent(analytic)
    if authors:
        return authors
    return _authors_from_parent(monogr)


def _reference_venue(
    monogr: ET.Element | None,
    *,
    has_analytic_title: bool,
) -> str:
    if has_analytic_title:
        return _first_title(monogr, preferred_level="j")
    return _text_content(_first_descendant(monogr, "publisher"))


def _is_header_bibl(root: ET.Element, bibl: ET.Element) -> bool:
    header = _first_descendant(root, "teiHeader")
    if header is None:
        return False
    return any(candidate is bibl for candidate in _descendants(header, "biblStruct"))


def _raw_reference_text(bibl: ET.Element) -> str:
    for note in _descendants(bibl, "note"):
        if _attribute(note, "type").lower() == "raw_reference":
            return _text_content(note)
    return _text_content(bibl)


def _authors_from_parent(parent: ET.Element | None) -> list[str]:
    if parent is None:
        return []
    authors: list[str] = []
    for author in _children(parent, "author"):
        name = _format_author(author)
        if name:
            authors.append(name)
    return authors


def _format_author(author: ET.Element) -> str:
    pers_name = _first_descendant(author, "persName")
    if pers_name is None:
        return _text_content(author)
    parts = [
        _text_content(child)
        for child in pers_name
        if _local_name(child.tag) in {"forename", "surname", "name"}
    ]
    name = " ".join(part for part in parts if part)
    return name or _text_content(pers_name)


def _first_title(parent: ET.Element | None, *, preferred_level: str) -> str:
    if parent is None:
        return ""
    titles = list(_descendants(parent, "title"))
    for title in titles:
        if _attribute(title, "level") == preferred_level:
            return _text_content(title)
    if titles:
        return _text_content(titles[0])
    return ""


def _first_doi(parent: ET.Element | None) -> str:
    for idno in _descendants(parent, "idno"):
        if _attribute(idno, "type").lower() == "doi":
            return _text_content(idno)
    return ""


def _first_year(parent: ET.Element | None) -> int | None:
    for date in _descendants(parent, "date"):
        year = _year_from_text(_attribute(date, "when")) or _year_from_text(
            _text_content(date)
        )
        if year is not None:
            return year
    return None


def _year_from_text(value: str) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", value)
    if match is None:
        return None
    return int(match.group(1))


def _descendants(parent: ET.Element | None, name: str) -> Iterable[ET.Element]:
    if parent is None:
        return ()
    return (element for element in parent.iter() if _local_name(element.tag) == name)


def _children(parent: ET.Element | None, name: str) -> Iterable[ET.Element]:
    if parent is None:
        return ()
    return (element for element in list(parent) if _local_name(element.tag) == name)


def _first_descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    return next(iter(_descendants(parent, name)), None)


def _first_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    return next(iter(_children(parent, name)), None)


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _attribute(element: ET.Element, name: str) -> str:
    for key, value in element.attrib.items():
        if name == "id" and key == XML_ID_ATTR:
            return value.strip()
        if _local_name(key) == name:
            return value.strip()
    return ""


def _text_content(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join(" ".join(element.itertext()).split())
