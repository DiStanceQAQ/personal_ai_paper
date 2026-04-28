from pathlib import Path


def test_paper_service_no_longer_imports_route_parse() -> None:
    source = Path("paper_engine/papers/service.py").read_text(encoding="utf-8")

    assert "route_parse" not in source
    assert "paper_engine.pdf.router" not in source
