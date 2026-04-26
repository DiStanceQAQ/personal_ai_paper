"""Built-in Agent logic for deep paper analysis and card extraction."""

import uuid
import json
from typing import Any
from db import get_connection
from llm_client import call_llm

SYSTEM_PROMPT = """You are an expert scientific research assistant. Your task is to analyze the provided text passages from a research paper and extract structured information.

OUTPUT FORMAT:
You MUST return a JSON object with the following structure:
{
  "metadata": {
    "title": "Actual paper title",
    "authors": "Full author list",
    "year": 2024,
    "abstract": "Brief one-sentence summary (TL;DR)",
    "venue": "Publication venue (e.g. CVPR 2023, Nature, arXiv)",
    "doi": "Digital Object Identifier if found",
    "relation_to_idea": "One of: baseline, inspiring, background, result_comparison, unclassified"
  },
  "knowledge_cards": [
    {
      "card_type": "Method",
      "summary": "Concise summary of a specific method, model, or approach used.",
      "source_passage_index": 0
    }
  ]
}

RESEARCH RELATION TYPES:
- baseline: The paper provides the fundamental theory or model you are building upon.
- inspiring: The paper provides interesting ideas or insights for your research.
- result_comparison: This paper's results are used as a comparison for yours.
- background: General contextual information.

CARD TYPES:
- Method, Metric, Result, Limitation, Claim, Failure Mode.

GUIDELINES:
1. Extract at most 10-15 most important cards.
2. The abstract should be a highly compressed summary (max 150 chars).
"""

async def analyze_paper_with_llm(paper_id: str, space_id: str) -> dict[str, Any]:
    """Execute deep analysis using the configured LLM."""
    print(f"\n[Agent] 🚀 开始深度分析论文: {paper_id}")
    conn = get_connection()
    try:
        # 1. Fetch passages
        rows = conn.execute(
            "SELECT id, original_text FROM passages WHERE paper_id = ? ORDER BY page_number, paragraph_index LIMIT 60",
            (paper_id,)
        ).fetchall()
        
        if not rows:
            print(f"[Agent] ❌ 错误: 未找到该论文的片段。请先执行 PDF 原文解析。")
            return {"status": "error", "message": "No passages found. Please parse PDF first."}
            
        passages = [dict(r) for r in rows]
        print(f"[Agent] 📚 已加载 {len(passages)} 个原文片段用于分析。")
        
        # 2. Prepare user prompt
        text_content = "\n\n".join([f"[{i}] {p['original_text']}" for i, p in enumerate(passages)])
        user_prompt = f"Analyze this paper and extract metadata and knowledge cards:\n\n{text_content}"
        
        # 3. Call LLM
        print("[Agent] 🤖 正在调用 LLM API...")
        result = await call_llm(SYSTEM_PROMPT, user_prompt)
        print(f"[Agent] ✅ LLM 响应成功。识别标题: {result.get('metadata', {}).get('title')}")
        
        # 4. Persistence
        meta = result.get("metadata", {})
        
        # Validate relation_to_idea value against DB constraints
        valid_relations = ['supports', 'refutes', 'inspires', 'baseline', 'method_source', 'background', 'result_comparison', 'unclassified']
        rel = meta.get("relation_to_idea", "unclassified").lower()
        if rel not in valid_relations:
            # Simple mapping to valid DB values
            mapping = {
                "inspiring": "inspires",
                "result_comparison": "result_comparison",
                "baseline": "baseline"
            }
            rel = mapping.get(rel, "unclassified")

        conn.execute(
            """UPDATE papers SET 
               title = ?, authors = ?, year = ?, abstract = ?, 
               venue = ?, doi = ?, relation_to_idea = ? 
               WHERE id = ?""",
            (
                meta.get("title", ""), 
                meta.get("authors", ""), 
                meta.get("year", 0), 
                meta.get("abstract", ""), 
                meta.get("venue", ""),
                meta.get("doi", ""),
                rel,
                paper_id
            )
        )
        
        # Clear existing cards to avoid duplicates
        conn.execute("DELETE FROM knowledge_cards WHERE paper_id = ?", (paper_id,))
        
        # Insert cards
        cards = result.get("knowledge_cards", [])
        print(f"[Agent] 💾 正在保存 {len(cards)} 张知识卡片...")
        for c in cards:
            card_id = str(uuid.uuid4())
            idx = c.get("source_passage_index", -1)
            source_id = passages[idx]["id"] if 0 <= idx < len(passages) else None
            
            conn.execute(
                """INSERT INTO knowledge_cards (id, space_id, paper_id, source_passage_id, card_type, summary, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, 0.95)""",
                (card_id, space_id, paper_id, source_id, c.get("card_type", "Method"), c.get("summary", ""))
            )
            
        conn.commit()
        print("[Agent] ✨ 分析任务圆满完成。")
        return {"status": "success", "card_count": len(cards)}
        
    except Exception as e:
        print(f"[Agent] 💥 解析过程中发生崩溃!")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()
