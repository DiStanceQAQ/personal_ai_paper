"""Built-in Agent logic for deep paper analysis and card extraction."""

import uuid
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
    "abstract": "Brief summary"
  },
  "knowledge_cards": [
    {
      "card_type": "Method",
      "summary": "Concise summary of a specific method, model, or approach used.",
      "source_passage_index": 0
    },
    ...
  ]
}

CARD TYPES:
- Method: Models, algorithms, or experimental procedures.
- Metric: Evaluation indicators (e.g., RMSE, F1-score).
- Result: Quantitative or qualitative findings.
- Limitation: Constraints, weaknesses, or future work.
- Claim: The main arguments or hypotheses made by the authors.
- Failure Mode: Specific cases where the method didn't work.

GUIDELINES:
1. Extract at most 10-15 most important cards.
2. Ensure summaries are technical and precise.
3. If information is missing, use empty strings or 0.
"""

async def analyze_paper_with_llm(paper_id: str, space_id: str) -> dict[str, Any]:
    """Execute deep analysis using the configured LLM."""
    conn = get_connection()
    try:
        # 1. Fetch passages
        rows = conn.execute(
            "SELECT id, original_text FROM passages WHERE paper_id = ? ORDER BY page_number, paragraph_index LIMIT 50",
            (paper_id,)
        ).fetchall()
        
        if not rows:
            return {"status": "error", "message": "No passages found. Please parse PDF first."}
            
        passages = [dict(r) for r in rows]
        
        # 2. Prepare user prompt
        text_content = "\n\n".join([f"[{i}] {p['original_text']}" for i, p in enumerate(passages)])
        user_prompt = f"Here are the passages from the paper (indices in brackets):\n\n{text_content}"
        
        # 3. Call LLM
        result = await call_llm(SYSTEM_PROMPT, user_prompt)
        
        # 4. Persistence
        # Update metadata
        meta = result.get("metadata", {})
        conn.execute(
            "UPDATE papers SET title = ?, authors = ?, year = ?, abstract = ? WHERE id = ?",
            (meta.get("title", ""), meta.get("authors", ""), meta.get("year", 0), meta.get("abstract", ""), paper_id)
        )
        
        # Insert cards
        cards = result.get("knowledge_cards", [])
        for c in cards:
            card_id = str(uuid.uuid4())
            # Map source_passage_index to actual passage ID
            idx = c.get("source_passage_index", -1)
            source_id = passages[idx]["id"] if 0 <= idx < len(passages) else None
            
            conn.execute(
                """INSERT INTO knowledge_cards (id, space_id, paper_id, source_passage_id, card_type, summary, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, 0.95)""",
                (card_id, space_id, paper_id, source_id, c.get("card_type", "Method"), c.get("summary", ""))
            )
            
        conn.commit()
        return {"status": "success", "card_count": len(cards)}
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()
