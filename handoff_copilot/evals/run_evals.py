"""Eval harness: runs the Summarizer and the red-flag rule engine directly
against a golden set of synthetic patient timelines (bypassing Pinecone and
the A2A hop entirely, so this is fast, free of network flakiness, and never
touches the real vector index).

Two scores, deliberately kept separate:
  - Faithfulness: the SAME deterministic guardrail (app.security) used in
    production — did the summary invent a drug/dose not in the notes? This
    is the authoritative score.
  - Recall: did the deterministic rule engine (redflag_service.rules) catch
    every flag type this case expects?
An LLM-as-judge completeness score is also collected, but explicitly
advisory: it is never used to decide pass/fail, per the project's rule that
an LLM must not be the final source of truth for a correctness judgment.

Usage:
    python -m evals.run_evals
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.summarizer import summarize  # noqa: E402
from app.llm import get_chat_model  # noqa: E402
from app.security import validate_summary_against_sources  # noqa: E402
from redflag_service.rules import check_red_flags  # noqa: E402
from schemas import NoteType, StoredNote  # noqa: E402

GOLDEN_SET_PATH = Path(__file__).parent / "golden_set.json"
RESULTS_PATH = Path(__file__).parent / "results.json"


def _load_cases() -> list[dict]:
    return json.loads(GOLDEN_SET_PATH.read_text(encoding="utf-8"))


def _build_notes(case: dict) -> list[StoredNote]:
    notes = []
    for i, raw in enumerate(case["notes"]):
        notes.append(StoredNote(
            note_id=f"{case['id']}-n{i}",
            patient_id=case["id"],
            author=raw.get("author", "dr_a"),
            note_type=NoteType(raw.get("note_type", "progress")),
            text=raw["text"],
            created_at=datetime.now(timezone.utc),
        ))
    return notes


def _summary_text(summary) -> str:
    return " ".join([summary.situation, summary.background, summary.assessment, summary.recommendation])


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(block.get("text", "") for block in content if isinstance(block, dict))
    return str(content)


async def _llm_judge(summary_text: str, notes_text: str) -> int | None:
    """Advisory completeness rating only — never used to decide pass/fail."""
    prompt = (
        "Rate this SBAR summary 1-5 for how completely it captures the "
        "clinically important information in the notes below "
        "(1=missed important info, 5=captures everything important). "
        f"Respond with ONLY a single digit.\n\nNOTES:\n{notes_text}\n\nSUMMARY:\n{summary_text}"
    )
    try:
        response = await get_chat_model().ainvoke(prompt)
        text = _content_text(response.content)
        digits = [c for c in text if c.isdigit()]
        return int(digits[0]) if digits else None
    except Exception:
        return None


async def run_case(case: dict) -> dict:
    notes = _build_notes(case)
    notes_text = "\n".join(n.text for n in notes)

    summary = await summarize(notes)
    summary_text = _summary_text(summary)

    faithfulness = validate_summary_against_sources(summary_text, [n.text for n in notes])

    found_flags = check_red_flags(notes)
    found_types = sorted({f.type.value for f in found_flags})
    expected_types = sorted(set(case.get("expected_flag_types", [])))
    missed = sorted(set(expected_types) - set(found_types))
    recall_hit = not missed

    judge_score = await _llm_judge(summary_text, notes_text)

    return {
        "id": case["id"],
        "faithful": faithfulness.passed,
        "faithfulness_reasons": faithfulness.reasons,
        "recall_hit": recall_hit,
        "expected_flag_types": expected_types,
        "found_flag_types": found_types,
        "missed_flag_types": missed,
        "llm_judge_completeness": judge_score,
        "summary": summary_text,
    }


async def main() -> None:
    cases = _load_cases()
    results = await asyncio.gather(*(run_case(c) for c in cases))

    total = len(results)
    faithful_count = sum(r["faithful"] for r in results)
    recall_count = sum(r["recall_hit"] for r in results)
    judge_scores = [r["llm_judge_completeness"] for r in results if r["llm_judge_completeness"] is not None]

    print(f"{'case':<32} {'faithful':<10} {'recall':<8} {'judge':<6} missed")
    for r in results:
        print(f"{r['id']:<32} {str(r['faithful']):<10} {str(r['recall_hit']):<8} {str(r['llm_judge_completeness']):<6} {r['missed_flag_types']}")

    print()
    print(f"Faithfulness (no hallucinated meds/doses): {faithful_count}/{total} ({faithful_count/total:.0%})")
    print(f"Recall (all expected flags caught):        {recall_count}/{total} ({recall_count/total:.0%})")
    if judge_scores:
        print(f"LLM-judge completeness (advisory, 1-5 avg): {sum(judge_scores)/len(judge_scores):.2f}")

    RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nFull results written to {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
