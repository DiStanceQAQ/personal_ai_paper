"""Static UI regression tests."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_inline_script_has_valid_javascript_syntax() -> None:
    """The browser UI script should parse before any interaction can work."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for JavaScript syntax validation")

    html = Path("static/index.html").read_text(encoding="utf-8")
    script_lines: list[str] = []
    in_script = False
    for line in html.splitlines():
        if "<script>" in line:
            in_script = True
            continue
        if "</script>" in line:
            in_script = False
            continue
        if in_script:
            script_lines.append(line)

    script_path = Path("/tmp/paper-engine-index-script.js")
    script_path.write_text("\n".join(script_lines), encoding="utf-8")

    result = subprocess.run(
        [node, "--check", str(script_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_static_ui_uses_chinese_core_copy() -> None:
    """Core user-facing shell copy should be localized to Chinese."""
    html = Path("static/index.html").read_text(encoding="utf-8")

    required_copy = [
        "本地论文知识引擎",
        "当前空间",
        "创建新的想法空间",
        "导入论文",
        "文献检索",
        "论文列表",
        "暂无想法空间",
    ]
    forbidden_core_copy = [
        "Local Paper Knowledge Engine",
        "Active Space:",
        "Create New Idea Space",
        "Import Papers",
        "Literature Search",
        "No idea spaces yet.",
    ]

    for copy in required_copy:
        assert copy in html
    for copy in forbidden_core_copy:
        assert copy not in html


def test_react_app_gates_shell_until_initial_data_loads() -> None:
    """Desktop UI should not show an empty workspace before startup data loads."""
    source = Path("frontend/src/App.tsx").read_text(encoding="utf-8")

    assert "initialLoadStatus" in source
    assert "正在启动论文知识引擎" in source
    assert "initialLoadStatus !== 'ready'" in source


def test_paper_hook_clears_detail_state_when_active_space_changes() -> None:
    """Switching spaces should not leave the inspector showing stale paper details."""
    source = Path("frontend/src/hooks/usePapers.ts").read_text(encoding="utf-8")
    expected_effect = """useEffect(() => {
    setSelectedPaper(null);
    setPassages([]);
    setCards([]);
  }, [activeSpaceId]);"""

    assert expected_effect in source


def test_react_card_ui_surfaces_source_grounding() -> None:
    """Knowledge cards should expose provenance without expanding every card by default."""
    card_source = Path("frontend/src/components/ui/KnowledgeCardFancy.tsx").read_text(encoding="utf-8")
    inspector_source = Path("frontend/src/components/layout/Inspector.tsx").read_text(encoding="utf-8")

    required_card_markers = [
        "card-source-summary",
        "card-evidence-details",
        "created_by",
        "evidence_json",
        "source_passage_ids",
    ]
    for marker in required_card_markers:
        assert marker in card_source

    assert "api.listPassages" in inspector_source


def test_heuristic_card_extraction_entrypoints_are_removed() -> None:
    """UI code should only trigger Agent analysis, not legacy keyword extraction."""
    html = Path("static/index.html").read_text(encoding="utf-8")
    api_source = Path("frontend/src/api.ts").read_text(encoding="utf-8")

    assert "/api/cards/extract" not in html
    assert "extractCards" not in api_source
    assert "/api/cards/extract" not in api_source
