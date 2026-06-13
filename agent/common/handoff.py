"""
Supplier BI Agent — Shared handoff + LLM helpers
=================================================
Centralises the two things that were previously re-implemented (slightly
differently) in every agent and node:

  1. RunContext — an explicit context object passed between agents so that
     no agent re-derives scope from `date.today()`. This is what fixes the
     comment-intelligence handoff bug: the scheduler computes ONE
     analysis_month and threads it through every agent that needs it.

  2. extract_text / parse_json_response — robust parsing of Anthropic
     responses. Filters content blocks by type instead of assuming
     content[0].text, and strips markdown fences before json.loads.

Import this from the nodes/agents instead of duplicating the logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Optional


# ── Month / scope helpers ─────────────────────────────────────────────────────

def current_analysis_month(today: Optional[date] = None) -> str:
    """
    First-of-month ISO string in UTC. Use this everywhere instead of
    date.today().replace(day=1) so the comment-intelligence WRITE and the
    generate-node READ always agree, even across a midnight / timezone roll.
    """
    today = today or datetime.now(timezone.utc).date()
    return today.replace(day=1).isoformat()


# ── Explicit inter-agent context ──────────────────────────────────────────────

@dataclass
class RunContext:
    """
    The single object passed across agent boundaries.

    scheduler -> comment_intelligence : uses analysis_month
    scheduler -> run_agent (pipeline) : uses analysis_month, supplier_id, scope
    generate  -> validate             : uses analysis_month + grounding_skus

    grounding_skus is the manifest of SKUs the narrative is ALLOWED to cite.
    validate uses it to flag any SKU mentioned in the report that did not
    come from real comment-intelligence data (hallucination guard).
    """
    run_id:          Optional[str] = None
    analysis_month:  str            = field(default_factory=current_analysis_month)
    supplier_id:     Optional[str]  = None
    date_from:       Optional[str]  = None
    date_to:         Optional[str]  = None
    grounding_skus:  list           = field(default_factory=list)
    min_ci_confidence: Optional[float] = None  # lowest confidence among loaded CI rows

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_state(cls, state: dict) -> "RunContext":
        """Rebuild a RunContext from a LangGraph AgentState dict."""
        return cls(
            run_id          = state.get("run_id"),
            analysis_month  = state.get("analysis_month") or current_analysis_month(),
            supplier_id     = state.get("supplier_id"),
            date_from       = state.get("date_from"),
            date_to         = state.get("date_to"),
            grounding_skus  = state.get("grounding_skus") or [],
            min_ci_confidence = state.get("min_ci_confidence"),
        )


# ── Robust Anthropic response parsing ─────────────────────────────────────────

def extract_text(response) -> str:
    """
    Concatenate all text blocks from an Anthropic Messages response.
    Never assumes content[0].text. Returns "" if there is no text block
    (e.g. a refusal or an empty content list) instead of raising.
    """
    content = getattr(response, "content", None) or []
    parts = []
    for block in content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        # drop the opening fence line and any trailing fence
        raw = raw.split("```", 2)
        # raw == ['', 'json\n{...}', ''] or ['', '{...}\n', '']
        body = raw[1] if len(raw) > 1 else ""
        if body.lower().startswith("json"):
            body = body[4:]
        return body.strip().rstrip("`").strip()
    return raw


def parse_json_response(response, *, default=None):
    """
    Pull text out of an Anthropic response, strip markdown fences, json.loads it.

    Returns `default` if parsing fails (when default is not None), otherwise
    raises ValueError with a truncated raw preview for debugging.
    """
    raw = extract_text(response)
    cleaned = _strip_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError) as e:
        if default is not None:
            return default
        raise ValueError(f"LLM returned invalid JSON: {e}\nRaw: {cleaned[:300]}")
