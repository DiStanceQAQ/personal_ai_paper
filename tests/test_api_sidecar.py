"""Tests for packaged API sidecar entry point."""

from pathlib import Path

from api_sidecar import ServerSettings, parse_args


def test_parse_args_uses_defaults() -> None:
    settings = parse_args([])
    assert settings == ServerSettings(
        host="127.0.0.1",
        port=8765,
        data_dir=None,
    )


def test_parse_args_accepts_port_and_data_dir(tmp_path: Path) -> None:
    settings = parse_args([
        "--host",
        "127.0.0.1",
        "--port",
        "9412",
        "--data-dir",
        str(tmp_path),
    ])
    assert settings.host == "127.0.0.1"
    assert settings.port == 9412
    assert settings.data_dir == tmp_path.resolve()
