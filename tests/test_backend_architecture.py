"""Architecture checks for backend package boundaries."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = ROOT / "paper_engine" / "api" / "routes"


def test_routes_do_not_open_database_connections_directly() -> None:
    offenders: list[str] = []
    for route_file in sorted(ROUTES_DIR.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        source = route_file.read_text(encoding="utf-8")
        if "get_connection(" in source or "sqlite3.connect" in source:
            offenders.append(route_file.name)

    assert offenders == []


def test_routes_do_not_contain_large_sql_blocks() -> None:
    offenders: list[str] = []
    for route_file in sorted(ROUTES_DIR.glob("*.py")):
        if route_file.name == "__init__.py":
            continue
        source = route_file.read_text(encoding="utf-8")
        if (
            "SELECT " in source
            or "INSERT " in source
            or "UPDATE " in source
            or "DELETE " in source
        ):
            offenders.append(route_file.name)

    assert offenders == []
