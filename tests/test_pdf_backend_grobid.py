from __future__ import annotations

import sqlite3
import tomllib
from pathlib import Path

import httpx
import pytest


HEADER_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">Optional Scholarly Parsing</title>
        <author>
          <persName><forename>Ada</forename><surname>Lovelace</surname></persName>
        </author>
        <author>
          <persName><forename>Alan</forename><surname>Turing</surname></persName>
        </author>
      </titleStmt>
      <publicationStmt>
        <publisher>Journal of Parser Systems</publisher>
        <date when="2024-03-15">2024</date>
      </publicationStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <title level="a">Optional Scholarly Parsing</title>
            <idno type="DOI">10.1234/example</idno>
          </analytic>
          <monogr>
            <title level="j">Journal of Parser Systems</title>
            <imprint><date when="2024"/></imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract>
        <p>We parse TEI metadata from a mocked GROBID response.</p>
      </abstract>
    </profileDesc>
  </teiHeader>
</TEI>
"""


FULLTEXT_TEI = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">Optional Scholarly Parsing</title>
        <author>
          <persName><forename>Ada</forename><surname>Lovelace</surname></persName>
        </author>
      </titleStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <title level="a">Optional Scholarly Parsing</title>
            <idno type="DOI">10.1234/example</idno>
          </analytic>
          <monogr>
            <title level="j">Journal of Parser Systems</title>
            <imprint><date when="2024"/></imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
    <profileDesc>
      <abstract><p>Fulltext abstract.</p></abstract>
    </profileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head n="1">Introduction</head>
        <p>GROBID provides structured scholarly text.</p>
      </div>
      <div>
        <head n="2">Methods</head>
        <p>We use mocked HTTP transports for repeatable tests.</p>
        <p>TEI parsing is namespace-insensitive.</p>
      </div>
    </body>
    <back>
      <listBibl>
        <biblStruct xml:id="b0">
          <analytic>
            <title level="a">A Referenced Paper</title>
            <author>
              <persName><forename>Grace</forename><surname>Hopper</surname></persName>
            </author>
            <idno type="DOI">10.9999/ref</idno>
          </analytic>
          <monogr>
            <title level="j">Computing Notes</title>
            <imprint><date when="1952"/></imprint>
          </monogr>
          <note type="raw_reference">Hopper, G. A Referenced Paper. Computing Notes. 1952.</note>
        </biblStruct>
      </listBibl>
    </back>
  </text>
</TEI>
"""


def test_pyproject_declares_grobid_module() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["tool"]["setuptools"]["packages"]["find"]["include"] == [
        "paper_engine*"
    ]


def test_is_alive_returns_true_for_grobid_status_ok() -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/isalive"
        return httpx.Response(200, text="true")

    client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert client.is_alive() is True


def test_is_alive_returns_false_for_down_or_unreachable_service() -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient

    down_client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(503))
        ),
    )
    unreachable_client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("offline"))
            )
        ),
    )

    assert down_client.is_alive() is False
    assert unreachable_client.is_alive() is False


def test_process_header_posts_pdf_and_extracts_metadata(tmp_path: Path) -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/processHeaderDocument"
        body = request.read()
        assert b"%PDF-1.7" in body
        assert b'filename="paper.pdf"' in body
        return httpx.Response(200, text=HEADER_TEI)

    client = GrobidClient(
        "http://grobid.test/",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    metadata = client.process_header(pdf_path)

    assert metadata.title == "Optional Scholarly Parsing"
    assert metadata.authors == ["Ada Lovelace", "Alan Turing"]
    assert metadata.year == 2024
    assert metadata.venue == "Journal of Parser Systems"
    assert metadata.doi == "10.1234/example"
    assert metadata.abstract == "We parse TEI metadata from a mocked GROBID response."


def test_process_header_falls_back_to_analytic_authors(tmp_path: Path) -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt>
        <title level="a" type="main">Analytic Author Metadata</title>
      </titleStmt>
      <sourceDesc>
        <biblStruct>
          <analytic>
            <title level="a">Analytic Author Metadata</title>
            <author>
              <persName><forename>Katherine</forename><surname>Johnson</surname></persName>
            </author>
            <author>
              <persName><forename>Dorothy</forename><surname>Vaughan</surname></persName>
            </author>
            <idno type="DOI">10.5555/analytic</idno>
          </analytic>
          <monogr>
            <title level="j">TEI Journal</title>
            <imprint><date when="2023"/></imprint>
          </monogr>
        </biblStruct>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
</TEI>
"""

    client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=tei))
        ),
    )

    metadata = client.process_header(pdf_path)

    assert metadata.title == "Analytic Author Metadata"
    assert metadata.authors == ["Katherine Johnson", "Dorothy Vaughan"]
    assert metadata.year == 2023
    assert metadata.venue == "TEI Journal"
    assert metadata.doi == "10.5555/analytic"


def test_process_fulltext_extracts_sections_references_and_raw_tei(tmp_path: Path) -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/processFulltextDocument"
        return httpx.Response(200, text=FULLTEXT_TEI)

    client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.process_fulltext(pdf_path)

    assert result.metadata.title == "Optional Scholarly Parsing"
    assert result.metadata.authors == ["Ada Lovelace"]
    assert result.metadata.year == 2024
    assert result.metadata.venue == "Journal of Parser Systems"
    assert result.metadata.doi == "10.1234/example"
    assert result.metadata.abstract == "Fulltext abstract."
    assert [(section.heading, section.text) for section in result.sections] == [
        ("Introduction", "GROBID provides structured scholarly text."),
        (
            "Methods",
            "We use mocked HTTP transports for repeatable tests.\n\n"
            "TEI parsing is namespace-insensitive.",
        ),
    ]
    assert len(result.references) == 1
    assert result.references[0].id == "b0"
    assert result.references[0].title == "A Referenced Paper"
    assert result.references[0].authors == ["Grace Hopper"]
    assert result.references[0].year == 1952
    assert result.references[0].venue == "Computing Notes"
    assert result.references[0].doi == "10.9999/ref"
    assert result.references[0].raw_text == (
        "Hopper, G. A Referenced Paper. Computing Notes. 1952."
    )
    assert result.raw_tei == FULLTEXT_TEI


def test_fulltext_reference_falls_back_to_monograph_fields() -> None:
    from paper_engine.pdf.backends.grobid import parse_grobid_fulltext

    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a">Paper With Book Reference</title></titleStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <back>
      <listBibl>
        <biblStruct xml:id="book-1">
          <monogr>
            <author>
              <persName><forename>Donald</forename><surname>Knuth</surname></persName>
            </author>
            <title level="m">The Art of Computer Programming</title>
            <imprint>
              <publisher>Addison-Wesley</publisher>
              <date when="1968"/>
            </imprint>
          </monogr>
          <note type="raw_reference">Knuth, D. The Art of Computer Programming. Addison-Wesley. 1968.</note>
        </biblStruct>
      </listBibl>
    </back>
  </text>
</TEI>
"""

    result = parse_grobid_fulltext(tei)

    assert len(result.references) == 1
    assert result.references[0].id == "book-1"
    assert result.references[0].title == "The Art of Computer Programming"
    assert result.references[0].authors == ["Donald Knuth"]
    assert result.references[0].year == 1968
    assert result.references[0].venue == "Addison-Wesley"
    assert result.references[0].raw_text == (
        "Knuth, D. The Art of Computer Programming. Addison-Wesley. 1968."
    )


def test_fulltext_nested_sections_do_not_duplicate_child_paragraphs() -> None:
    from paper_engine.pdf.backends.grobid import parse_grobid_fulltext

    tei = """<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <fileDesc>
      <titleStmt><title level="a">Nested Section Paper</title></titleStmt>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div>
        <head>Results</head>
        <p>Parent result paragraph.</p>
        <div>
          <head>Ablation</head>
          <p>Child ablation paragraph.</p>
        </div>
      </div>
    </body>
  </text>
</TEI>
"""

    result = parse_grobid_fulltext(tei)

    assert [(section.heading, section.text) for section in result.sections] == [
        ("Results", "Parent result paragraph."),
        ("Ablation", "Child ablation paragraph."),
    ]
    all_section_text = "\n".join(section.text for section in result.sections)
    assert all_section_text.count("Parent result paragraph.") == 1
    assert all_section_text.count("Child ablation paragraph.") == 1


def test_process_header_wraps_http_failures(tmp_path: Path) -> None:
    from paper_engine.pdf.backends.grobid import GrobidClient, GrobidClientError

    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    client = GrobidClient(
        "http://grobid.test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ),
    )

    with pytest.raises(GrobidClientError, match="processHeaderDocument"):
        client.process_header(pdf_path)


def test_get_configured_grobid_client_uses_optional_app_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.grobid as pdf_backend_grobid
    from paper_engine.pdf.backends.grobid import GrobidClient, get_configured_grobid_client

    def connection_with_value(value: str | None) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        if value is not None:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?)",
                ("grobid_base_url", value),
            )
        conn.commit()
        return conn

    monkeypatch.setattr(
        pdf_backend_grobid, "get_connection", lambda: connection_with_value(None)
    )
    assert get_configured_grobid_client() is None

    monkeypatch.setattr(
        pdf_backend_grobid, "get_connection", lambda: connection_with_value("   ")
    )
    assert get_configured_grobid_client() is None

    monkeypatch.setattr(
        pdf_backend_grobid,
        "get_connection",
        lambda: connection_with_value(" http://grobid.test/ "),
    )
    configured = get_configured_grobid_client()

    assert isinstance(configured, GrobidClient)
    assert configured.base_url == "http://grobid.test"


def test_get_configured_grobid_client_treats_missing_app_state_as_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import paper_engine.pdf.backends.grobid as pdf_backend_grobid
    from paper_engine.pdf.backends.grobid import get_configured_grobid_client

    def connection_without_app_state() -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(
        pdf_backend_grobid,
        "get_connection",
        connection_without_app_state,
    )

    assert get_configured_grobid_client() is None
