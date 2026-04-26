"""Built-in Agent logic for deep paper analysis and card extraction."""

import uuid
import json
from typing import Any
from db import get_connection
from llm_client import call_llm

SYSTEM_PROMPT = """你是一位专业的学术科研助理。你的任务是分析提供的论文片段，并提取结构化的信息。

输出语言要求：
无论论文原文使用的是什么语言（如英文），你必须使用【准确、专业、精炼的中文】来填写 JSON 中的所有内容。

OUTPUT FORMAT:
You MUST return a JSON object with the following structure:
{
  "metadata": {
    "title": "论文的真实标题",
    "authors": "完整的作者列表",
    "year": 2024,
    "abstract": "一句话极简总结（TL;DR），不超过 150 字",
    "venue": "发表渠道（如 CVPR 2023, Nature 等）",
    "doi": "DOI 标识符（如果找到）",
    "relation_to_idea": "必须是以下之一: baseline, inspiring, background, result_comparison, unclassified"
  },
  "knowledge_cards": [
    {
      "card_type": "Method",
      "summary": "对特定方法、模型或实验步骤的精炼中文总结。",
      "source_passage_index": 0
    }
  ]
}

RESEARCH RELATION TYPES:
- baseline: 作为你研究基础的理论或基准模型。
- inspiring: 提供了有趣的思路或见解。
- result_comparison: 用于与你的结果进行对比。
- background: 一般背景信息。

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
