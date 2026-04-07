"""
Supplier BI Agent — LangGraph Graph
=====================================
Defines the agent state and wires all nodes into a directed graph.

State flows through nodes in sequence:
  Trigger → Discover → Pull → Analyse → Generate → Review → Publish

Each node reads from state and writes back to it.
The human review gate is a LangGraph interrupt — the pipeline
pauses here until approved, rejected, or edited via the control plane.
"""

import uuid
from typing import Optional
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from agent.nodes.discover import discover_node
from agent.nodes.pull import pull_node


# ── Agent state ───────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # ── Input ─────────────────────────────────────────────────────────────────
    report_type:    str
    audience:       str              # "business" or "supplier"
    supplier_id:    Optional[str]    # required for supplier-scoped reports
    date_from:      Optional[str]    # override date range (adhoc reports)
    date_to:        Optional[str]    # override date range (adhoc reports)
    goal:           str              # human-readable report goal

    # ── Discover node output ──────────────────────────────────────────────────
    selected_tables:      Optional[list]   # tables chosen for this report
    table_schemas:        Optional[dict]   # column metadata per table
    discover_reasoning:   Optional[str]    # LLM reasoning trace

    # ── Pull node output ──────────────────────────────────────────────────────
    queries:          Optional[dict]   # {table_name: sql_string}
    query_results:    Optional[dict]   # {table_name: aggregated_data_json}
    row_counts:       Optional[dict]   # {table_name: int}
    pull_validation:  Optional[dict]   # {table_name: {status, warnings}}

    # ── Analyse node output (Phase 3) ─────────────────────────────────────────
    analysis:         Optional[dict]   # structured JSON analysis
    confidence:       Optional[float]  # 0.0 - 1.0
    flags:            Optional[list]   # low confidence warnings, anomalies

    # ── Generate node output (Phase 3) ────────────────────────────────────────
    report_narrative: Optional[str]    # human-readable report text
    report_json:      Optional[dict]   # structured report for Looker Studio

    # ── Review node output (Phase 4) ──────────────────────────────────────────
    approved:         Optional[bool]
    reviewer:         Optional[str]
    review_notes:     Optional[str]
    approved_at:      Optional[str]

    # ── Pipeline metadata ─────────────────────────────────────────────────────
    run_id:           Optional[str]
    errors:           Optional[list]
    current_node:     Optional[str]


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph():
    """
    Build and compile the LangGraph agent graph.

    Phase 2: discover → pull → END (analyse/generate/review/publish are placeholders)
    Phase 3: adds analyse + generate nodes
    Phase 4: adds review interrupt + publish node
    """
    graph = StateGraph(AgentState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("discover", discover_node)
    graph.add_node("pull",     pull_node)

    # Placeholder nodes — implemented in Phase 3 and 4
    graph.add_node("analyse",  _placeholder_node("analyse"))
    graph.add_node("generate", _placeholder_node("generate"))
    graph.add_node("review",   _placeholder_node("review"))
    graph.add_node("publish",  _placeholder_node("publish"))

    # ── Define edges ──────────────────────────────────────────────────────────
    graph.set_entry_point("discover")
    graph.add_edge("discover", "pull")
    graph.add_edge("pull",     "analyse")
    graph.add_edge("analyse",  "generate")
    graph.add_edge("generate", "review")
    graph.add_edge("review",   "publish")
    graph.add_edge("publish",  END)

    # ── Compile ───────────────────────────────────────────────────────────────
    # MemorySaver for development — replaced with persistent checkpointer in Phase 4
    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def _placeholder_node(name: str):
    """Pass-through node for phases not yet implemented."""
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

    Returns:
        Final agent state after all nodes have run
    """
    graph     = build_graph()
    thread_id = thread_id or str(uuid.uuid4())

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
        "run_id":             str(uuid.uuid4()),
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
    print(f"  Run ID:      {initial_state['run_id']}")
    print(f"{'='*60}\n")

    return graph.invoke(initial_state, config=config)
