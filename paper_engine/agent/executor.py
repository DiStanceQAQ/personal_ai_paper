"""Built-in Agent logic for deep paper analysis and card extraction."""

from typing import Any

from paper_engine.analysis.pipeline import PaperAnalysisRunResult, run_paper_analysis


def _analysis_success_payload(analysis: PaperAnalysisRunResult) -> dict[str, Any]:
    result = analysis.result
    accepted_card_count = result.quality.accepted_card_count
    if accepted_card_count == 0 and result.cards:
        accepted_card_count = len(result.cards)

    return {
        "status": "success",
        "card_count": accepted_card_count,
        "analysis_run_id": analysis.analysis_run_id,
        "accepted_card_count": accepted_card_count,
        "rejected_card_count": result.quality.rejected_card_count,
        "metadata_confidence": result.metadata.confidence,
    }


async def analyze_paper_with_llm(paper_id: str, space_id: str) -> dict[str, Any]:
    """Execute deep analysis using the multi-stage analysis pipeline."""
    print(f"\n[Agent] 🚀 开始深度分析论文: {paper_id}")
    try:
        analysis = await run_paper_analysis(paper_id, space_id)
        payload = _analysis_success_payload(analysis)
        print(
            "[Agent] ✨ 分析任务完成。"
            f" run={payload['analysis_run_id']} cards={payload['accepted_card_count']}"
        )
        return payload
    except Exception as e:
        print(f"[Agent] 💥 解析过程中发生崩溃!")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
