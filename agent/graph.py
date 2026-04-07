"""
Supplier BI Agent — LangGraph Graph
=====================================
Defines the agent state and wires all nodes into a directed graph.

Phase 3 additions:
  - analyse_node and generate_node now wired in (no longer placeholders)
  - LangSmith tracing enabled via environment variable
  - Confidence-based routing: low confidence reports flagged before review

Phase 2 (complete):
  - discover_node: table selection (template mode skips LLM)
  - pull_node: SQL execution (templates for scheduled, LLM for ad-hoc)
  - Security: input sanitiser, SQL validator, supplier scoping

Phase 4 (planned):
  - review_node: LangGraph human-in-the-loop interrupt
  - publish_node: GCS + BigQuery + Looker Studio
"""

import os
import uuid
from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agent.nodes.discover  import discover_node
from agent.nodes.pull      import pull_node
from agent.nodes.analyse   import analyse_node
from agent.nodes.generate  import generate_node


# ── Agent state ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # Input
    report_type:    str
    audience:       str
    supplier_id:    Optional[str]
    date_from:      Optional[str]
    date_to:        Optional[str]
    goal:           str

    # Discover output
    selected_tables:      Optional[list]
    table_schemas:        Optional[dict]
    discover_reasoning:   Optional[str]

    # Pull output
    queries:          Optional[dict]
    query_results:    Optional[dict]
    row_counts:       Optional[dict]
    pull_validation:  Optional[dict]

    # Analyse output
    analysis:         Optional[dict]
    confidence:       Optional[float]
    flags:            Optional[list]

    # Generate output
    report_narrative: Optional[str]
    report_json:      Optional[dict]

    # Review output (Phase 4)
    approved:         Optional[bool]
    reviewer:         Optional[str]
    review_notes:     Optional[str]
    approved_at:      Optional[str]

    # Pipeline metadata
    run_id:           Optional[str]
    errors:           Optional[list]
    current_node:     Optional[str]


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the LangGraph agent graph.

    Phase 3: discover → pull → analyse → generate → review* → publish* → END
    * review and publish are placeholders until Phase 4
    """
    graph = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("discover", discover_node)
    graph.add_node("pull",     pull_node)
    graph.add_node("analyse",  analyse_node)
    graph.add_node("generate", generate_node)

    # Placeholders — Phase 4
    graph.add_node("review",   _placeholder_node("review"))
    graph.add_node("publish",  _placeholder_node("publish"))

    # ── Edges ─────────────────────────────────────────────────────────────────
    graph.set_entry_point("discover")
    graph.add_edge("discover", "pull")
    graph.add_edge("pull",     "analyse")
    graph.add_edge("analyse",  "generate")
    graph.add_edge("generate", "review")
    graph.add_edge("review",   "publish")
    graph.add_edge("publish",  END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def _placeholder_node(name: str):
    def node(state: AgentState) -> dict:
        print(f"  [{name}] placeholder — not yet implemented")
        return {"current_node": name}
    node.__name__ = name
    return node


# ── Convenience runner ────────────────────────────────────────────────────────

def run_agent(
    report_type: str,
    goal:        str,
    audience:    str  = "business",
    supplier_id: str  = None,
    date_from:   str  = None,
    date_to:     str  = None,
    thread_id:   str  = None,
) -> AgentState:
    """
    Run the agent pipeline end-to-end.

    Args:
        report_type:  Must match a key in agent/config/metadata.yaml
        goal:         Human-readable description of what the report should cover
        audience:     "business" or "supplier"
        supplier_id:  Required for supplier-scoped reports
        date_from:    Optional date override (YYYY-MM-DD) for adhoc reports
        date_to:      Optional date override (YYYY-MM-DD) for adhoc reports
        thread_id:    LangGraph thread ID — auto-generated if not provided
    """
    graph     = build_graph()
    thread_id = thread_id or str(uuid.uuid4())
    run_id    = str(uuid.uuid4())

    initial_state: AgentState = {
        "report_type":        report_type,
        "audience":           audience,
        "supplier_id":        supplier_id,
        "date_from":          date_from,
        "date_to":            date_to,
        "goal":               goal,
        "selected_tables":    None,
        "table_schemas":      None,
        "discover_reasoning": None,
        "queries":            None,
        "query_results":      None,
        "row_counts":         None,
        "pull_validation":    None,
        "analysis":           None,
        "confidence":         None,
        "flags":              None,
        "report_narrative":   None,
        "report_json":        None,
        "approved":           None,
        "reviewer":           None,
        "review_notes":       None,
        "approved_at":        None,
        "run_id":             run_id,
        "errors":             [],
        "current_node":       None,
    }

    config = {"configurable": {"thread_id": thread_id}}

    print(f"\n{'='*60}")
    print(f"Agent run starting")
    print(f"  Report type: {report_type}")
    print(f"  Audience:    {audience}")
    print(f"  Goal:        {goal}")
    if supplier_id:
        print(f"  Supplier:    {supplier_id}")
    print(f"  Run ID:      {run_id}")
    print(f"{'='*60}\n")

    return graph.invoke(initial_state, config=config)
